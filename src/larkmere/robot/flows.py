"""The two jobs the robot does in the CRM: collect statement pages, and save an approved note."""

import re
import time

from larkmere.robot import ocr
from larkmere.robot.navigator import Frame, SafetyStop, similarity

READBACK_THRESHOLD = 0.85


def open_account(robot, account_id):
    robot.click_entry_right_of("Find account", on="Accounts")
    robot.desktop.select_all_and_delete()
    robot.type_text(account_id, on="Accounts")
    robot.click("Search", on="Accounts", then="Accounts")
    # Wait for the list to filter, so we click the row and not a stale neighbour.
    deadline = time.monotonic() + robot.timeout
    while True:
        frame = robot.expect("Accounts")
        search = frame.find("Search")
        below = search.y1 if search else frame.image.height
        rows = [t for t in frame.texts if t.y0 > below and re.match(r"LM[-~ ]?\d{4}", t.text)]
        if len(rows) == 1 and account_id.replace("-", "") in rows[0].text.replace("-", "").replace(" ", ""):
            break
        if time.monotonic() > deadline:
            raise SafetyStop(f"search for {account_id} did not narrow to one row", [t.text for t in frame.texts])
        time.sleep(robot.poll)
    robot.desktop.click(*frame.to_screen(*rows[0].centre))
    robot.trace.add(f"open account {account_id}", frame, ocr_seconds=robot.ocr_seconds)
    return _confirm_account(robot, account_id, "Account")


def _confirm_account(robot, account_id, screen):
    """The header names the screen. This checks it is the right account's screen."""
    frame = robot.expect(screen)
    if not any(account_id in t.text.replace(" ", "") or account_id in t.text for t in frame.texts):
        raise SafetyStop(f"on '{screen}' but {account_id} is not shown", [t.text for t in frame.texts])
    return frame


def collect_statement(robot, account_id, month):
    """Page through one statement. Returns the window image of each page, in order."""
    open_account(robot, account_id)
    robot.click("Statements", on="Account", then="Statements")
    robot.click(month, on="Statements", then="Statement")
    frame = _confirm_account(robot, account_id, "Statement")
    pages = []
    while True:
        match = next((re.search(r"Page\s*(\d+)\s*of\s*(\d+)", t.text) for t in frame.texts
                      if re.search(r"Page\s*\d+\s*of\s*\d+", t.text)), None)
        if match is None:
            raise SafetyStop("cannot read the page count on this statement", [t.text for t in frame.texts])
        page, total = int(match[1]), int(match[2])
        if page != len(pages) + 1:
            raise SafetyStop(f"expected page {len(pages) + 1}, screen says page {page}", [t.text for t in frame.texts])
        pages.append(frame.image.copy())
        robot.trace.add(f"read {account_id} {month} page {page} of {total}", frame, ocr_seconds=robot.ocr_seconds)
        if page == total:
            break
        robot.desktop.click(*frame.to_screen(*frame.find("Next page").centre))
        frame = _wait_for_page(robot, account_id, page + 1, total)
    robot.click("Accounts", on="Statement", then="Accounts")
    return pages


def _wait_for_page(robot, account_id, page, total):
    deadline = time.monotonic() + robot.timeout
    while True:
        frame = _confirm_account(robot, account_id, "Statement")
        if any(re.search(rf"Page\s*{page}\s*of\s*{total}", t.text) for t in frame.texts):
            return frame
        if time.monotonic() > deadline:
            raise SafetyStop(f"page {page} of {total} did not appear", [t.text for t in frame.texts])
        time.sleep(robot.poll)


def write_note(robot, approved):
    """Type an approved note, read it back from the screen, and save only if it matches.

    Takes an ApprovedNote rather than a string: an unapproved draft cannot reach this function
    without someone constructing that object, which the review queue only does on a person's say-so.
    """
    approved.verify()
    open_account(robot, approved.account_id)
    robot.click("Notes", on="Account", then="Notes")
    frame = _confirm_account(robot, approved.account_id, "Notes")
    before = _saved_count(frame)
    robot.click_below("New note", on="Notes", offset=40)
    robot.type_text(approved.text, on="Notes")
    # Stop before saving if what landed in the box isn't what was approved.
    frame = robot.expect("Notes")
    typed, score = _best_readback(frame, approved.text)
    robot.trace.add(f"typed note for {approved.account_id}, read back {score:.0%} match", frame,
                    ocr_seconds=robot.ocr_seconds)
    if score < READBACK_THRESHOLD:
        raise SafetyStop(f"read-back matched only {score:.0%} of the approved note, not saving", [typed[:200]])
    robot.click("Save note", on="Notes", then="Notes")
    frame = robot.expect("Notes")
    after = _saved_count(frame)
    if after != before + 1:
        raise SafetyStop(f"saved-note count went from {before} to {after}, expected {before + 1}")
    robot.trace.add(f"saved note for {approved.account_id}", frame, ocr_seconds=robot.ocr_seconds)
    robot.click("Accounts", on="Notes", then="Accounts")
    return score


def _saved_count(frame):
    for t in frame.texts:
        match = re.search(r"Saved\s*notes\W*(\d+)", t.text, re.IGNORECASE)
        if match:
            return int(match[1])
    raise SafetyStop("cannot read the saved-note count", [t.text for t in frame.texts])


def _box_text(frame):
    new_note = frame.find("New note")
    save = frame.find("Save note")
    if new_note is None or save is None:
        raise SafetyStop("cannot find the note box on screen", [t.text for t in frame.texts])
    inside = [t for t in frame.texts if new_note.y1 <= t.y0 and t.y1 <= save.y0]
    return " ".join(t.text for t in inside)


def _best_readback(frame, approved_text):
    """Compare the box with the approved text, reading again if OCR seems to have skipped a line.

    Safe to retry: text that differs from what was approved can't match under any reading.
    """
    typed = _box_text(frame)
    score = similarity(typed, approved_text)
    for settings in ocr.READINGS[1:]:
        if score >= READBACK_THRESHOLD:
            break
        again = Frame(frame.image, frame.origin, ocr.merge_adjacent(ocr.read(frame.image, **settings)), frame.screen)
        attempt = _box_text(again)
        if (better := similarity(attempt, approved_text)) > score:
            typed, score = attempt, better
    return typed, score
