"""The only module that touches real input and real pixels. Tests swap it for a fake."""

import sys
from pathlib import Path

from PIL import ImageGrab


class EmergencyStop(Exception):
    """A person pushed the mouse into a screen corner."""


class RealDesktop:
    def __init__(self):
        if sys.platform == "win32":
            # Without this, screenshots and mouse coordinates disagree on scaled displays.
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        elif sys.platform.startswith("linux"):
            # pyautogui's X library refuses to load without this file, even for an open display.
            (Path.home() / ".Xauthority").touch()
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
        self._gui = pyautogui

    def grab(self):
        return ImageGrab.grab()

    def click(self, x, y):
        self._guarded(self._gui.click, x, y)

    def type_text(self, text):
        self._guarded(self._gui.write, text, interval=0.002)

    def select_all_and_delete(self):
        self._guarded(self._gui.hotkey, "ctrl", "a")
        self._guarded(self._gui.press, "backspace")

    def foreground_title(self):
        """A second opinion from the OS where one is cheap to get. None means no opinion."""
        if sys.platform != "win32":
            return None
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buffer, 256)
        return buffer.value

    def _guarded(self, action, *args, **kwargs):
        try:
            action(*args, **kwargs)
        except self._gui.FailSafeException as exc:
            raise EmergencyStop("mouse moved into a screen corner") from exc
