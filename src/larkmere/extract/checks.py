"""Hard checks on an extracted statement. Plain arithmetic, no AI.

These never correct a value. A statement either passes all of them or goes to quarantine
for a person, with the failing checks named. The same code runs whichever extractor produced
the data, so it catches a misreading model and a buggy source system alike.
"""

from dataclasses import dataclass
from decimal import Decimal

from larkmere.world import RATE_CARD

CENT = Decimal("0.01")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _money(value):
    return f"${value:,.2f}"


def run_checks(extracted, expected_account, expected_period):
    e = extracted
    checks = [
        Check("right account", e.account_id == expected_account,
              f"asked for {expected_account}, statement says {e.account_id or 'nothing readable'}"),
        Check("right period", e.period == expected_period,
              f"asked for {expected_period}, statement says {e.period or 'nothing readable'}"),
        Check("all pages read", e.pages_total > 0 and e.pages_seen == e.pages_total,
              f"read {e.pages_seen} of {e.pages_total} pages"),
        Check("all lines present", e.line_count_reported > 0 and len(e.lines) == e.line_count_reported,
              f"statement reports {e.line_count_reported} line items, extracted {len(e.lines)}"),
    ]
    summary_fields = ("usage_total", "monthly_commit", "overage", "unused_commit", "total_due")
    missing = [f for f in summary_fields if getattr(e, f) is None]
    checks.append(Check("summary readable", not missing, f"missing: {', '.join(missing)}" if missing else "all five present"))

    for line in e.lines:
        computed = line.tokens_m * line.rate
        ok = abs(computed - line.amount) <= CENT
        checks.append(Check(f"{line.item}: tokens x rate = amount", ok,
                            f"{line.tokens_m} x {_money(line.rate)} = {_money(computed)}, shown {_money(line.amount)}"))
        tier, _, kind = line.item.partition(" ")
        listed = RATE_CARD.get((tier, kind))
        checks.append(Check(f"{line.item}: rate is on the rate card", listed is not None and line.rate * 100 == listed,
                            f"shown {_money(line.rate)}, rate card {_money(Decimal(listed) / 100) if listed else 'has no such item'}"))

    if not missing:
        total = sum((l.amount for l in e.lines), Decimal(0))
        checks.append(Check("lines add up to usage total", abs(total - e.usage_total) <= CENT,
                            f"lines add to {_money(total)}, statement says {_money(e.usage_total)}"))
        overage = max(Decimal(0), e.usage_total - e.monthly_commit)
        checks.append(Check("overage = usage above commit", abs(overage - e.overage) <= CENT,
                            f"computed {_money(overage)}, statement says {_money(e.overage)}"))
        unused = max(Decimal(0), e.monthly_commit - e.usage_total)
        checks.append(Check("unused commit = commit above usage", abs(unused - e.unused_commit) <= CENT,
                            f"computed {_money(unused)}, statement says {_money(e.unused_commit)}"))
        due = max(e.usage_total, e.monthly_commit)
        checks.append(Check("total due = greater of usage and commit", abs(due - e.total_due) <= CENT,
                            f"computed {_money(due)}, statement says {_money(e.total_due)}"))
    return checks


def verdict(checks):
    return "verified" if all(c.ok for c in checks) else "quarantined"
