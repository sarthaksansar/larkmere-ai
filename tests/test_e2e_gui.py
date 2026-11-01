"""The whole demo against the real Tk window with real input.

Takes over the mouse and keyboard, so it only runs where that's safe: on a virtual display
in CI, or when you ask for it with LARKMERE_GUI_TESTS=1.
"""

import json
import os
import sqlite3
import sys

import pytest

from larkmere import pipeline, world

pytestmark = pytest.mark.gui

virtual_display = sys.platform.startswith("linux") and os.environ.get("DISPLAY")
if not (virtual_display or os.environ.get("LARKMERE_GUI_TESTS") == "1"):
    pytest.skip("needs a display that's safe to take over", allow_module_level=True)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    results = pipeline.run(out, reviewer="Test Reviewer", assume_yes=True, open_browser=False)
    return out, results


def test_finished_without_stopping(run):
    _, results = run
    assert results["stopped_early"] is None


def test_clean_statements_verify_and_planted_ones_are_quarantined(run):
    _, results = run
    verdicts = {(r["account_id"], bool(r["fault_injected"])): r["verdict"] for r in results["results"]}
    assert verdicts == {
        ("LM-1001", False): "verified", ("LM-1002", False): "verified", ("LM-1003", False): "verified",
        ("LM-1004", False): "quarantined", ("LM-1005", False): "quarantined", ("LM-1006", False): "verified",
        ("LM-1006", True): "quarantined",
    }


def test_each_planted_fault_fails_for_the_right_reason(run):
    _, results = run
    by_account = {r["account_id"]: r for r in results["results"] if not r["fault_injected"]}
    failed = lambda a: {c["name"] for c in by_account[a]["checks"] if not c["ok"]}
    assert "lines add up to usage total" in failed("LM-1004")
    assert "Heron output: tokens x rate = amount" in failed("LM-1005")


def test_verified_figures_match_the_source_exactly(run):
    out, results = run
    with sqlite3.connect(out / "larkmere.sqlite") as db:
        for r in results["results"]:
            if r["verdict"] != "verified":
                continue
            due, usage = db.execute("SELECT total_due_cents, usage_cents FROM statements WHERE customer_id=? AND month=?",
                                    (r["account_id"], world.LAST_MONTH)).fetchone()
            assert round(float(r["extracted"]["total_due"]) * 100) == due
            assert round(float(r["extracted"]["usage_total"]) * 100) == usage


def test_the_lookalike_stopped_the_robot_with_no_input_sent(run):
    _, results = run
    safety = results["safety"]
    assert safety["stopped"] is True
    assert "production" in safety["reason"]
    assert safety["inputs_sent_after_it_appeared"] == 0


def test_only_approved_notes_reached_the_crm(run):
    _, results = run
    approved = {d["account_id"]: d["decision"]["final_text"] for d in results["reviews"]
                if d["decision"]["decision"] == "approved"}
    saved = {n["account_id"]: n["body"] for n in results["notes_in_crm"]}
    assert set(saved) == set(approved) == {"LM-1002", "LM-1003", "LM-1006"}
    for account, text in approved.items():
        assert saved[account] == text


def test_trace_and_site_were_written(run):
    out, _ = run
    trace = json.loads((out / "trace" / "trace.json").read_text(encoding="utf-8"))
    assert len(trace["steps"]) > 30 and trace["incidents"]
    index = (out / "site" / "index.html").read_text(encoding="utf-8")
    assert "__DATA__" not in index and "Larkmere" in index
    assert (out / "site" / "frames").is_dir()
