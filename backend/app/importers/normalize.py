"""Shared normalization primitives for typed source adapters.

Money is parsed with the exact string arithmetic in ``app.domain.money``;
timestamps must be strict ``YYYY-MM-DDTHH:MM:SSZ`` UTC; dates must be strict
``YYYY-MM-DD``. Anything else is a quarantine condition, never a silent
default and never a float.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from hashlib import sha256

from app.domain.enums import Currency
from app.domain.money import Paise, paise_from_decimal_rupees

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_DATE_FORMAT = "%Y-%m-%d"


class FieldError(ValueError):
    """A single field failed normalization; the caller quarantines the row."""


def parse_timestamp(text: str) -> datetime:
    """Parse a strict UTC timestamp; raise FieldError on any deviation."""
    try:
        parsed = datetime.strptime(text.strip(), _TS_FORMAT)
    except ValueError as exc:
        raise FieldError(f"invalid UTC timestamp {text!r}") from exc
    return parsed


def format_timestamp(moment: datetime) -> str:
    """Render a UTC datetime back to the canonical wire format."""
    return moment.strftime(_TS_FORMAT)


def parse_date(text: str) -> date:
    """Parse a strict calendar date; raise FieldError on any deviation."""
    try:
        parsed = datetime.strptime(text.strip(), _DATE_FORMAT)
    except ValueError as exc:
        raise FieldError(f"invalid date {text!r}") from exc
    return parsed.date()


def format_date(value: date) -> str:
    return value.strftime(_DATE_FORMAT)


def parse_paise(text: str) -> Paise:
    """Parse a decimal rupee string into paise; FieldError on malformed text."""
    from app.domain.money import MoneyError

    try:
        return paise_from_decimal_rupees(text)
    except MoneyError as exc:
        raise FieldError(str(exc)) from exc


def normalize_currency(text: str) -> Currency:
    """Only INR is supported; anything else quarantines the row."""
    candidate = text.strip()
    try:
        return Currency(candidate)
    except ValueError as exc:
        raise FieldError(f"unsupported currency {text!r}") from exc


def require_text(row: dict[str, str], column: str) -> str:
    """Required non-empty trimmed field."""
    value = row.get(column)
    if value is None or not value.strip():
        raise FieldError(f"missing required field {column}")
    return value.strip()


def optional_text(row: dict[str, str], column: str) -> str | None:
    """Optional field; empty or absent becomes None."""
    value = row.get(column)
    if value is None or not value.strip():
        return None
    return value.strip()


def require_status(row: dict[str, str], column: str, allowed: frozenset[str]) -> str:
    """Status whitelist per source type; unknown statuses quarantine the row."""
    value = require_text(row, column)
    if value not in allowed:
        raise FieldError(f"unknown status {value!r} for {column}")
    return value


def content_hash(columns: tuple[str, ...], row: dict[str, str]) -> str:
    """SHA-256 over canonical JSON of ordered ``[column, raw_value]`` pairs.

    Byte-identical rows hash identically regardless of file position, so the
    value is stable under row reordering and duplicate-delivery detection.
    """
    payload = [[column, row.get(column, "")] for column in columns]
    canonical = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest()
