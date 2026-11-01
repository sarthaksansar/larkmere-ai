import pytest
from PIL import Image, ImageDraw, ImageFont

from larkmere import world
from larkmere.robot import ocr


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    return world.build(tmp_path_factory.mktemp("world") / "larkmere.sqlite")


class FakeDesktop:
    """Serves prepared images and records input instead of sending it."""

    def __init__(self, screens):
        self.screens = list(screens)
        self.clicks = []
        self.typed = []

    def grab(self):
        return self.screens[0] if len(self.screens) == 1 else self.screens.pop(0)

    def click(self, x, y):
        self.clicks.append((x, y))

    def type_text(self, text):
        self.typed.append(text)

    def select_all_and_delete(self):
        pass

    def foreground_title(self):
        return None


def text_image(items, size=(1000, 700)):
    """Draws text at fixed spots, standing in for a screen."""
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)
    for x, y, text in items:
        draw.text((x, y), text, fill="black", font=font)
    return image


def fake_texts(items):
    """OCR results without running OCR, for tests about logic rather than reading."""
    return [ocr.Text(text, x, y, x + 10 * len(text), y + 20, 0.99) for x, y, text in items]


def app_screen(screen="Accounts", footer="Environment: demo data", extra=()):
    return [(20, 20, f"Larkmere Desk - {screen}"), (700, 650, footer), *extra]
