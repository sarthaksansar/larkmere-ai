"""Transcript importers. Sample files are written for this project, not real exports."""

import pytest

from larkmere.notes.importers import ImportFailed, from_granola_text, from_teams_vtt
from larkmere.notes.transcripts import GRANOLA, SOURCES, TEAMS, TRANSCRIPTS, parse

VTT = """WEBVTT

1
00:01:05.120 --> 00:01:09.000
<v Ada Thornquist>Usage was about
$25,000 &amp; rising.</v>

NOTE not speech

01:02:03.000 --> 01:02:04.000
<v Nell Harbottle>Understood.</v>

00:00:01.000 --> 00:00:02.000
No speaker here.
"""


def test_teams_vtt_gives_speakers_times_and_clean_text():
    lines = parse(from_teams_vtt(VTT))
    assert [(u.time, u.speaker, u.text) for u in lines] == [
        ("01:05", "Ada Thornquist", "Usage was about $25,000 & rising."),
        ("62:03", "Nell Harbottle", "Understood."),
    ]


def test_teams_vtt_copes_with_a_byte_order_mark_and_windows_line_endings():
    lines = parse(from_teams_vtt("﻿" + VTT.replace("\n", "\r\n")))
    assert len(lines) == 2


@pytest.mark.parametrize("text", ["Me: hello", "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nno voice tag"])
def test_teams_import_refuses_what_it_cannot_read(text):
    with pytest.raises(ImportFailed):
        from_teams_vtt(text)


def test_granola_labels_become_names():
    lines = parse(from_granola_text("Me: Hello.\nThem: Hi there.", {"Me": "Ada Thornquist", "Them": "Nell Harbottle"}))
    assert [(u.speaker, u.text) for u in lines] == [("Ada Thornquist", "Hello."), ("Nell Harbottle", "Hi there.")]
    assert lines[0].time == "--:--"


def test_granola_keeps_a_label_it_has_no_name_for():
    lines = parse(from_granola_text("Speaker B: Hello.\n[01:05] Them: Hi."))
    assert (lines[0].speaker, lines[1].speaker, lines[1].time) == ("Speaker B", "Them", "01:05")


def test_granola_import_refuses_text_with_no_speakers():
    with pytest.raises(ImportFailed):
        from_granola_text("just some notes\nwith no speakers")


def test_every_sample_came_through_an_importer():
    assert set(SOURCES) == set(TRANSCRIPTS)
    assert set(SOURCES.values()) == {TEAMS, GRANOLA}
    for _, _, raw in TRANSCRIPTS.values():
        lines = parse(raw)
        assert lines and all(u.speaker not in ("Me", "Them") for u in lines)
