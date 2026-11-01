"""Runs the whole demo: draft and review notes, drive the CRM, check the figures, build the site."""

import json
import shutil
import sqlite3
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from larkmere import site, world
from larkmere.extract.checks import run_checks, verdict
from larkmere.extract.offline import OfflineExtractor
from larkmere.extract.schema import Extracted
from larkmere.notes.drafting import RuleDrafter, inject_invented_figure
from larkmere.notes.review import NotApproved, ReviewQueue
from larkmere.notes.server import serve
from larkmere.notes.transcripts import TRANSCRIPTS, parse
from larkmere.revenue import metrics

FAULT_ACCOUNT = "LM-1006"
FAULT_NOTE = "T-304"


RECORDINGS = Path(__file__).parent / "recordings"


def default_mode():
    return "replay" if any(RECORDINGS.glob("*.json")) else "offline"


def say(message):
    print(message, flush=True)


def brains(mode, recordings):
    """Which extractor and drafter to use. Only 'live' and 'replay' involve a model's output."""
    if mode == "offline":
        return OfflineExtractor(), RuleDrafter()
    from larkmere.extract.model import ModelExtractor, ReplayExtractor
    from larkmere.notes.drafting import ModelDrafter, ReplayDrafter
    if mode == "replay":
        return ReplayExtractor(recordings), ReplayDrafter(recordings)
    if mode == "live":
        return ModelExtractor(), ModelDrafter()
    if mode == "record":
        return ModelExtractor(record_to=recordings), ModelDrafter(record_to=recordings)
    raise ValueError(mode)


def make_drafts(drafter):
    drafts = []
    for draft_id, (account_id, meeting, raw) in TRANSCRIPTS.items():
        draft = drafter.draft(draft_id, account_id, meeting, parse(raw))
        if draft_id == FAULT_NOTE:
            inject_invented_figure(draft)
        drafts.append(draft)
    return drafts


def auto_review(queue, reviewer):
    """Non-interactive decisions for CI, under an explicit name. Anything flagged gets rejected."""
    for draft_id in queue.drafts:
        try:
            queue.approve(draft_id, reviewer, queue.drafts[draft_id].text())
        except NotApproved as refused:
            queue.reject(draft_id, reviewer, reason=str(refused))


def human_review(queue, open_browser=True):
    server, url = serve(queue)
    say(f"\nReview the drafted notes: {url}\nWaiting for a decision on each one...")
    if open_browser:
        webbrowser.open(url)
    while not queue.finished():
        time.sleep(0.5)
    server.shutdown()


class _Crm:
    def __init__(self, db, environment="demo", topmost=True):
        arguments = [sys.executable, "-m", "larkmere.crm.app", "--db", str(db), "--environment", environment]
        if topmost:
            arguments.append("--topmost")
        self.process = subprocess.Popen(arguments)

    def close(self):
        self.process.terminate()
        self.process.wait(timeout=10)


def _extract_all(robot, extractor, out):
    from larkmere.robot import flows
    results = []
    pages_dir = out / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for account_id, name, _, _, _, purpose in world.DEMO_ACCOUNTS:
        say(f"  Reading {account_id} {name}")
        pages = flows.collect_statement(robot, account_id, world.LAST_MONTH)
        extracted = extractor.extract(account_id, world.LAST_MONTH, pages)
        results.append(_result(account_id, name, purpose, extracted, None))
        if account_id == FAULT_ACCOUNT:
            damaged = Extracted.from_json(extracted.to_json())
            damaged.lines.pop()
            results.append(_result(account_id, name, purpose, damaged,
                                   "extractor output damaged on purpose: one line item removed"))
    return results


def _result(account_id, name, purpose, extracted, fault):
    checks = run_checks(extracted, account_id, world.LAST_MONTH)
    state = verdict(checks)
    say(f"    {state}" + (f" ({fault})" if fault else ""))
    return {"account_id": account_id, "name": name, "purpose": purpose, "fault_injected": fault, "verdict": state,
            "extracted": extracted.to_json(), "checks": [vars(c) for c in checks]}


def _safety_scenario(robot, db, out):
    """Put a look-alike production window over the app. The robot must stop and send nothing."""
    from larkmere.robot import flows
    decoy_db = out / "lookalike.sqlite"
    shutil.copy(db, decoy_db)
    decoy = _Crm(decoy_db, environment="production")
    time.sleep(2.5)
    inputs_before = robot.desktop.inputs
    stop = robot.guarded(lambda: flows.open_account(robot, "LM-1002"))
    outcome = {
        "scenario": "a look-alike window from the production environment appeared on top",
        "stopped": stop is not None,
        "reason": stop.reason if stop else None,
        "inputs_sent_after_it_appeared": robot.desktop.inputs - inputs_before,
    }
    decoy.close()
    time.sleep(1)
    robot.window = None
    say(f"  Stopped: {outcome['stopped']}. Reason: {outcome['reason']}. "
        f"Input sent: {outcome['inputs_sent_after_it_appeared']}")
    return outcome


def _write_notes(robot, queue, saved):
    from larkmere.robot import flows
    for note in queue.approved:
        say(f"  Saving the note approved by {note.reviewer} on {note.account_id}")
        score = flows.write_note(robot, note)
        saved.append({"draft_id": note.draft_id, "account_id": note.account_id, "reviewer": note.reviewer,
                      "readback": round(score, 3)})


def _robot_phase(db, out, extractor, queue):
    from larkmere.robot.desktop import RealDesktop
    from larkmere.robot.navigator import Robot, Trace
    trace = Trace(out / "trace")
    crm = _Crm(db)
    time.sleep(2.5)
    results, safety, saved = [], None, []
    robot = Robot(RealDesktop(), trace)
    stop = None
    try:
        def work():
            nonlocal results, safety
            say("Reading statements through the screen")
            results = _extract_all(robot, extractor, out)
            say("Safety scenario: a look-alike window")
            safety = _safety_scenario(robot, db, out)
            say("Typing approved notes into the CRM")
            _write_notes(robot, queue, saved)
        stop = robot.guarded(work)
    finally:
        crm.close()
    trace.save()
    if stop:
        say(f"\nThe robot stopped safely: {stop.reason}")
    return {"results": results, "safety": safety, "notes_saved": saved, "stopped_early": stop.reason if stop else None}


def run(out, mode="offline", reviewer=None, use_robot=True, assume_yes=False, open_browser=True,
        recordings=None):
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    db = out / "larkmere.sqlite"
    say(f"Generating the fictional company into {db}")
    world.build(db)
    recordings = recordings or RECORDINGS
    extractor, drafter = brains(mode, recordings)

    say("Drafting notes from meeting transcripts")
    queue = ReviewQueue(make_drafts(drafter), out / "audit-log.jsonl")
    if reviewer:
        auto_review(queue, reviewer)
    else:
        human_review(queue, open_browser)

    robot_result = {"results": [], "safety": None, "notes_saved": [], "stopped_early": "robot phase was skipped"}
    if use_robot:
        if not assume_yes:
            say("\nThe robot will now use your real mouse and keyboard for a few minutes, in a window it opens.")
            say("Don't touch them while it runs. To stop it at any time, push the mouse into a screen corner.")
            input("Press Enter to start, or Ctrl+C to skip: ")
        robot_result = _robot_phase(db, out, extractor, queue)

    # An independent look at the database, which the robot itself can't see.
    with sqlite3.connect(db) as conn:
        in_crm = conn.execute("SELECT customer_id, body FROM notes ORDER BY id").fetchall()
    results = {
        "mode": mode,
        "extractor": extractor.source,
        "drafter": drafter.source,
        "reviews": queue.view(),
        **robot_result,
        "notes_in_crm": [{"account_id": a, "body": b} for a, b in in_crm],
    }
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")

    say("Computing revenue metrics and building the site")
    index = site.build(out, metrics.compute(db), results)
    say(f"\nDone. Open {index}")
    if open_browser:
        webbrowser.open(index.as_uri())
    return results
