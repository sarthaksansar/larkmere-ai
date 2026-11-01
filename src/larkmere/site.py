"""Builds a static site from one demo run. No server and no generated files are committed."""

import json
import shutil
from importlib import resources
from pathlib import Path

from PIL import Image


def _copy_frames(out, site_dir):
    trace_dir = out / "trace"
    frames = site_dir / "frames"
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True)
    trace = {"steps": [], "incidents": []}
    if (trace_dir / "trace.json").exists():
        trace = json.loads((trace_dir / "trace.json").read_text(encoding="utf-8"))
        for png in trace_dir.glob("*.png"):
            with Image.open(png) as image:
                image.convert("P", palette=Image.ADAPTIVE, colors=64).save(frames / png.name, optimize=True)
    return trace


def build(out, metrics, results):
    out = Path(out)
    site_dir = out / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    data = {"metrics": metrics, "results": results, "trace": _copy_frames(out, site_dir)}
    template = resources.files("larkmere.web").joinpath("site.html").read_text(encoding="utf-8")
    payload = json.dumps(data, separators=(",", ":"), default=str).replace("</", "<\\/")
    index = site_dir / "index.html"
    index.write_text(template.replace("__DATA__", payload), encoding="utf-8")
    return index.resolve()
