"""Exact money handling. Everything is integer paise; rupee strings are parsed with Decimal."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_CLEAN = re.compile(r"[₹,\s]|INR|Rs\.?", re.IGNORECASE)


class MoneyParseError(ValueError):
    pass


def parse_rupees(value: object) -> int | None:
    """Parse a rupee amount ("12,000.50", "₹ 500", 1200, "1,00,000") into paise.

    Returns None for blank cells. Raises MoneyParseError for anything ambiguous,
    including more than two decimal places.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise MoneyParseError("Boolean is not an amount")
    if isinstance(value, int):
        return value * 100
    if isinstance(value, float):
        # Spreadsheet cells arrive as floats; round to paise via str() repr.
        value = repr(value)
    text = str(value).strip()
    if text in {"", "-", "--"}:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    text = _CLEAN.sub("", text)
    if text.upper().endswith("CR"):
        text = text[:-2]
    elif text.upper().endswith("DR"):
        negative, text = True, text[:-2]
    if text.startswith("-"):
        negative, text = True, text[1:]
    try:
        d = Decimal(text)
    except InvalidOperation as exc:
        raise MoneyParseError(f"Not a valid amount: {value!r}") from exc
    if not d.is_finite():
        raise MoneyParseError(f"Not a valid amount: {value!r}")
    paise = d * 100
    if paise != paise.to_integral_value():
        # Floats like 1234.5600000001 come from spreadsheets; tolerate tiny noise only.
        rounded = paise.quantize(Decimal(1), rounding=ROUND_HALF_UP)
        if abs(paise - rounded) > Decimal("0.0001"):
            raise MoneyParseError(f"Amount has more than two decimal places: {value!r}")
        paise = rounded
    result = int(paise)
    return -result if negative else result


def format_inr(paise: int) -> str:
    """Indian digit grouping: 123456789 paise -> '₹12,34,567.89'."""
    sign = "-" if paise < 0 else ""
    paise = abs(paise)
    rupees, p = divmod(paise, 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return f"{sign}₹{s}" + (f".{p:02d}" if p else "")


def to_rupees_decimal(paise: int) -> Decimal:
    return (Decimal(paise) / 100).quantize(Decimal("0.01"))
