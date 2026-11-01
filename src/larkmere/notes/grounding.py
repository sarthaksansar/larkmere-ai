"""Rule-based checks that a note says only what the transcript says. No AI.

These catch invented figures, dates and names, broken citations and text that won't type
cleanly. They do not catch a claim that uses real words to say something false, which is why
a person still reads every note.
"""

import re

from larkmere.notes.drafting import INJECTION, MAX_NOTE_CHARS, SECTIONS

FIGURE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")
DATE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
                  r"(?:\s+\d{1,2})?\b")
NAME = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")


def _figures(text):
    return {f.rstrip(".,") for f in FIGURE.findall(text)}


def check_text(text, utterances, meeting=""):
    """Flags for final note text, whoever wrote it. Empty means no objection."""
    source = meeting + " " + " ".join(f"{u.speaker} {u.text}" for u in utterances)
    flags = []
    if not text.strip():
        flags.append("the note is empty")
    if len(text) > MAX_NOTE_CHARS:
        flags.append(f"the note is {len(text)} characters, over the {MAX_NOTE_CHARS} the CRM box holds")
    if not text.isascii():
        flags.append("the note has non-ASCII characters, which can't be typed reliably")
    for figure in sorted(_figures(text) - _figures(source)):
        flags.append(f"figure {figure} is not in the transcript")
    for date in sorted(set(DATE.findall(text)) - set(DATE.findall(source))):
        flags.append(f"date '{date}' is not in the transcript")
    for name in sorted(set(NAME.findall(text))):
        if name not in source and not any(name.startswith(s) for s in SECTIONS):
            flags.append(f"name '{name}' is not in the transcript")
    if INJECTION.search(text):
        flags.append("the note repeats an instruction aimed at an AI, not meeting content")
    return flags


def check_draft(draft, utterances):
    """Everything in check_text, plus per-claim citation checks that only a draft can have."""
    flags = check_text(draft.text(), utterances, draft.meeting)
    numbers = {u.number: u for u in utterances}
    for claim in draft.claims:
        if claim.section == "Summary" and not claim.cites:
            continue
        missing = [n for n in claim.cites if n not in numbers]
        if missing or not claim.cites:
            flags.append(f"'{claim.text[:50]}' cites lines that don't exist: {missing or 'none given'}")
            continue
        cited = " ".join(numbers[n].text for n in claim.cites)
        for figure in sorted(_figures(claim.text) - _figures(cited)):
            flags.append(f"figure {figure} is not in the lines cited by '{claim.text[:50]}'")
    # The same complaint can arrive by both routes.
    return list(dict.fromkeys(flags))


def injection_lines(utterances):
    """Transcript lines trying to instruct an AI. Shown to the reviewer as a warning."""
    return [u.number for u in utterances if INJECTION.search(u.text)]
