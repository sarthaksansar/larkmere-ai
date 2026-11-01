"""Hard checks: every kind of damage to a good statement must be caught."""

import copy
import sqlite3
from decimal import Decimal

import pytest

from conftest import fake_texts
from larkmere import world
from larkmere.extract.checks import run_checks, verdict
from larkmere.extract.offline import OfflineExtractor, parse_pages
from larkmere.extract.schema import Extracted, Line
from larkmere.robot import ocr


def statement():
    lines = [Line("Wren input", Decimal("100.00"), Decimal("0.40"), Decimal("40.00")),
             Line("Heron output", Decimal("10.50"), Decimal("10.00"), Decimal("105.00"))]
    return Extracted("LM-1", "2026-09", 1, 1, 2, lines, Decimal("145.00"), Decimal("100.00"), Decimal("45.00"),
                     Decimal("0.00"), Decimal("145.00"))


def result(e):
    return verdict(run_checks(e, "LM-1", "2026-09"))


def test_good_statement_passes():
    assert result(statement()) == "verified"


def set_field(name, value):
    def damage(e):
        setattr(e, name, Decimal(value) if value else None)
    return damage


def wrong_amount(e):
    e.lines[0].amount = Decimal("44.00")


def wrong_tokens(e):
    e.lines[1].tokens_m = Decimal("15.50")


def wrong_rate(e):
    e.lines[0].rate = Decimal("0.50")
    e.lines[0].tokens_m = Decimal("80.00")


def dropped_line(e):
    e.lines.pop()


def duplicated_line(e):
    e.lines.append(copy.deepcopy(e.lines[0]))
    e.line_count_reported = 3


def missed_page(e):
    e.pages_seen = 1
    e.pages_total = 2


def other_account(e):
    e.account_id = "LM-2"


def other_period(e):
    e.period = "2026-08"


@pytest.mark.parametrize("damage", [
    set_field("usage_total", "154.00"), set_field("overage", "54.00"), set_field("total_due", "154.00"),
    set_field("monthly_commit", "90.00"), set_field("unused_commit", "5.00"), set_field("total_due", None),
    wrong_amount, wrong_tokens, wrong_rate, dropped_line, duplicated_line, missed_page, other_account, other_period,
])
def test_any_damage_is_quarantined(damage):
    e = statement()
    damage(e)
    assert result(e) == "quarantined"


def test_checks_do_not_correct_values():
    e = statement()
    e.total_due = Decimal("999.00")
    run_checks(e, "LM-1", "2026-09")
    assert e.total_due == Decimal("999.00")


def test_a_one_cent_rounding_difference_is_allowed_but_two_are_not():
    e = statement()
    e.lines[0].amount = Decimal("40.01")
    e.usage_total, e.total_due, e.overage = Decimal("145.01"), Decimal("145.01"), Decimal("45.01")
    assert result(e) == "verified"
    e.lines[0].amount = Decimal("40.02")
    e.usage_total, e.total_due, e.overage = Decimal("145.02"), Decimal("145.02"), Decimal("45.02")
    assert result(e) == "quarantined"


def page(rows):
    return fake_texts([(20, 40 + i * 30, text) for i, text in enumerate(rows)])


def test_parser_handles_what_ocr_really_does():
    texts = page(["LM-1002 Thistledown Health", "Period:2026-09 Page 1 of 1 Line items:6",
                  "Wren input 1,154.24 $0.40 $461,70", "Heron output 75.66 $10.00 $756.60",
                  "Usagetotal $2.786,77", "Total due $2,786.77"])
    e = parse_pages([texts])
    assert e.account_id == "LM-1002" and e.period == "2026-09" and e.line_count_reported == 6
    assert e.lines[0].tokens_m == Decimal("1154.24") and e.lines[0].amount == Decimal("461.70")
    assert e.usage_total == Decimal("2786.77")
    assert e.total_due == Decimal("2786.77")


def test_parser_leaves_unreadable_figures_empty_rather_than_guessing():
    e = parse_pages([page(["Period 2026-09 Page 1 of 1 Line items 1", "Total due $2,7?6.77"])])
    assert e.total_due is None


HEADER = "Period 2026-09 Page 1 of 1 Line items 1"
LINE_ROW = "Wren input 100.00 $0.40 $40.00"
SUMMARY_ROWS = ["Usage total $40.00", "Monthly commit $0.00", "Overage $40.00", "Unused commit $0.00", "Total due $40.00"]


def test_reads_again_when_the_detector_skipped_something(monkeypatch):
    readings = iter([page(["LM-1", HEADER, *SUMMARY_ROWS]), page(["LM-1", HEADER, LINE_ROW, *SUMMARY_ROWS])])
    monkeypatch.setattr(ocr, "read", lambda image, **_: next(readings))
    e = OfflineExtractor().extract("LM-1", "2026-09", ["page"])
    assert len(e.lines) == 1


def test_a_complete_reading_that_does_not_add_up_is_not_read_again(monkeypatch):
    calls = []

    def read(image, **settings):
        calls.append(settings)
        return page(["LM-1", HEADER, LINE_ROW, "Usage total $99.00", *SUMMARY_ROWS[1:]])

    monkeypatch.setattr(ocr, "read", read)
    e = OfflineExtractor().extract("LM-1", "2026-09", ["page"])
    assert len(calls) == 1
    assert verdict(run_checks(e, "LM-1", "2026-09")) == "quarantined"


def test_gives_back_the_first_reading_when_none_is_complete(monkeypatch):
    calls = []

    def read(image, **settings):
        calls.append(settings)
        return page(["LM-1", HEADER, *SUMMARY_ROWS])

    monkeypatch.setattr(ocr, "read", read)
    e = OfflineExtractor().extract("LM-1", "2026-09", ["page"])
    assert len(calls) == len(ocr.READINGS)
    assert verdict(run_checks(e, "LM-1", "2026-09")) == "quarantined"


def test_the_planted_statements_differ_from_the_truth_only_where_intended(db_path):
    db = sqlite3.connect(db_path)
    for account, *_ in world.DEMO_ACCOUNTS:
        shown, shown_lines = world.displayed_statement(db, account, world.LAST_MONTH)
        true_lines = sum(l["amount_cents"] for l in shown_lines)
        faulty = account in (world.STALE_TOTAL_ACCOUNT, world.BAD_LINE_ACCOUNT)
        assert (true_lines != shown["usage_cents"]) is faulty, account
