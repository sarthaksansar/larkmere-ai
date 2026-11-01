"""Turn transcripts exported from meeting tools into one plain form: "[mm:ss] Name: words".

These read files and don't connect to either product's service. Both are built from public
descriptions of the formats and sample files written for this project, not from real exports,
so expect to adjust them on first contact with the real thing.
"""

import html
import re


class ImportFailed(Exception):
    pass


TEAMS_CUE_TIME = re.compile(r"^(\d\d):(\d\d):(\d\d)[.,]\d+\s+-->")
TEAMS_VOICE = re.compile(r"<v\s+([^>]+)>(.*?)(?:</v>|$)", re.DOTALL)
GRANOLA_LINE = re.compile(r"^(?:\[(\d\d:\d\d)\]\s*)?(Me|Them|Speaker [A-Z0-9]+|[^:\[\]]{1,60}):\s+(.+)$")


def from_teams_vtt(text):
    """A Microsoft Teams .vtt download: WebVTT cues with the speaker in a voice tag."""
    if not text.lstrip("\ufeff \r\n").startswith("WEBVTT"):
        raise ImportFailed("not a WebVTT file: the WEBVTT header is missing")
    lines = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        rows = block.strip().splitlines()
        at = next((i for i, row in enumerate(rows) if TEAMS_CUE_TIME.match(row)), None)
        if at is None:
            continue
        hours, minutes, seconds = TEAMS_CUE_TIME.match(rows[at]).groups()
        voice = TEAMS_VOICE.search("\n".join(rows[at + 1:]))
        if voice:
            words = " ".join(html.unescape(voice[2]).split())
            lines.append(f"[{int(hours) * 60 + int(minutes):02d}:{seconds}] {voice[1].strip()}: {words}")
    if not lines:
        raise ImportFailed("no cues with a speaker were found")
    return "\n".join(lines)


def from_granola_text(text, speakers=None):
    """Transcript text from Granola, where speakers are often just "Me" and "Them".

    Those labels only say whose microphone it was, so `speakers` maps them to real names. A label
    with no mapping is kept as it is, and the grounding checks will treat it as an ordinary name.
    """
    speakers = speakers or {}
    lines = []
    for row in text.strip().splitlines():
        match = GRANOLA_LINE.match(row.strip())
        if match:
            lines.append(f"[{match[1] or '--:--'}] {speakers.get(match[2], match[2])}: {match[3].strip()}")
    if not lines:
        raise ImportFailed("no speaker lines were found")
    return "\n".join(lines)
