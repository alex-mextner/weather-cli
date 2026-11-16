"""Builders for deterministic weather bundles used across the test suite.

Accessed via: every decision/compose test. Times are wall-clock in Europe/Belgrade so a test
reads like a real moment («21:00 on 9 November»). Overrides are dicts keyed by the snake_case
field names.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from weather_cli.local_time import LocalParts, add_days, date_to_utc_ms, local_date, local_parts
from weather_cli.thresholds import DEFAULT_THRESHOLDS
from weather_cli.types import Bundle, Climate, ClimateSeries, Condition, Day, Moment, Place

TZ = "Europe/Belgrade"
PLACE = Place(lat=44.8, lon=20.48, tz=TZ)
T = DEFAULT_THRESHOLDS
HOUR = 3_600_000

Overrides = dict[str, Any]


def _wall_clock_ms(p: LocalParts) -> int:
    return (
        date_to_utc_ms(f"{p.year}-{p.month:02d}-{p.day:02d}")
        + p.hour * 3_600_000
        + p.minute * 60_000
    )


def zoned_time_to_ms(tz: str, date: str, hour: int = 0, minute: int = 0) -> int:
    """Epoch ms of an ordinary wall-clock time in a zone (fixed-point iteration on the offset).

    Test helper only: DST gaps and overlaps are not resolved, so avoid those wall times.
    """
    wall = date_to_utc_ms(date) + hour * 3_600_000 + minute * 60_000
    guess = wall
    for _ in range(3):
        delta = wall - _wall_clock_ms(local_parts(guess, tz))
        if delta == 0:
            break
        guess += delta
    return guess


def at(date: str, hour: int, minute: int = 0) -> int:
    return zoned_time_to_ms(TZ, date, hour, minute)


def moment(time: float, **over: Any) -> Moment:
    return Moment(**{"time": time, "temp": 10, "condition": "cloudy", **over})


def hours(now: float, shape: Callable[[int, float], Overrides] | None = None) -> list[Moment]:
    """Forecast hours from one hour before `now` (floored to the hour) for 48 hours."""
    first = (now // HOUR) * HOUR - HOUR
    result = []
    for i in range(49):
        start = first + i * HOUR
        over = shape(i, start) if shape else {}
        result.append(moment(start, **{"precip": 0, **over}))
    return result


def day(date: str, **over: Any) -> Day:
    return Day(
        **{"date": date, "min": 5, "max": 12, "condition": "cloudy", "precip": 0, "snow": 0, **over}
    )


def days(now: float, shape: Callable[[int], Overrides] | None = None) -> list[Day]:
    """Eight days from the local date of `now`; `shape(offset)` overrides each."""
    today = local_date(now, TZ)
    return [day(add_days(today, offset), **(shape(offset) if shape else {})) for offset in range(8)]


def bundle(
    *,
    now: float,
    current: Overrides | None = None,
    hour: Callable[[int, float], Overrides] | None = None,
    day: Callable[[int], Overrides] | None = None,
) -> Bundle:
    current_over = current or {}

    def hourly_shape(index: int, start: float) -> Overrides:
        # Forecast hours hold the current air temperature unless a test shapes them otherwise.
        return {"temp": current_over.get("temp", 10), **(hour(index, start) if hour else {})}

    return Bundle(
        place=PLACE,
        fetched_at=now,
        current=moment(now, **{"precip": 0, **current_over}),
        hourly=hours(now, hourly_shape),
        daily=days(now, day),
        sources=["open-meteo"],
    )


def _flat(value: float) -> list[list[float]]:
    return [[value for _ in range(24)] for _ in range(12)]


def series(mean: float, sd: float) -> ClimateSeries:
    return ClimateSeries(mean=_flat(mean), sd=_flat(sd))


def climate(
    humidity_mean: float,
    humidity_sd: float,
    temp_mean: float = 10,
    temp_sd: float = 4,
) -> Climate:
    """Norms with the same mean/sd for every month and hour."""
    return Climate(
        place=PLACE,
        years=(2015, 2024),
        humidity=series(humidity_mean, humidity_sd),
        temp=series(temp_mean, temp_sd),
        built_at=0,
    )


_MASK = 0xFFFFFFFF


def _imul(x: int, y: int) -> int:
    return (x * y) & _MASK


def rng(seed: int) -> Callable[[], float]:
    """A tiny seeded generator (mulberry32) keeps the property test reproducible."""
    state = seed & _MASK

    def random() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & _MASK
        t = _imul(state ^ (state >> 15), 1 | state)
        t = ((t + _imul(t ^ (t >> 7), 61 | t)) ^ t) & _MASK
        return ((t ^ (t >> 14)) & _MASK) / 4294967296

    return random


CONDITIONS: list[Condition] = [
    "clear",
    "partly-cloudy",
    "cloudy",
    "fog",
    "drizzle",
    "rain",
    "heavy-rain",
    "thunderstorm",
    "snow",
    "heavy-snow",
    "sleet",
    "hail",
]
