"""Metrics, all computed here and only here. The dashboard displays them and does no arithmetic.

Revenue means what was billed: the greater of commit and usage. All figures are in cents.
"""

import sqlite3
from collections import defaultdict

from larkmere.world import EVENTS, LAST_MONTH, TIERS, month_add, months_between

ALL = "All"
SEGMENT_FILTERS = (ALL, "Startup", "Mid-market", "Enterprise")
MOVEMENT_LABELS = ("new", "expansion", "contraction", "churn")


def load(db_path):
    db = sqlite3.connect(db_path)
    customers = {row[0]: {"id": row[0], "name": row[1], "segment": row[2], "region": row[3], "owner": row[4]}
                 for row in db.execute("SELECT id, name, segment, region, owner FROM customers")}
    statements = list(db.execute(
        "SELECT customer_id, month, commit_cents, usage_cents, overage_cents, unused_commit_cents, total_due_cents"
        " FROM statements"))
    lines = list(db.execute("SELECT customer_id, month, tier, kind, tokens_centi_m, amount_cents FROM lines"))
    db.close()
    return customers, statements, lines


def bridge(billed, months):
    """Month-on-month revenue movement. billed is {customer: {month: cents}}.

    A customer returning after a gap counts as new. Churn is booked in the first month with no
    bill, at the size of the last bill.
    """
    rows = []
    for previous, month in zip(months, months[1:]):
        row = dict.fromkeys(MOVEMENT_LABELS, 0)
        row["month"] = month
        row["opening"] = sum(b.get(previous, 0) for b in billed.values())
        for b in billed.values():
            before, after = b.get(previous, 0), b.get(month, 0)
            if before == 0 and after > 0:
                row["new"] += after
            elif before > 0 and after == 0:
                row["churn"] -= before
            elif after > before:
                row["expansion"] += after - before
            elif after < before:
                row["contraction"] += after - before
        row["closing"] = sum(b.get(month, 0) for b in billed.values())
        rows.append(row)
    return rows


def retention(billed, month, lookback=12):
    """Net and gross revenue retention, and logo retention, for the cohort billed `lookback` months ago."""
    start = month_add(month, -lookback)
    cohort = {c: b for c, b in billed.items() if b.get(start, 0) > 0}
    opening = sum(b[start] for b in cohort.values())
    if not opening:
        return {"nrr": None, "grr": None, "logo": None, "cohort": 0}
    closing = sum(b.get(month, 0) for b in cohort.values())
    capped = sum(min(b.get(month, 0), b[start]) for b in cohort.values())
    kept = sum(1 for b in cohort.values() if b.get(month, 0) > 0)
    return {"nrr": closing / opening, "grr": capped / opening, "logo": kept / len(cohort), "cohort": len(cohort)}


def primary_tier(tier_spend):
    """The tier a customer spends most on in a month, or None if they spent nothing."""
    return max(tier_spend, key=tier_spend.get) if tier_spend else None


def tier_movement(spend, month, lookback=12):
    """How many customers sit in each primary tier now, by where they were `lookback` months ago."""
    start = month_add(month, -lookback)
    labels = ("Not a customer",) + TIERS + ("Churned",)
    matrix = {a: dict.fromkeys(labels, 0) for a in labels}
    for by_month in spend.values():
        was = primary_tier(by_month.get(start, {})) or "Not a customer"
        now = primary_tier(by_month.get(month, {}))
        if now is None:
            now = "Churned" if was != "Not a customer" else None
        if now:
            matrix[was][now] += 1
    return {"labels": labels, "start": start, "end": month, "matrix": matrix}


def segment_metrics(customers, statements, lines, segment):
    months = months_between()
    wanted = {c for c, row in customers.items() if segment == ALL or row["segment"] == segment}
    billed = defaultdict(dict)
    monthly = {m: {"month": m, "revenue": 0, "committed": 0, "overage": 0, "usage": 0, "unused_commit": 0,
                   "customers": 0, "commit_customers": 0, "commit_usage": 0, "commit_total": 0} for m in months}
    for customer, month, commit, usage, overage, unused, due in statements:
        if customer not in wanted:
            continue
        billed[customer][month] = due
        row = monthly[month]
        row["revenue"] += due
        # Committed revenue is what the contract guarantees. Overage is the rest.
        row["committed"] += commit if commit else 0
        row["overage"] += due - commit
        row["usage"] += usage
        row["unused_commit"] += unused
        row["customers"] += 1
        if commit:
            row["commit_customers"] += 1
            row["commit_usage"] += usage
            row["commit_total"] += commit

    tokens = {m: {"month": m, **{t: 0 for t in TIERS}} for m in months}
    tier_value = {m: {"month": m, **{t: 0 for t in TIERS}} for m in months}
    spend = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for customer, month, tier, _, tokens_centi_m, amount in lines:
        if customer not in wanted:
            continue
        tokens[month][tier] += tokens_centi_m
        tier_value[month][tier] += amount
        spend[customer][month][tier] += amount

    bridge_rows = bridge(billed, months)
    now = monthly[LAST_MONTH]
    last_year = monthly[month_add(LAST_MONTH, -12)]
    return {
        "segment": segment,
        "monthly": list(monthly.values()),
        "bridge": bridge_rows,
        "tokens": list(tokens.values()),
        "tier_value": list(tier_value.values()),
        "retention": retention(billed, LAST_MONTH),
        "tier_movement": tier_movement(spend, LAST_MONTH),
        "kpis": {
            "revenue": now["revenue"],
            "revenue_growth": now["revenue"] / last_year["revenue"] - 1 if last_year["revenue"] else None,
            "annualised": now["revenue"] * 12,
            "customers": now["customers"],
            "overage_share": now["overage"] / now["revenue"] if now["revenue"] else None,
            "commit_utilisation": now["commit_usage"] / now["commit_total"] if now["commit_total"] else None,
        },
    }


def commit_signals(customers, statements):
    """Rule-based account lists for the current month. A prompt for a conversation, not a forecast."""
    upsell, at_risk = [], []
    for customer, month, commit, usage, overage, unused, _ in statements:
        if month != LAST_MONTH or customer not in customers:
            continue
        row = {**customers[customer], "commit": commit, "usage": usage, "overage": overage, "unused": unused}
        if commit and overage >= commit * 0.25:
            upsell.append(row)
        elif not commit and usage >= 300_000:
            upsell.append(row)
        if commit and usage < commit * 0.75:
            at_risk.append(row)
    upsell.sort(key=lambda r: -r["usage"])
    at_risk.sort(key=lambda r: -r["unused"])
    return {"upsell": upsell[:10], "at_risk": at_risk[:10],
            "rules": {"upsell": "On a commit with overage of 25% or more, or no commit and usage of $3,000 or more",
                      "at_risk": "Using less than 75% of commit"}}


def compute(db_path):
    customers, statements, lines = load(db_path)
    return {
        "as_of": LAST_MONTH,
        "events": EVENTS,
        "tiers": list(TIERS),
        "segments": {s: segment_metrics(customers, statements, lines, s) for s in SEGMENT_FILTERS},
        "signals": commit_signals(customers, statements),
    }
