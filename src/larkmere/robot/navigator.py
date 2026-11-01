"""Reads the screen before and after every action, and stops rather than guessing.

The rule that matters: no click or keystroke goes out unless this frame shows the exact
header we expect and the demo environment footer, in the right places. There is no retry
that keeps clicking, and no fallback to remembered coordinates.
"""

import difflib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import ImageDraw

from larkmere.robot import ocr
from larkmere.robot.desktop import EmergencyStop

APP = "Larkmere Desk"
FOOTER = "Environment: demo data"
OS_TITLE = "Larkmere Desk"
# Characters OCR commonly confuses, mapped to one form before comparing.
CONFUSABLE = str.maketrans({"0": "o", "1": "l", "i": "l"})
MIN_CONFIDENCE = 0.6
FUZZY_LABEL_MATCH = 0.85


class SafetyStop(Exception):
    """The robot has stopped and sent no further input. `reason` says why."""

    def __init__(self, reason, saw=None):
        super().__init__(reason)
        self.reason = reason
        self.saw = saw or []


def normalise(text):
    return re.sub(r"[^a-z0-9]", "", text.lower()).translate(CONFUSABLE)


def same_text(a, b):
    return normalise(a) == normalise(b)


def similarity(a, b):
    return difflib.SequenceMatcher(None, normalise(a), normalise(b)).ratio()


@dataclass
class Frame:
    image: object  # cropped to the app window, never the whole screen
    origin: tuple  # top-left of the crop on screen
    texts: list  # coordinates relative to the crop
    screen: str  # the part of the header after the app name

    def find(self, label):
        """The single piece of text matching label. Ambiguity is treated as not found."""
        exact = [t for t in self.texts if same_text(t.text, label)]
        if len(exact) == 1:
            return exact[0]
        if exact:
            return None
        contains = [t for t in self.texts if normalise(label) in normalise(t.text)]
        if contains:
            return contains[0] if len(contains) == 1 else None
        # OCR occasionally garbles a word. Allow that for words only: a misread digit could mean
        # the wrong month or the wrong account.
        close = [t for t in self.texts if similarity(t.text, label) >= FUZZY_LABEL_MATCH
                 and re.sub(r"\D", "", t.text) == re.sub(r"\D", "", label)]
        return close[0] if len(close) == 1 else None

    def to_screen(self, x, y):
        return x + self.origin[0], y + self.origin[1]

    def annotated(self):
        """The frame with a box around everything OCR read, to show what the robot saw."""
        image = self.image.convert("RGB")
        draw = ImageDraw.Draw(image)
        for t in self.texts:
            draw.rectangle((t.x0 - 2, t.y0 - 2, t.x1 + 2, t.y1 + 2), outline="#2a78d6")
        return image


@dataclass
class TraceStep:
    n: int
    action: str
    screen: str
    outcome: str
    frame: str | None = None
    ocr_seconds: float = 0.0


@dataclass
class Trace:
    directory: Path
    steps: list = field(default_factory=list)
    incidents: list = field(default_factory=list)

    def add(self, action, frame, outcome="ok", ocr_seconds=0.0, save_frame=True):
        n = len(self.steps) + 1
        name = None
        if frame is not None and save_frame:
            self.directory.mkdir(parents=True, exist_ok=True)
            name = f"step-{n:03d}.png"
            frame.annotated().save(self.directory / name)
        self.steps.append(TraceStep(n, action, frame.screen if frame else "?", outcome, name, round(ocr_seconds, 2)))

    def incident(self, stop, frame=None):
        self.directory.mkdir(parents=True, exist_ok=True)
        name = None
        if frame is not None:
            name = f"incident-{len(self.incidents) + 1}.png"
            frame.annotated().save(self.directory / name)
        self.incidents.append({"reason": stop.reason, "saw": stop.saw[:12], "frame": name, "after_step": len(self.steps)})
        self.save()

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "trace.json").write_text(json.dumps(
            {"steps": [asdict(s) for s in self.steps], "incidents": self.incidents}, indent=1), encoding="utf-8")


class _WatchedDesktop:
    """Counts input, so a frame is never reused once anything has been sent to the screen."""

    def __init__(self, desktop):
        self._desktop = desktop
        self.inputs = 0

    def grab(self):
        return self._desktop.grab()

    def foreground_title(self):
        return self._desktop.foreground_title()

    def click(self, x, y):
        self.inputs += 1
        self._desktop.click(x, y)

    def type_text(self, text):
        self.inputs += 1
        self._desktop.type_text(text)

    def select_all_and_delete(self):
        self.inputs += 1
        self._desktop.select_all_and_delete()


class Robot:
    # OCR is slow. A frame this young, with no input since, can stand in for a fresh look.
    MAX_FRAME_AGE = 0.3

    def __init__(self, desktop, trace, timeout=10.0, poll=0.25, settle=1.5):
        self.desktop = _WatchedDesktop(desktop)
        self._frame_stamp = None
        self.trace = trace
        self.timeout = timeout
        self.poll = poll
        self.settle = settle
        self.ocr_seconds = 0.0
        self.window = None  # (left, top, right, bottom) once the app has been found
        self.last_frame = None

    def look(self):
        """One capture and OCR pass. Raises SafetyStop if this is not the demo app."""
        started = time.perf_counter()
        screenshot = self.desktop.grab()
        if self.window:
            left, top, right, bottom = self.window
            crop = screenshot.crop(self.window)
            texts = _read(crop)
            origin = (left, top)
            if len(_header_footer_pairs(texts)) != 1:
                # The window may have moved. Rescan once before concluding anything.
                self.window = None
        if not self.window:
            full = _read(screenshot)
            pairs = _header_footer_pairs(full)
            if len(pairs) != 1:
                raise _describe_miss(full, pairs)
            header, footer = pairs[0]
            pad = 12
            self.window = (max(0, header.x0 - pad), max(0, header.y0 - pad),
                           min(screenshot.width, footer.x1 + pad), min(screenshot.height, footer.y1 + pad))
            left, top = self.window[:2]
            crop = screenshot.crop(self.window)
            origin = (left, top)
            texts = [t.shifted(-left, -top) for t in full
                     if t.x0 >= left and t.y0 >= top and t.x1 <= self.window[2] and t.y1 <= self.window[3]]
        header = _header_footer_pairs(texts)[0][0]
        frame = Frame(crop, origin, texts, _screen_name(header.text))
        self.last_frame = frame
        self.ocr_seconds = time.perf_counter() - started
        self._frame_stamp = (time.monotonic(), self.desktop.inputs)
        return frame

    def expect(self, screen):
        """Wait for a screen. On timeout, stop."""
        if self._frame_stamp:
            taken, inputs = self._frame_stamp
            if (inputs == self.desktop.inputs and time.monotonic() - taken < self.MAX_FRAME_AGE
                    and same_text(self.last_frame.screen, screen)):
                return self.last_frame
        deadline = time.monotonic() + self.timeout
        settle = time.monotonic() + self.settle
        frame = None
        while True:
            try:
                frame = self.look()
            except SafetyStop:
                # A redraw can catch a half-painted window. No input is sent while waiting.
                if time.monotonic() > settle:
                    raise
                time.sleep(self.poll)
                continue
            if same_text(frame.screen, screen):
                return frame
            if time.monotonic() > deadline:
                raise SafetyStop(f"expected screen '{screen}' but it is showing '{frame.screen}'",
                                 [t.text for t in frame.texts])
            time.sleep(self.poll)

    def click(self, label, on, then, action=None):
        """Confirm we are on `on`, click `label`, confirm we reached `then`."""
        frame = self.expect(on)
        target = frame.find(label)
        if target is None:
            raise SafetyStop(f"no unambiguous '{label}' on screen '{on}'", [t.text for t in frame.texts])
        self.desktop.click(*frame.to_screen(*target.centre))
        self.trace.add(action or f"click '{label}'", frame, ocr_seconds=self.ocr_seconds)
        return self.expect(then)

    def click_entry_right_of(self, label, on):
        """Click the input box beside a label, which has no text of its own to read."""
        frame = self.expect(on)
        target = frame.find(label)
        if target is None:
            raise SafetyStop(f"no '{label}' on screen '{on}'", [t.text for t in frame.texts])
        height = target.y1 - target.y0
        self.desktop.click(*frame.to_screen(target.x1 + 12 + height * 3, target.centre[1]))
        return frame

    def click_below(self, label, on, offset):
        """Click a fixed distance below a label, for text areas that start out empty."""
        frame = self.expect(on)
        target = frame.find(label)
        if target is None:
            raise SafetyStop(f"no '{label}' on screen '{on}'", [t.text for t in frame.texts])
        self.desktop.click(*frame.to_screen(target.x0 + 60, target.y1 + offset))
        return frame

    def type_text(self, text, on):
        self.expect(on)
        self._check_foreground()
        self.desktop.type_text(text)

    def _check_foreground(self):
        title = self.desktop.foreground_title()
        if title is not None and not title.startswith(OS_TITLE):
            raise SafetyStop(f"the OS says the active window is '{title[:60]}'")

    def _evidence(self):
        """What is now showing where the app was. Cropped, so the rest of the desktop is never kept."""
        if self.last_frame is None:
            return None
        left, top = self.last_frame.origin
        width, height = self.last_frame.image.size
        crop = self.desktop.grab().crop((left, top, left + width, top + height))
        return Frame(crop, self.last_frame.origin, [], self.last_frame.screen)

    def guarded(self, work):
        """Run work, turning any stop into a recorded incident. Returns the stop, or None."""
        try:
            work()
        except SafetyStop as stop:
            self.trace.incident(stop, self._evidence())
            return stop
        except EmergencyStop as stop:
            wrapped = SafetyStop(str(stop))
            self.trace.incident(wrapped, self._evidence())
            return wrapped
        return None


def _screen_name(header_text):
    """'Larkmere Desk - Notes' to 'Notes'. OCR is unreliable about the spaces around the dash."""
    return re.sub(r"^\s*larkmere\s*desk\W*", "", header_text, flags=re.IGNORECASE).strip()


def _find_header(texts):
    # "App - Screen" only. The OS title bar on some platforms says just "App", which proves nothing.
    return [t for t in texts if normalise(t.text).startswith(normalise(APP)) and not same_text(t.text, APP)]


def _header_footer_pairs(texts):
    """Each header with a demo-environment footer below it. Exactly one pair means we've found the app."""
    footers = [t for t in texts if same_text(t.text, FOOTER)]
    return [(h, f) for h in _find_header(texts) for f in footers if f.x1 > h.x0 and f.y0 > h.y1]


def _read(image):
    return ocr.merge_adjacent([t for t in ocr.read(image) if t.confidence >= MIN_CONFIDENCE])


def _describe_miss(texts, pairs):
    """Why the app wasn't found. This text comes from the whole screen, so none of it goes in the
    stop's `saw` list, which gets saved. Other windows' contents are none of the robot's business."""
    if len(pairs) > 1:
        return SafetyStop(f"more than one window claims to be {APP}")
    if not _find_header(texts):
        return SafetyStop(f"{APP} is not on screen, or something is covering its header")
    for row in ocr.rows(texts):
        at = next((i for i, t in enumerate(row) if normalise(t.text).startswith("envlronment")), None)
        if at is not None:
            said = " ".join(t.text for t in row[at:at + 2])[:40]
            return SafetyStop(f"This is {APP}, but the footer says '{said}', not '{FOOTER}'")
    return SafetyStop(f"{APP} is visible but its environment footer is not")
