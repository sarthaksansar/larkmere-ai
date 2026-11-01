"""Thin wrapper over a local OCR engine (RapidOCR on ONNX Runtime). Nothing leaves the machine here."""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image

# UI text reads better enlarged. At 2x this engine still confused commas and decimal points
# in amounts; at 3x it did not. Coordinates are mapped back to the original image.
UPSCALE = 3
# How far detected text boxes are grown. The engine's own default.
DEFAULT_UNCLIP = 1.6
# The text detector sometimes skips a whole cell or line, and which one depends on these settings.
READINGS = ({"upscale": 3, "unclip_ratio": 1.6}, {"upscale": 3, "unclip_ratio": 2.0},
            {"upscale": 2, "unclip_ratio": 1.6}, {"upscale": 4, "unclip_ratio": 2.0})


@dataclass(frozen=True)
class Text:
    text: str
    x0: int
    y0: int
    x1: int
    y1: int
    confidence: float

    @property
    def centre(self):
        return (self.x0 + self.x1) // 2, (self.y0 + self.y1) // 2

    def shifted(self, dx, dy):
        return Text(self.text, self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy, self.confidence)


@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def read(image, upscale=None, unclip_ratio=DEFAULT_UNCLIP):
    """Every piece of text in the image, top to bottom then left to right."""
    upscale = upscale or UPSCALE
    big = image.convert("RGB").resize((image.width * upscale, image.height * upscale), Image.LANCZOS)
    # Always passed explicitly: the engine remembers per-call settings from earlier calls.
    result, _ = _engine()(np.array(big), unclip_ratio=unclip_ratio)
    texts = []
    for box, text, confidence in result or []:
        xs = [p[0] / upscale for p in box]
        ys = [p[1] / upscale for p in box]
        texts.append(Text(text.strip(), int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)), float(confidence)))
    return sorted(texts, key=lambda t: (t.y0, t.x0))


def merge_adjacent(texts, gap_ratio=0.7):
    """Rejoin words the engine split. Pieces on a row closer than a fraction of their height are one phrase."""
    merged = []
    for row in rows(texts):
        current = row[0]
        for piece in row[1:]:
            if piece.x0 - current.x1 < (current.y1 - current.y0) * gap_ratio:
                current = Text(f"{current.text} {piece.text}", current.x0, min(current.y0, piece.y0), piece.x1,
                               max(current.y1, piece.y1), min(current.confidence, piece.confidence))
            else:
                merged.append(current)
                current = piece
        merged.append(current)
    return sorted(merged, key=lambda t: (t.y0, t.x0))


def rows(texts, tolerance=8):
    """Group text pieces into visual rows, each sorted left to right."""
    grouped = []
    for t in sorted(texts, key=lambda t: (t.y0 + t.y1) / 2):
        middle = (t.y0 + t.y1) / 2
        if grouped and abs(middle - grouped[-1]["middle"]) <= tolerance:
            grouped[-1]["items"].append(t)
        else:
            grouped.append({"middle": middle, "items": [t]})
    return [sorted(g["items"], key=lambda t: t.x0) for g in grouped]
