"""Every tunable number of the decision functions, in one place.

Accessed via: each decision takes `Thresholds`; `say()` and the CLI pass `DEFAULT_THRESHOLDS`
(overridable with `with_thresholds`).

Assumptions: speeds are m/s; temperature gaps are compared on WHOLE spoken degrees, so what
the listener hears is what the rule saw («7, ощущается как 1» is a 6° gap).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class HumidityThresholds:
    # z-score (against the month/hour norm) beyond which air is "notably" dry or damp.
    sd: float = 1.0
    # ...and at least this many humidity points away from the norm.
    gap: float = 10
    # z-score for the stronger wording.
    strong_sd: float = 1.8


@dataclass(frozen=True, slots=True)
class WindThresholds:
    breezy: float = 6
    strong: float = 9
    storm: float = 13
    gust_breezy: float = 10
    gust_strong: float = 14
    gust_storm: float = 19


@dataclass(frozen=True, slots=True)
class LookaheadThresholds:
    # Minutes ahead that count as "about an hour".
    window_minutes: float = 60
    # Air temperature change within the window that is worth saying, degrees.
    temp_jump: float = 3


@dataclass(frozen=True, slots=True)
class TomorrowThresholds:
    # Tomorrow's daytime high differs from today's by at least this much → said.
    shift: float = 5


@dataclass(frozen=True, slots=True)
class WeekThresholds:
    # Daytime high moves this far from the next two days' level → warming/cooling.
    shift: float = 5
    frost: float = 0
    hard_frost: float = -10
    heat: float = 30
    strong_heat: float = 35
    # Daily precipitation sum (mm) that makes a "wet" day.
    wet_day_mm: float = 1
    # Snowfall (cm) that makes a "snowy" day.
    snow_day_cm: float = 0.5
    # Look at this many days after today.
    horizon_days: int = 7


@dataclass(frozen=True, slots=True)
class AnomalyThresholds:
    sd: float = 1.5
    gap: float = 5


@dataclass(frozen=True, slots=True)
class Thresholds:
    # Feels-like is spoken only if it differs from the air temperature by at least this many
    # degrees.
    feels_gap: float = 6
    humidity: HumidityThresholds = field(default_factory=HumidityThresholds)
    wind: WindThresholds = field(default_factory=WindThresholds)
    lookahead: LookaheadThresholds = field(default_factory=LookaheadThresholds)
    # From this local hour on, a "now" answer also carries tomorrow.
    late_hour: int = 21
    tomorrow: TomorrowThresholds = field(default_factory=TomorrowThresholds)
    week: WeekThresholds = field(default_factory=WeekThresholds)
    anomaly: AnomalyThresholds = field(default_factory=AnomalyThresholds)


DEFAULT_THRESHOLDS = Thresholds()


def _merge(base: _T, overrides: Mapping[str, Any]) -> _T:
    changes = {
        name: _merge(getattr(base, name), value) if isinstance(value, Mapping) else value
        for name, value in overrides.items()
    }
    return dataclasses.replace(base, **changes)  # type: ignore[call-overload]


def with_thresholds(overrides: Mapping[str, Any]) -> Thresholds:
    """Defaults with the given (possibly nested, snake_case) fields replaced."""
    return _merge(DEFAULT_THRESHOLDS, overrides)
