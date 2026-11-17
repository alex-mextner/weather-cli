"""Local-time arithmetic on top of zoneinfo.

Accessed via: every decision that talks about "tonight", "tomorrow", a month norm or a weekday.

Assumptions: epoch milliseconds in, IANA zone names. "Tomorrow" is a calendar step on the
local date, never `ms + 24h` (a DST day is 23 or 25 hours long, and +24h from 23:30 on the
eve of the spring change lands two calendar days ahead).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal
from zoneinfo import ZoneInfo

DAY_MS = 86_400_000
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
_EPOCH_DATE = dt.date(1970, 1, 1)


@dataclass(frozen=True, slots=True)
class LocalParts:
    year: int
    month: int
    day: int
    hour: int
    minute: int
    # 0 = Sunday.
    weekday: int


def local_parts(ms: float, tz: str) -> LocalParts:
    # Like a JS Date, the instant is truncated to whole milliseconds.
    instant = _EPOCH + dt.timedelta(milliseconds=int(ms))
    local = instant.astimezone(ZoneInfo(tz))
    return LocalParts(
        year=local.year,
        month=local.month,
        day=local.day,
        hour=local.hour,
        minute=local.minute,
        weekday=(local.weekday() + 1) % 7,
    )


def _pad(n: int) -> str:
    return str(n).zfill(2)


def _format_date(day: dt.date) -> str:
    return f"{day.year}-{_pad(day.month)}-{_pad(day.day)}"


def local_date(ms: float, tz: str) -> str:
    p = local_parts(ms, tz)
    return f"{p.year}-{_pad(p.month)}-{_pad(p.day)}"


def _parse_date(date: str) -> dt.date:
    year, month, day = (int(part) for part in date.split("-"))
    return dt.date(year, month, day)


def date_to_utc_ms(date: str) -> int:
    return (_parse_date(date) - _EPOCH_DATE).days * DAY_MS


def add_days(date: str, days: int) -> str:
    """Calendar arithmetic on a YYYY-MM-DD string."""
    return _format_date(_parse_date(date) + dt.timedelta(days=days))


def days_between(from_date: str, to_date: str) -> int:
    return (date_to_utc_ms(to_date) - date_to_utc_ms(from_date)) // DAY_MS


def weekday_of(date: str) -> int:
    """0 = Sunday."""
    return (_parse_date(date).weekday() + 1) % 7


DayPart = Literal["night", "morning", "day", "evening"]


def day_part_of(hour: int) -> DayPart:
    if hour < 6 or hour >= 22:
        return "night"
    if hour < 11:
        return "morning"
    if hour < 17:
        return "day"
    return "evening"
