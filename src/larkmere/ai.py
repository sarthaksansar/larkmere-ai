"""The one place a real model is called. Needs `pip install .[live]` and an API key.

Only the `live` and `record` modes come through here. Everything else in the project,
including the default demo, runs without it.
"""

import base64
import io
import json
import os
from datetime import date
from pathlib import Path

DEFAULT_MODEL = "claude-opus-5-5"


class ModelError(Exception):
    pass


def model_name():
    return os.environ.get("LARKMERE_MODEL", DEFAULT_MODEL)


def recording(response):
    """What gets saved from a real call, so a replay can say exactly where its answer came from."""
    return {"model": model_name(), "recorded_on": date.today().isoformat(), "response": response}


def recorded_label(record):
    return f"AI: recorded response from {record['model']}, {record['recorded_on']}"


def recordings_label(directory):
    """The label for a folder of recordings, for showing before any one of them is read."""
    first = next(iter(sorted(Path(directory).glob("*.json"))), None)
    return recorded_label(json.loads(first.read_text(encoding="utf-8"))) if first else "AI: recorded response"


def image_block(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": base64.standard_b64encode(buffer.getvalue()).decode("ascii")}}


def call_json(system, content, schema, client=None):
    """One request, one JSON answer matching schema. A refusal or cut-off raises rather than guesses."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=model_name(),
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason != "end_turn":
        raise ModelError(f"model stopped early: {response.stop_reason}")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ModelError("model returned no text")
    return json.loads(text)
