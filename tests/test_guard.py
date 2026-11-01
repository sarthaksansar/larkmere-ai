"""The robot's safety behaviour, using real OCR on drawn screens and a fake mouse."""

import pytest

from conftest import FakeDesktop, app_screen, fake_texts, text_image
from larkmere.robot import ocr
from larkmere.robot.navigator import Frame, Robot, SafetyStop, Trace

pytestmark = pytest.mark.ocr


def robot_on(tmp_path, *screens):
    desktop = FakeDesktop(text_image(items) for items in screens)
    robot = Robot(desktop, Trace(tmp_path), timeout=0.2, poll=0.01, settle=0.1)
    return robot, desktop


def test_recognises_the_right_screen_and_clicks_the_label(tmp_path):
    screen = app_screen("Accounts", extra=[(40, 120, "Search")])
    robot, desktop = robot_on(tmp_path, screen, app_screen("Statements"))
    robot.click("Search", on="Accounts", then="Statements")
    assert len(desktop.clicks) == 1
    x, y = desktop.clicks[0]
    assert 40 <= x <= 140 and 115 <= y <= 150


@pytest.mark.parametrize("screen,expected_words", [
    ([(100, 100, "Quarterly report.xlsx"), (100, 200, "Save changes?")], "not on screen"),
    (app_screen("Accounts", footer="Environment: production"), "production"),
    ([(20, 20, "Larkmere Desk - Accounts")], "footer"),
])
def test_stops_without_sending_input(tmp_path, screen, expected_words):
    robot, desktop = robot_on(tmp_path, screen)
    with pytest.raises(SafetyStop) as stop:
        robot.click("Search", on="Accounts", then="Accounts")
    assert expected_words in stop.value.reason
    assert desktop.clicks == [] and desktop.typed == []


def test_stops_on_the_wrong_screen_of_the_right_app(tmp_path):
    robot, desktop = robot_on(tmp_path, app_screen("Notes", extra=[(40, 120, "Search")]))
    with pytest.raises(SafetyStop, match="expected screen 'Accounts'"):
        robot.click("Search", on="Accounts", then="Accounts")
    assert desktop.clicks == []


def test_stops_when_the_expected_label_is_missing(tmp_path):
    robot, desktop = robot_on(tmp_path, app_screen("Accounts"))
    with pytest.raises(SafetyStop, match="no unambiguous 'Search'"):
        robot.click("Search", on="Accounts", then="Accounts")
    assert desktop.clicks == []


def test_stops_when_the_label_is_ambiguous(tmp_path):
    screen = app_screen("Accounts", extra=[(40, 120, "Search"), (40, 300, "Search")])
    robot, desktop = robot_on(tmp_path, screen)
    with pytest.raises(SafetyStop):
        robot.click("Search", on="Accounts", then="Accounts")
    assert desktop.clicks == []


def test_stops_when_two_windows_claim_to_be_the_app(tmp_path):
    screen = app_screen("Accounts", extra=[(20, 300, "Larkmere Desk - Notes")])
    robot, desktop = robot_on(tmp_path, screen)
    with pytest.raises(SafetyStop, match="more than one"):
        robot.expect("Accounts")
    assert desktop.clicks == []


def test_a_popup_arriving_mid_run_stops_the_next_action(tmp_path):
    good = app_screen("Accounts", extra=[(40, 120, "Search")])
    popup = [(200, 200, "Update available: restart now?")]
    robot, desktop = robot_on(tmp_path, good, popup, popup, popup, popup)
    robot.expect("Accounts")
    robot.settle = 0
    robot.MAX_FRAME_AGE = 0
    with pytest.raises(SafetyStop):
        robot.click("Search", on="Accounts", then="Accounts")
    assert desktop.clicks == []


def test_incident_keeps_only_the_app_window_area(tmp_path):
    good = app_screen("Accounts")
    robot, _ = robot_on(tmp_path, good)
    frame = robot.expect("Accounts")
    assert frame.image.size[0] < 1000 and frame.image.size[1] < 700
    stop = robot.guarded(lambda: (_ for _ in ()).throw(SafetyStop("test")))
    assert stop.reason == "test"
    assert (tmp_path / "incident-1.png").exists()
    assert robot.trace.incidents[0]["reason"] == "test"


def test_text_outside_the_window_is_not_in_the_frame(tmp_path):
    screen = app_screen("Accounts") + [(20, 680, "someone outside the window")]
    robot, _ = robot_on(tmp_path, screen)
    frame = robot.expect("Accounts")
    assert not any("outside" in t.text for t in frame.texts)


def test_a_failed_search_of_the_whole_screen_saves_nothing_from_it(tmp_path):
    secret = "Private Letter To My Bank"
    robot, desktop = robot_on(tmp_path, [(100, 100, secret)])
    stop = robot.guarded(lambda: robot.expect("Accounts"))
    assert stop is not None and desktop.clicks == []
    robot.trace.save()
    saved = (tmp_path / "trace.json").read_text(encoding="utf-8")
    assert "Letter" not in saved and "Bank" not in saved
    assert not list(tmp_path.glob("*.png"))


def test_a_header_the_engine_split_in_two_is_still_recognised(tmp_path, monkeypatch):
    pieces = fake_texts([(20, 20, "Larkmere"), (20 + 10 * 8 + 6, 20, "Desk - Accounts"), (700, 650, "Environment:"),
                         (700 + 10 * 12 + 6, 650, "demo data")])
    monkeypatch.setattr(ocr, "read", lambda image: pieces)
    robot = Robot(FakeDesktop([text_image([])]), Trace(tmp_path), timeout=0.1, poll=0.01, settle=0)
    assert robot.expect("Accounts").screen == "Accounts"


def test_the_app_name_mentioned_elsewhere_on_screen_does_not_confuse_it(tmp_path):
    screen = app_screen("Accounts") + [(20, 680, "Larkmere Desk - Notes")]
    robot, _ = robot_on(tmp_path, screen)
    assert robot.expect("Accounts").screen == "Accounts"


def frame_with(*labels):
    return Frame(None, (0, 0), fake_texts([(20, 40 + i * 40, label) for i, label in enumerate(labels)]), "Accounts")


def test_a_slightly_garbled_label_is_still_found():
    assert frame_with("Find a account", "Search").find("Find account").text == "Find a account"


def test_a_misread_digit_is_never_accepted():
    assert frame_with("2026-08", "2026-07").find("2026-09") is None


def test_two_close_matches_count_as_not_found():
    assert frame_with("Find a account", "Find b account").find("Find account") is None


def test_a_different_word_is_not_a_close_match():
    assert frame_with("New note").find("Save note") is None


def test_columns_far_apart_are_not_merged():
    pieces = fake_texts([(20, 20, "Usage total"), (400, 20, "$40.00")])
    assert [t.text for t in ocr.merge_adjacent(pieces)] == ["Usage total", "$40.00"]
