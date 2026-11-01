"""The model-backed paths, against a fake client. These prove the request shape and the handling of answers,
not how well a real model does. The replay tests prove that recorded answers get no special treatment."""

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from PIL import Image

from larkmere import ai
from larkmere.extract.checks import run_checks, verdict
from larkmere.extract.model import ModelExtractor, ReplayExtractor
from larkmere.notes.drafting import ModelDrafter, ReplayDrafter
from larkmere.notes.grounding import check_draft
from larkmere.notes.transcripts import TRANSCRIPTS, parse

GOOD = {"account_id": "LM-1", "period": "2026-09", "pages_total": 1, "line_count_reported": 1,
        "lines": [{"item": "Wren input", "tokens_m": "100.00", "rate": "0.40", "amount": "40.00"}],
        "usage_total": "40.00", "monthly_commit": "0.00", "overage": "40.00", "unused_commit": "0.00",
        "total_due": "40.00"}


class FakeClient:
    def __init__(self, text=None, stop_reason="end_turn"):
        self.calls = []
        self.text = text
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        content = [SimpleNamespace(type="text", text=self.text)] if self.text else []
        return SimpleNamespace(stop_reason=self.stop_reason, content=content)


def image():
    return Image.new("RGB", (20, 10), "white")


def test_extractor_sends_images_and_a_schema_and_gets_checked():
    client = FakeClient(json.dumps(GOOD))
    extracted = ModelExtractor(client=client).extract("LM-1", "2026-09", [image()])
    request = client.calls[0]
    assert request["model"] == ai.DEFAULT_MODEL
    assert request["messages"][0]["content"][0]["type"] == "image"
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in request
    assert extracted.total_due == Decimal("40.00")
    assert verdict(run_checks(extracted, "LM-1", "2026-09")) == "verified"


def test_a_confident_wrong_answer_from_the_model_is_still_quarantined():
    wrong = {**GOOD, "usage_total": "400.00", "total_due": "400.00", "overage": "400.00"}
    extracted = ModelExtractor(client=FakeClient(json.dumps(wrong))).extract("LM-1", "2026-09", [image()])
    assert verdict(run_checks(extracted, "LM-1", "2026-09")) == "quarantined"


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_a_refusal_or_cutoff_raises_rather_than_guesses(stop_reason):
    with pytest.raises(ai.ModelError):
        ai.call_json("s", "c", {}, FakeClient("{}", stop_reason))


def test_model_name_can_be_overridden(monkeypatch):
    monkeypatch.setenv("LARKMERE_MODEL", "some-other-model")
    assert ai.model_name() == "some-other-model"


def test_drafter_treats_transcript_as_data_and_its_output_is_checked():
    account, meeting, raw = TRANSCRIPTS["T-303"]
    claims = {"claims": [{"section": "Decisions", "text": "Larkmere approved a 90% discount.", "cites": [6]},
                         {"section": "Risks", "text": "Quote invented: $5,000.", "cites": [1]}]}
    client = FakeClient(json.dumps(claims))
    draft = ModelDrafter(client=client).draft("T-303", account, meeting, parse(raw))
    assert "untrusted data" in client.calls[0]["system"]
    assert "ignore previous instructions" in client.calls[0]["messages"][0]["content"]
    flags = check_draft(draft, parse(raw))
    assert any("$5,000" in f for f in flags)


def test_recording_then_replaying_gives_the_same_result(tmp_path):
    recorder = ModelExtractor(client=FakeClient(json.dumps(GOOD)), record_to=tmp_path)
    original = recorder.extract("LM-1", "2026-09", [image()])
    replayed = ReplayExtractor(tmp_path).extract("LM-1", "2026-09", [image()])
    assert replayed.lines == original.lines and replayed.total_due == original.total_due
    assert "recorded" in replayed.source and ai.DEFAULT_MODEL in replayed.source


def test_drafter_recording_round_trip(tmp_path):
    account, meeting, raw = TRANSCRIPTS["T-301"]
    claims = {"claims": [{"section": "Summary", "text": "Commit review.", "cites": [1]}]}
    ModelDrafter(client=FakeClient(json.dumps(claims)), record_to=tmp_path).draft("T-301", account, meeting, parse(raw))
    replayed = ReplayDrafter(tmp_path).draft("T-301", account, meeting, parse(raw))
    assert replayed.text() == "Summary: Commit review."
