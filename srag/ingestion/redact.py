# srag/ingestion/redact.py
from __future__ import annotations
import re

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
# Credit cards: 13-19 digit runs (optionally spaced/hyphenated), Luhn-validated
# so arbitrary long numeric IT values (ms epochs, byte counts, serials) survive.
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
# Phones: 3-3-4 groups with REQUIRED separators so bare 10-digit runs (epoch
# timestamps, `date +%s` output) are never matched. Optional country code/parens.
_PHONE = re.compile(
    r"(?<!\d)(?:\+\d{1,3}[-.\s])?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"
)
# SSN form only. A bare \b\d{9}\b rule is deliberately omitted: ticket numbers,
# UIDs, and numeric checksums in IT content are far more common than bare 9-digit
# national IDs, and would be destroyed by it.
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _luhn_valid(value: str) -> bool:
    digits = [int(d) for d in _digits(value)]
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def redact_pii(text: str) -> str:
    text = _EMAIL.sub("[EMAIL REDACTED]", text)
    text = _CARD.sub(
        lambda m: "[CREDIT_CARD REDACTED]" if _luhn_valid(m.group()) else m.group(),
        text,
    )
    text = _PHONE.sub("[PHONE REDACTED]", text)
    text = _SSN.sub("[NATIONAL_ID REDACTED]", text)
    return text
