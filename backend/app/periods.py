"""Billing-period helpers. A period is a 'YYYY-MM' string."""

from __future__ import annotations

import calendar
import datetime as dt
import re

from .errors import DomainError

_PERIOD = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def validate_period(period: str) -> str:
    if not isinstance(period, str) or not _PERIOD.match(period):
        raise DomainError("invalid_period", f"Invalid month '{period}'. Use YYYY-MM.")
    return period


def parse_period(period: str) -> tuple[int, int]:
    validate_period(period)
    y, m = period.split("-")
    return int(y), int(m)


def period_of(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


# Tests pin "today" through this hook so date-dependent rules are deterministic.
_today_override: dt.date | None = None


def today() -> dt.date:
    return _today_override or dt.date.today()


def current_period() -> str:
    return period_of(today())


def first_day(period: str) -> dt.date:
    y, m = parse_period(period)
    return dt.date(y, m, 1)


def last_day(period: str) -> dt.date:
    y, m = parse_period(period)
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def days_in(period: str) -> int:
    y, m = parse_period(period)
    return calendar.monthrange(y, m)[1]


def add_months(period: str, n: int) -> str:
    y, m = parse_period(period)
    idx = y * 12 + (m - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def iter_periods(start: str, end: str):
    p = start
    while p <= end:
        yield p
        p = add_months(p, 1)


def due_date_for(period: str, due_day: int) -> dt.date:
    """Due day clamped to the month's length (31 -> 30 Apr / 28 Feb)."""
    y, m = parse_period(period)
    return dt.date(y, m, min(due_day, calendar.monthrange(y, m)[1]))


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def label(period: str) -> str:
    """'2026-11' -> 'Nov 2026' for messages shown to people."""
    y, m = parse_period(period)
    return f"{_MONTHS[m - 1]} {y}"
