"""Seeded generator for the fictional company. Everything here is invented.

Writes one SQLite file. The desktop CRM reads it to draw its screens and the revenue
metrics read it directly, so the two can be reconciled against each other in tests.
The automation never touches this file: it only sees pixels.

Money is held in integer cents and token volumes in hundredths of a million, so that
arithmetic on statements is exact.
"""

import sqlite3
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from random import Random

SEED = 20260930
FIRST_MONTH = "2024-01"
LAST_MONTH = "2026-09"

TIERS = ("Wren", "Heron", "Condor")
KINDS = ("input", "output")
# Invented list prices, in cents per million tokens.
RATE_CARD = {
    ("Wren", "input"): 40, ("Wren", "output"): 160,
    ("Heron", "input"): 250, ("Heron", "output"): 1000,
    ("Condor", "input"): 1200, ("Condor", "output"): 4800,
}
INPUT_VALUE_SHARE = 0.35

SEGMENTS = {
    # segment: (weight, median monthly usage in dollars, chance of a commit, monthly churn hazard)
    "Startup": (0.45, 450, 0.10, 0.018),
    "Mid-market": (0.38, 4_200, 0.55, 0.008),
    "Enterprise": (0.17, 31_000, 0.90, 0.003),
}
REGIONS = ("Americas", "EMEA", "APAC")
OWNERS = ("Ada Thornquist", "Bram Oyelaran", "Cleo Vantongeren", "Dev Marchetti", "Esme Rookwood", "Finn Halloway")

# Tier launches inside the story. They explain the movement between tiers in the dashboard.
WREN_REFRESH = "2025-08"
CONDOR_REFRESH = "2026-02"
EVENTS = [
    {"month": WREN_REFRESH, "label": "Wren refresh: cheap tier gets good enough for many Heron jobs"},
    {"month": CONDOR_REFRESH, "label": "Condor refresh: enterprises move hard work up a tier"},
]

NAME_STEMS = (
    "Tansywood", "Wickerlane", "Dunnock", "Yarrowgate", "Mossgrove", "Larchwick", "Quenby", "Rookhaven",
    "Sedgeworth", "Teasel", "Underhay", "Vetchley", "Whinfield", "Alderwick", "Cobblemere", "Hobnail",
    "Fennimore", "Bramblewick", "Nettlebury", "Ousel", "Pipistrel", "Ragwort", "Sallowfield", "Tormentil",
)
NAME_TRADES = (
    "Freight", "Seed Co", "Robotics", "Media", "Legal", "Analytics", "Retail", "Foods",
    "Software", "Travel", "Energy", "Learning", "Biotech", "Games", "Print Works", "Marine",
)

# The accounts the robot visits. Each exercises one thing the checks must get right.
DEMO_ACCOUNTS = [
    # id, name, segment, tiers used, commit in dollars, what this account demonstrates
    ("LM-1001", "Saltmarsh Analytics", "Startup", ("Wren", "Heron"), 0, "clean: pay as you go, one page"),
    ("LM-1002", "Thistledown Health", "Enterprise", TIERS, 18_000, "clean: overage, two pages"),
    ("LM-1003", "Oakhollow Legal", "Mid-market", ("Wren", "Heron"), 6_000, "clean: commit not fully used"),
    ("LM-1004", "Pennyroyal Insurance", "Enterprise", TIERS, 22_000, "planted: reported totals are stale"),
    ("LM-1005", "Marrowfield Logistics", "Mid-market", TIERS, 3_000, "planted: one line's arithmetic is wrong"),
    ("LM-1006", "Gorsewick Media", "Mid-market", TIERS, 2_500, "clean: used for the injected extractor fault"),
]
STALE_TOTAL_ACCOUNT = "LM-1004"
BAD_LINE_ACCOUNT = "LM-1005"
# A pretend billing-system bug: the subtotal was not refreshed after a late usage correction.
STALE_TOTAL_ERROR_CENTS = 90_000
BAD_LINE_ERROR_CENTS = 1_800

# Named stories that show up in the revenue dashboard.
WHALE_CHURN = ("LM-1007", "Cobblemere Reinsurance", "Enterprise", "2026-03")
WHALE_EXPANSION = ("LM-1008", "Hobnail Robotics", "Enterprise", "2025-11")

SCHEMA = """
CREATE TABLE customers (id TEXT PRIMARY KEY, name TEXT, segment TEXT, region TEXT, owner TEXT,
                        first_month TEXT, churn_month TEXT);
CREATE TABLE lines (customer_id TEXT, month TEXT, tier TEXT, kind TEXT,
                    tokens_centi_m INTEGER, rate_cents INTEGER, amount_cents INTEGER,
                    PRIMARY KEY (customer_id, month, tier, kind));
-- True figures for each month. Revenue analytics reads these.
CREATE TABLE statements (customer_id TEXT, month TEXT, commit_cents INTEGER, usage_cents INTEGER,
                         overage_cents INTEGER, unused_commit_cents INTEGER, total_due_cents INTEGER,
                         PRIMARY KEY (customer_id, month));
-- What the CRM shows, where a planted fault makes it differ from the truth.
CREATE TABLE display_overrides (customer_id TEXT, month TEXT, tier TEXT, kind TEXT, field TEXT, cents INTEGER);
CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id TEXT, body TEXT, saved_by TEXT);
"""


def months_between(first=FIRST_MONTH, last=LAST_MONTH):
    y, m = map(int, first.split("-"))
    months = []
    while f"{y}-{m:02d}" <= last:
        months.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def month_add(month, n):
    y, m = map(int, month.split("-"))
    index = y * 12 + m - 1 + n
    return f"{index // 12}-{index % 12 + 1:02d}"


def line_amount(tokens_centi_m, rate_cents):
    """Tokens (hundredths of a million) times a rate in cents per million, half-up to the cent."""
    exact = Decimal(tokens_centi_m) * Decimal(rate_cents) / Decimal(100)
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def bill(commit_cents, usage_cents):
    """The contract rule: you pay your commit or your usage, whichever is more."""
    return {
        "overage_cents": max(0, usage_cents - commit_cents),
        "unused_commit_cents": max(0, commit_cents - usage_cents),
        "total_due_cents": max(usage_cents, commit_cents),
    }


@dataclass
class Sim:
    id: str
    name: str
    segment: str
    region: str
    owner: str
    first_month: str
    base: float
    growth: float
    has_commit: bool
    mix: dict
    churn_month: str | None = None
    fixed_commit: int | None = None


def _tier_mix(rng, segment, tiers=None):
    weights = {
        "Startup": (0.62, 0.34, 0.04),
        "Mid-market": (0.28, 0.60, 0.12),
        "Enterprise": (0.18, 0.64, 0.18),
    }[segment]
    raw = {t: max(0.01, w * rng.uniform(0.6, 1.4)) for t, w in zip(TIERS, weights)}
    if tiers:
        raw = {t: v for t, v in raw.items() if t in tiers}
    total = sum(raw.values())
    return {t: v / total for t, v in raw.items()}


def _customer_names(rng, count):
    names = [f"{stem} {trade}" for stem in NAME_STEMS for trade in NAME_TRADES]
    rng.shuffle(names)
    return names[:count]


def _simulated_customers(rng, months, count=110):
    sims = []
    for i, (cid, name, segment, tiers, commit, _) in enumerate(DEMO_ACCOUNTS):
        sims.append(Sim(cid, name, segment, REGIONS[i % 3], OWNERS[i % 6], months[i], SEGMENTS[segment][1] * 1.2,
                        0.03, commit > 0, _tier_mix(rng, segment, tiers), fixed_commit=commit * 100))
    for cid, name, segment, churn in (WHALE_CHURN,):
        sims.append(Sim(cid, name, segment, "EMEA", OWNERS[2], months[1], 95_000, 0.015, True,
                        _tier_mix(rng, segment), churn_month=churn))
    for cid, name, segment, _ in (WHALE_EXPANSION,):
        sims.append(Sim(cid, name, segment, "APAC", OWNERS[3], months[4], 24_000, 0.02, True, _tier_mix(rng, segment)))

    segments = list(SEGMENTS)
    weights = [SEGMENTS[s][0] for s in segments]
    for i, name in enumerate(_customer_names(rng, count)):
        segment = rng.choices(segments, weights)[0]
        median, commit_chance, hazard = SEGMENTS[segment][1:]
        sim = Sim(
            id=f"LM-{1100 + i}", name=name, segment=segment,
            region=rng.choice(REGIONS), owner=rng.choice(OWNERS),
            # Weighted towards recent months, as a growing business would be.
            first_month=months[min(int(len(months) * rng.random() ** 0.8), len(months) - 1)],
            base=median * rng.lognormvariate(0, 0.55),
            growth=rng.gauss(0.02, 0.025),
            has_commit=rng.random() < commit_chance,
            mix=_tier_mix(rng, segment),
        )
        for month in months[months.index(sim.first_month) + 1:]:
            if rng.random() < hazard:
                sim.churn_month = month
                break
        sims.append(sim)
    return sims


def _shifted_mix(sim, month):
    """Move usage between tiers after each refresh. Returns value multipliers per tier."""
    values = dict(sim.mix)
    if sim.segment in ("Startup", "Mid-market") and "Wren" in values and "Heron" in values and month >= WREN_REFRESH:
        ramp = min(1.0, len(months_between(WREN_REFRESH, month)) / 5)
        moved = values["Heron"] * 0.35 * ramp
        values["Heron"] -= moved
        # Tokens move across at a much lower price, partly offset by people using far more of them.
        values["Wren"] += moved * (RATE_CARD["Wren", "output"] / RATE_CARD["Heron", "output"]) * 3
    if sim.segment == "Enterprise" and "Condor" in values and "Heron" in values and month >= CONDOR_REFRESH:
        ramp = min(1.0, len(months_between(CONDOR_REFRESH, month)) / 4)
        moved = values["Heron"] * 0.30 * ramp
        values["Heron"] -= moved
        values["Condor"] += moved * (RATE_CARD["Condor", "output"] / RATE_CARD["Heron", "output"]) * 0.35
    return values


def _month_lines(sim, month, usage_dollars):
    rows = []
    for tier, share in _shifted_mix(sim, month).items():
        for kind in KINDS:
            part = INPUT_VALUE_SHARE if kind == "input" else 1 - INPUT_VALUE_SHARE
            rate = RATE_CARD[tier, kind]
            tokens = max(1, round(usage_dollars * share * part * 100 / rate * 100))
            rows.append((sim.id, month, tier, kind, tokens, rate, line_amount(tokens, rate)))
    return rows


def _renewed_commit(usage_cents):
    """Renewals land on 90% of current run rate, rounded down to $500."""
    return max(50_000, int(usage_cents * 0.9) // 50_000 * 50_000)


def build(path, seed=SEED):
    rng = Random(seed)
    months = months_between()
    sims = _simulated_customers(rng, months)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    for sim in sims:
        db.execute("INSERT INTO customers VALUES (?,?,?,?,?,?,?)",
                   (sim.id, sim.name, sim.segment, sim.region, sim.owner, sim.first_month, sim.churn_month))
        active = months[months.index(sim.first_month):]
        if sim.churn_month:
            active = active[:active.index(sim.churn_month)]
        commit = sim.fixed_commit or 0
        for age, month in enumerate(active):
            usage = sim.base * (1 + sim.growth) ** age * rng.uniform(0.92, 1.08)
            if sim.id == WHALE_EXPANSION[0] and month >= WHALE_EXPANSION[3]:
                usage *= 2.6
            if sim.id == "LM-1002":
                usage = 22_000 * rng.uniform(0.97, 1.03)
            if sim.id == "LM-1003":
                usage = 4_200 * rng.uniform(0.95, 1.05)
            if sim.id == "LM-1004":
                usage = 26_000 * rng.uniform(0.97, 1.03)
            if sim.id in ("LM-1005", "LM-1006"):
                usage = 3_400 * rng.uniform(0.95, 1.05)
            rows = _month_lines(sim, month, usage)
            usage_cents = sum(r[6] for r in rows)
            if sim.has_commit and sim.fixed_commit is None and age % 12 == 0:
                commit = _renewed_commit(usage_cents)
            db.executemany("INSERT INTO lines VALUES (?,?,?,?,?,?,?)", rows)
            b = bill(commit, usage_cents)
            db.execute("INSERT INTO statements VALUES (?,?,?,?,?,?,?)",
                       (sim.id, month, commit, usage_cents, b["overage_cents"], b["unused_commit_cents"],
                        b["total_due_cents"]))
    _plant_faults(db)
    db.commit()
    db.close()
    return path


def _plant_faults(db):
    """Make the CRM display figures that disagree with themselves, as a buggy upstream system would."""
    usage, due = db.execute("SELECT usage_cents, total_due_cents FROM statements WHERE customer_id=? AND month=?",
                            (STALE_TOTAL_ACCOUNT, LAST_MONTH)).fetchone()
    db.executemany("INSERT INTO display_overrides VALUES (?,?,NULL,NULL,?,?)", [
        (STALE_TOTAL_ACCOUNT, LAST_MONTH, "usage_cents", usage + STALE_TOTAL_ERROR_CENTS),
        (STALE_TOTAL_ACCOUNT, LAST_MONTH, "total_due_cents", due + STALE_TOTAL_ERROR_CENTS),
    ])
    amount = db.execute("SELECT amount_cents FROM lines WHERE customer_id=? AND month=? AND tier='Heron' AND kind='output'",
                        (BAD_LINE_ACCOUNT, LAST_MONTH)).fetchone()[0]
    db.execute("INSERT INTO display_overrides VALUES (?,?,'Heron','output','amount_cents',?)",
               (BAD_LINE_ACCOUNT, LAST_MONTH, amount + BAD_LINE_ERROR_CENTS))


def displayed_statement(db, customer_id, month):
    """The statement as the CRM shows it, planted faults included."""
    db.row_factory = sqlite3.Row
    s = dict(db.execute("SELECT * FROM statements WHERE customer_id=? AND month=?", (customer_id, month)).fetchone())
    lines = [dict(r) for r in db.execute(
        "SELECT * FROM lines WHERE customer_id=? AND month=? ORDER BY "
        "CASE tier WHEN 'Wren' THEN 0 WHEN 'Heron' THEN 1 ELSE 2 END, kind", (customer_id, month))]
    for row in db.execute("SELECT * FROM display_overrides WHERE customer_id=? AND month=?", (customer_id, month)):
        if row["tier"]:
            for line in lines:
                if line["tier"] == row["tier"] and line["kind"] == row["kind"]:
                    line[row["field"]] = row["cents"]
        else:
            s[row["field"]] = row["cents"]
    db.row_factory = None
    return s, lines
