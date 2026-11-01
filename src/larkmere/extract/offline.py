"""Rule-based stand-in for the AI extractor. No model is involved.

It parses OCR rows with regular expressions, so the demo runs with no key and no network.
It only works because this CRM's layout is known and fixed, which is the very thing a
vision model doesn't need. Anything it can't read is left empty for the checks to reject.
"""

import re
from decimal import Decimal, InvalidOperation

from larkmere.extract.schema import Extracted, Line
from larkmere.robot import ocr

SOURCE = "rule-based OCR parser (no AI)"
# OCR swaps commas and points, so accept either. The last one before two digits is the decimal point.
# It must be a whole token: reading "$2,7?6.77" as 6.77 would be a guess, not a reading.
NUMBER = r"(?<![^\s$])\$?\s*(\d[\d,.]*[.,]\d{2})(?!\S)"
# OCR also drops spaces between words, so whitespace is optional throughout.
LINE = re.compile(rf"(Wren|Heron|Condor)\s*(input|output)\s*{NUMBER}\s+{NUMBER}\s+{NUMBER}", re.IGNORECASE)
HEADER = re.compile(r"Period\W*(\d{4}-\d{2})\s*Page\s*(\d+)\s*of\s*(\d+)\s*Line\s*items\W*(\d+)", re.IGNORECASE)
ACCOUNT = re.compile(r"(LM-\d{4})")
SUMMARY = {
    "usage_total": "Usage total",
    "monthly_commit": "Monthly commit",
    "overage": "Overage",
    "unused_commit": "Unused commit",
    "total_due": "Total due",
}


def _decimal(text):
    try:
        return Decimal(re.sub(r"[.,]", "", text[:-3]) + "." + text[-2:])
    except InvalidOperation:
        return None


def parse_pages(texts_per_page):
    """texts_per_page is a list of OCR results, one per statement page."""
    result = Extracted(account_id="", period="", pages_seen=0, pages_total=0, line_count_reported=0, source=SOURCE)
    for texts in texts_per_page:
        result.pages_seen += 1
        for row in ocr.rows(texts):
            joined = " ".join(t.text for t in row)
            if not result.account_id and (match := ACCOUNT.search(joined)):
                result.account_id = match[1]
            if match := HEADER.search(joined):
                result.period, result.pages_total, result.line_count_reported = match[1], int(match[3]), int(match[4])
            if match := LINE.search(joined):
                values = [_decimal(match[i]) for i in (3, 4, 5)]
                if None not in values:
                    result.lines.append(Line(f"{match[1]} {match[2]}", *values))
            for key, label in SUMMARY.items():
                squeezed = joined.lower().replace(" ", "")
                if squeezed.startswith(label.lower().replace(" ", "")) and (match := re.search(NUMBER, joined)):
                    setattr(result, key, _decimal(match[1]))
    return result


def is_complete(extracted):
    """Did this reading find everything the statement says it has? Says nothing about whether it adds up."""
    return (extracted.line_count_reported > 0 and len(extracted.lines) == extracted.line_count_reported
            and all(getattr(extracted, key) is not None for key in SUMMARY))


class OfflineExtractor:
    source = SOURCE

    def extract(self, account_id, month, page_images):
        """Read again only if a reading is incomplete.

        A complete reading is accepted without looking at the arithmetic, so a statement that
        doesn't add up can't be re-read until it passes. The checks stay the only judge of that.
        """
        first = None
        for settings in ocr.READINGS:
            extracted = parse_pages([ocr.read(image, **settings) for image in page_images])
            first = first or extracted
            if is_complete(extracted):
                return extracted
        return first
