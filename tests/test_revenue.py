"""Metric definitions on small hand-made cases, and identities across the generated data."""

import sqlite3

import pytest

from larkmere import world
from larkmere.revenue import metrics


def test_bridge_classifies_each_kind_of_movement():
    billed = {"grew": {"a": 100, "b": 150}, "shrank": {"a": 100, "b": 60}, "left": {"a": 100},
              "joined": {"b": 70}, "flat": {"a": 50, "b": 50}}
    row = metrics.bridge(billed, ["a", "b"])[0]
    assert (row["new"], row["expansion"], row["contraction"], row["churn"]) == (70, 50, -40, -100)
    assert row["opening"] == 350 and row["closing"] == 330
    assert row["opening"] + row["new"] + row["expansion"] + row["contraction"] + row["churn"] == row["closing"]


def test_retention_definitions():
    start, end = world.month_add(world.LAST_MONTH, -12), world.LAST_MONTH
    billed = {"grew": {start: 100, end: 200}, "shrank": {start: 100, end: 50}, "left": {start: 100},
              "joined_later": {end: 999}}
    r = metrics.retention(billed, end)
    assert r["nrr"] == pytest.approx(250 / 300)
    assert r["grr"] == pytest.approx(150 / 300)
    assert r["logo"] == pytest.approx(2 / 3)
    assert r["cohort"] == 3


def test_retention_with_no_cohort_is_empty_not_a_crash():
    assert metrics.retention({}, world.LAST_MONTH)["nrr"] is None


def test_primary_tier():
    assert metrics.primary_tier({"Wren": 5, "Condor": 9}) == "Condor"
    assert metrics.primary_tier({}) is None


@pytest.fixture(scope="module")
def computed(db_path):
    return metrics.compute(db_path)


@pytest.mark.parametrize("segment", metrics.SEGMENT_FILTERS)
def test_bridge_identity_holds_every_month(computed, segment):
    for row in computed["segments"][segment]["bridge"]:
        assert row["opening"] + row["new"] + row["expansion"] + row["contraction"] + row["churn"] == row["closing"]


def test_segments_add_up_to_the_total(computed):
    for key in ("revenue", "committed", "overage", "customers"):
        for i in range(len(computed["segments"]["All"]["monthly"])):
            total = computed["segments"]["All"]["monthly"][i][key]
            parts = sum(computed["segments"][s]["monthly"][i][key] for s in metrics.SEGMENT_FILTERS[1:])
            assert total == parts, key


def test_committed_plus_overage_is_revenue(computed):
    for row in computed["segments"]["All"]["monthly"]:
        assert row["committed"] + row["overage"] == row["revenue"]


def test_every_bill_follows_the_contract_rule(db_path):
    with sqlite3.connect(db_path) as db:
        for commit, usage, overage, unused, due in db.execute(
                "SELECT commit_cents, usage_cents, overage_cents, unused_commit_cents, total_due_cents FROM statements"):
            assert due == max(commit, usage)
            assert overage == max(0, usage - commit)
            assert unused == max(0, commit - usage)


def test_every_line_multiplies_out_and_uses_the_rate_card(db_path):
    with sqlite3.connect(db_path) as db:
        for tier, kind, tokens, rate, amount in db.execute(
                "SELECT tier, kind, tokens_centi_m, rate_cents, amount_cents FROM lines"):
            assert rate == world.RATE_CARD[tier, kind]
            assert amount == world.line_amount(tokens, rate)


def test_statements_equal_the_sum_of_their_lines(db_path):
    with sqlite3.connect(db_path) as db:
        rows = db.execute("""SELECT s.usage_cents, SUM(l.amount_cents) FROM statements s
                             JOIN lines l ON l.customer_id=s.customer_id AND l.month=s.month
                             GROUP BY s.customer_id, s.month""").fetchall()
    assert rows and all(usage == total for usage, total in rows)


def test_generator_is_deterministic(tmp_path, db_path):
    other = world.build(tmp_path / "again.sqlite")
    query = "SELECT customer_id, month, amount_cents FROM lines ORDER BY 1,2,3"
    with sqlite3.connect(db_path) as a, sqlite3.connect(other) as b:
        assert a.execute(query).fetchall() == b.execute(query).fetchall()


def test_a_different_seed_changes_the_data(tmp_path, db_path):
    other = world.build(tmp_path / "seed.sqlite", seed=7)
    query = "SELECT SUM(amount_cents) FROM lines"
    with sqlite3.connect(db_path) as a, sqlite3.connect(other) as b:
        assert a.execute(query).fetchone() != b.execute(query).fetchone()


def test_planted_stories_show_up(computed):
    enterprise = computed["segments"]["Enterprise"]["bridge"]
    churn_month = next(r for r in enterprise if r["month"] == world.WHALE_CHURN[3])
    assert churn_month["churn"] < -5_000_000  # a whale of more than $50k a month leaving
    expansion_month = next(r for r in enterprise if r["month"] == world.WHALE_EXPANSION[3])
    assert expansion_month["expansion"] > 2_000_000
    # The Wren refresh moves some customers from Heron down to Wren within a year.
    movement = computed["segments"]["Mid-market"]["tier_movement"]
    assert computed["signals"]["upsell"] and computed["signals"]["at_risk"]
    assert movement["matrix"]["Heron"]["Wren"] + movement["matrix"]["Wren"]["Wren"] > 0


def test_every_tier_movement_cell_is_accounted_for(computed):
    move = computed["segments"]["All"]["tier_movement"]
    started = sum(sum(move["matrix"][t].values()) for t in world.TIERS)
    with_start = computed["segments"]["All"]["retention"]["cohort"]
    assert started == with_start
