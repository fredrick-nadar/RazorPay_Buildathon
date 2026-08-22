"""Integer-paise money primitives.

INR amounts are signed integer paise everywhere in ARGUS. Binary floating
point is never accepted as money: any float (including integral values such
as 100.0) and any bool is rejected with a clear error. Decimal rupee strings
are converted with exact string arithmetic only; no float ever participates.
"""

from __future__ import annotations

import re
from typing import NewType

Paise = NewType("Paise", int)

# Guardrail chosen so every downstream system (SQLite int64, JSON consumers,
# double-precision reporters) can round-trip values without loss.
MAX_ABS_PAISE = 10**15

_RUPEE_TEXT_RE = re.compile(r"^-?\d+(\.\d{1,2})?$")


class MoneyError(ValueError):
    """Raised when a value cannot be represented as exact integer paise."""


def require_paise(value: int) -> Paise:
    """Validate an integer paise amount; rejects floats, bools, and oversized values."""
    if isinstance(value, bool):
        raise MoneyError("paise amounts must be integers, got bool")
    if not isinstance(value, int):
        raise MoneyError(
            "paise amounts must be integers, got "
            f"{type(value).__name__}: {value!r}; parse decimal strings with "
            "paise_from_decimal_rupees() instead of float arithmetic"
        )
    if abs(value) > MAX_ABS_PAISE:
        raise MoneyError(f"|{value}| exceeds the maximum representable paise ({MAX_ABS_PAISE})")
    return Paise(value)


def paise_from_decimal_rupees(text: str) -> Paise:
    """Parse a decimal rupee string such as ``"-1234.56"`` into integer paise exactly.

    Accepts optional surrounding whitespace, an optional sign, integer digits,
    and at most two fractional digits. Commas, exponent notation, and
    non-string input are rejected. Never uses float arithmetic.
    """
    if not isinstance(text, str):
        raise MoneyError(f"decimal rupee input must be a str, got {type(text).__name__}")
    stripped = text.strip()
    if not _RUPEE_TEXT_RE.fullmatch(stripped):
        raise MoneyError(
            f"invalid decimal rupee amount: {text!r}; expected forms like "
            "'1234', '1234.5', '1234.56'"
        )
    negative = stripped.startswith("-")
    digits = stripped[1:] if negative else stripped
    rupees, _, fraction = digits.partition(".")
    fraction_padded = (fraction + "00")[:2]
    total = int(rupees) * 100 + int(fraction_padded)
    return Paise(-total if negative else total)


def format_paise(amount: Paise, *, currency: str = "INR") -> str:
    """Render paise as a plain signed decimal string such as ``"-1234.56 INR"``."""
    checked = require_paise(amount)
    sign = "-" if checked < 0 else ""
    absolute = abs(checked)
    return f"{sign}{absolute // 100}.{absolute % 100:02d} {currency}"


def add_paise(left: Paise, right: Paise) -> Paise:
    """Add two paise amounts with the same overflow guard as require_paise."""
    return require_paise(int(left) + int(right))
