"""Number conversions, JS-compatible number helpers and the one physical formula the library owns.

Accessed via: providers (unit boundary), the temperature decision (rounding), the CLI (`--at`,
`--timeout`) and the JSON writers.

Assumptions: spoken temperatures are whole degrees and `-0` never reaches a listener, so every
spoken value goes through `spoken()`. The JS helpers (`js_round`, `parse_js_number`,
`parse_iso_ms`) exist because Python's `round`, `float` and `fromisoformat` disagree with
`Math.round`, `Number` and `Date.parse` in edge cases that change spoken output or exit codes.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# The span `datetime` holds is year 1..9999 UTC; two days are kept clear at each end so that
# converting to any local zone (offsets reach ±14 h) can never overflow.
_EDGE_MS = 2 * 86_400_000
MIN_EPOCH_MS = -62_135_596_800_000 + _EDGE_MS
MAX_EPOCH_MS = 253_402_300_799_999 - _EDGE_MS


def is_plausible_instant(ms: float) -> bool:
    """An epoch-ms value every date/local-time conversion in this library can handle."""
    return MIN_EPOCH_MS <= ms <= MAX_EPOCH_MS


def js_round(x: float) -> int:
    """`Math.round`: halves go toward +infinity (Python's `round` is banker's rounding)."""
    return math.floor(x + 0.5)


def spoken(value: float) -> int:
    """Whole degrees as spoken: rounded half away from zero, `-0` collapsed to `0`."""
    sign = (value > 0) - (value < 0)
    rounded = sign * js_round(abs(value))
    return 0 if rounded == 0 else rounded


def kmh_to_ms(kmh: float) -> float:
    return kmh / 3.6


def mph_to_ms(mph: float) -> float:
    return mph * 0.44704


def knots_to_ms(knots: float) -> float:
    return knots * 0.514444


def fahrenheit_to_celsius(f: float) -> float:
    return ((f - 32) * 5) / 9


def apparent_temperature(temp: float, humidity: float, wind: float) -> float:
    """Apparent temperature (Steadman, as used by the Australian Bureau of Meteorology):
    AT = T + 0.33·e − 0.70·ws − 4.00, e = rh/100 · 6.105 · exp(17.27·T / (237.7 + T)).
    Used when a provider (Home Assistant) gives no apparent temperature of its own.
    `wind` in m/s, `humidity` in %.
    """
    vapour = (humidity / 100) * 6.105 * math.exp((17.27 * temp) / (237.7 + temp))
    return temp + 0.33 * vapour - 0.7 * wind - 4


_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_RADIX = {
    "0x": (16, re.compile(r"[0-9a-fA-F]+")),
    "0o": (8, re.compile(r"[0-7]+")),
    "0b": (2, re.compile(r"[01]+")),
}


def parse_js_number(text: str) -> float:
    """`Number(text)` for strings: blank is 0, radix prefixes work, anything odd is NaN."""
    stripped = text.strip()
    if stripped == "":
        return 0.0
    if stripped[:2].lower() in _RADIX:
        base, digits = _RADIX[stripped[:2].lower()]
        if digits.fullmatch(stripped[2:]) is None:
            return math.nan
        try:
            return float(int(stripped[2:], base))
        except OverflowError:  # more digits than a double holds: Infinity, as in JS
            return math.inf
    if _DECIMAL.fullmatch(stripped):
        return float(stripped)
    infinities = {"Infinity": math.inf, "+Infinity": math.inf, "-Infinity": -math.inf}
    return infinities.get(stripped, math.nan)


_ISO = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})"
    r"(?:[T ]([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.([0-9]+))?)?)?"
    r"(Z|[+-][0-9]{2}:?[0-9]{2})?"
)


def _offset(text: str) -> timezone | None:
    """A `+hh:mm` offset as JavaScript reads it: minutes below 60, hours below 24, else invalid."""
    sign = -1 if text[0] == "-" else 1
    digits = text[1:].replace(":", "")
    hours, minutes = int(digits[:2]), int(digits[2:])
    if hours >= 24 or minutes >= 60:
        return None
    return timezone(timedelta(minutes=sign * (hours * 60 + minutes)))


def _placed(naive: datetime, zone: str | None, date_only: bool) -> datetime | None:
    """`naive` in its stated offset; a date alone is UTC, a time alone the machine's zone."""
    if zone is not None:
        offset = timezone.utc if zone == "Z" else _offset(zone)
        return None if offset is None else naive.replace(tzinfo=offset)
    return naive.replace(tzinfo=timezone.utc) if date_only else naive.astimezone()


def parse_iso_ms(text: str) -> int | None:
    """`Date.parse` for ISO-8601 strings, epoch ms or None. A date without time is UTC, a time
    without an offset is the machine's local time, exactly as in JavaScript."""
    match = _ISO.fullmatch(text)
    if match is None:
        return None
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    try:
        naive = datetime(
            int(year), int(month), int(day), int(hour or 0), int(minute or 0), int(second or 0)
        )
    except ValueError:
        return None
    millis = int((fraction or "0")[:3].ljust(3, "0"))
    try:
        moment = _placed(naive, zone, hour is None)
    except (OverflowError, OSError):  # the very edge of the calendar, in the local zone
        return None
    if moment is None:
        return None
    result = (moment - _EPOCH) // timedelta(milliseconds=1) + millis
    return result if is_plausible_instant(result) else None
