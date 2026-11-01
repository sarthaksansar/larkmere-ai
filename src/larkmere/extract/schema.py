"""The shape every extractor must produce, and its JSON Schema for model calls."""

from dataclasses import asdict, dataclass, field
from decimal import Decimal


@dataclass
class Line:
    item: str  # e.g. "Heron output"
    tokens_m: Decimal
    rate: Decimal
    amount: Decimal


@dataclass
class Extracted:
    account_id: str
    period: str
    pages_seen: int
    pages_total: int
    line_count_reported: int
    lines: list = field(default_factory=list)
    usage_total: Decimal | None = None
    monthly_commit: Decimal | None = None
    overage: Decimal | None = None
    unused_commit: Decimal | None = None
    total_due: Decimal | None = None
    source: str = "unknown"  # which extractor produced this, shown wherever the result is shown

    def to_json(self):
        data = asdict(self)
        return _plain(data)

    @classmethod
    def from_json(cls, data):
        data = dict(data)
        data["lines"] = [Line(item=l["item"], **{k: Decimal(str(l[k])) for k in ("tokens_m", "rate", "amount")})
                         for l in data["lines"]]
        for key in ("usage_total", "monthly_commit", "overage", "unused_commit", "total_due"):
            data[key] = None if data.get(key) is None else Decimal(str(data[key]))
        return cls(**data)


def _plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


# Numbers are strings on purpose: a statement's cents should never pass through a float.
MONEY = {"type": "string", "description": "Plain decimal with no currency sign or commas, such as 1234.50"}
JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["account_id", "period", "pages_total", "line_count_reported", "lines", "usage_total",
                 "monthly_commit", "overage", "unused_commit", "total_due"],
    "properties": {
        "account_id": {"type": "string"},
        "period": {"type": "string", "description": "YYYY-MM"},
        "pages_total": {"type": "integer"},
        "line_count_reported": {"type": "integer"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["item", "tokens_m", "rate", "amount"],
                "properties": {"item": {"type": "string"}, "tokens_m": MONEY, "rate": MONEY, "amount": MONEY},
            },
        },
        "usage_total": MONEY,
        "monthly_commit": MONEY,
        "overage": MONEY,
        "unused_commit": MONEY,
        "total_due": MONEY,
    },
}
