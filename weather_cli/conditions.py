"""Sky/precipitation vocabulary: provider code mappings and classification helpers.

Accessed via: providers (WMO codes, Home Assistant condition strings) and every decision
that asks "is it wet / snowing / thundering".

Assumptions: Home Assistant's `windy`, `windy-variant` and `exceptional` carry no sky
information, so they map to `partly-cloudy`, the neutral choice.

Is it precipitating NOW? `current_precipitation()` is the only place that decides, by this
precedence:
  (a) a precipitating condition on the current observation wins over a model amount: an
      observation beats a model, and Home Assistant reports no amount while Open-Meteo's 0.0 mm
      for the same hour must not veto it (`decided_by` = "condition");
  (b) otherwise the current amount decides, with the usual `is_wet` rules (`decided_by` =
      "amount");
  (c) forecast hours keep using `is_wet` unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from weather_cli.types import Bundle, Condition, Moment, PrecipKind, SourceName

_WMO: dict[int, Condition] = {
    0: "clear",
    1: "clear",
    2: "partly-cloudy",
    3: "cloudy",
    45: "fog",
    48: "fog",
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    56: "sleet",
    57: "sleet",
    61: "rain",
    63: "rain",
    65: "heavy-rain",
    66: "sleet",
    67: "sleet",
    71: "snow",
    73: "snow",
    75: "heavy-snow",
    77: "snow",
    80: "rain",
    81: "rain",
    82: "heavy-rain",
    85: "snow",
    86: "heavy-snow",
    95: "thunderstorm",
    96: "thunderstorm",
    99: "thunderstorm",
}


def condition_from_wmo(code: int) -> Condition:
    return _WMO.get(code, "cloudy")


_HOME_ASSISTANT: dict[str, Condition] = {
    "clear-night": "clear",
    "sunny": "clear",
    "partlycloudy": "partly-cloudy",
    "cloudy": "cloudy",
    "fog": "fog",
    "hail": "hail",
    "lightning": "thunderstorm",
    "lightning-rainy": "thunderstorm",
    "pouring": "heavy-rain",
    "rainy": "rain",
    "snowy": "snow",
    "snowy-rainy": "sleet",
    "windy": "partly-cloudy",
    "windy-variant": "partly-cloudy",
    "exceptional": "partly-cloudy",
}


def condition_from_home_assistant(state: str) -> Condition | None:
    return _HOME_ASSISTANT.get(state)


_PRECIP_KINDS: dict[Condition, PrecipKind] = {
    "drizzle": "rain",
    "rain": "rain",
    "heavy-rain": "rain",
    "thunderstorm": "rain",
    "hail": "rain",
    "snow": "snow",
    "heavy-snow": "snow",
    "sleet": "mixed",
}


def precip_kind_of(condition: Condition) -> PrecipKind:
    return _PRECIP_KINDS.get(condition, "none")


# Minimum hourly amount that counts as "it is raining" (mm).
WET_MM = 0.2
# Below this probability a forecast hour is not called wet unless the amount is clear.
_WET_PROBABILITY = 40


def is_wet(moment: Moment) -> bool:
    """Is precipitation falling in this moment?

    A current observation with a precipitating condition counts even without an amount
    (Home Assistant gives none); a forecast hour needs an amount and a not-tiny probability.
    """
    mm = moment.precip
    if mm is None:
        return precip_kind_of(moment.condition) != "none"
    if mm < WET_MM:
        return False
    return not (moment.precip_prob is not None and moment.precip_prob < _WET_PROBABILITY and mm < 1)


# What falls when it is wet.
WetKind = Literal["rain", "snow", "mixed"]


def wet_kind(moment: Moment, temp: float | None = None) -> WetKind:
    """What falls (rain/snow/mixed), judged by the moment's condition, else by temperature."""
    from_condition = precip_kind_of(moment.condition)
    if from_condition in ("rain", "snow", "mixed"):
        return from_condition
    # The sky code says "cloudy" but the amount says wet: decide by temperature.
    degrees = moment.temp if temp is None else temp
    if degrees <= 0:
        return "snow"
    if degrees <= 2:
        return "mixed"
    return "rain"


@dataclass(frozen=True, slots=True)
class PrecipNow:
    wet: bool
    kind: WetKind | None
    decided_by: Literal["condition", "amount"]
    source: SourceName | None
    # What the deciding observation said, e.g. "rain, 0.0 mm" or "0.6 mm".
    detail: str
    # An observed condition overrode a smaller model amount (rule a).
    contested: bool

    def note(self) -> str:
        """The `explain` clause naming what decided; empty when nothing is worth saying."""
        if not self.contested and self.source is None:
            return ""
        state = "wet" if self.wet else "dry"
        return (
            f"precipitation now: {state} ({self.decided_by}, {self.source or 'unknown source'}"
            f": {self.detail})"
        )


def _fmt_mm(mm: float | None) -> str:
    return "no amount" if mm is None else f"{mm:g} mm"


def current_precipitation(bundle: Bundle) -> PrecipNow:
    """The single decision of whether it precipitates now (precedence in the module docstring)."""
    current = bundle.current
    mm = current.precip
    observed = precip_kind_of(current.condition) != "none"
    detail = f"{current.condition}, {_fmt_mm(mm)}"
    if observed and (mm is None or mm < WET_MM):
        return PrecipNow(
            True,
            wet_kind(current),
            "condition",
            bundle.current_source,
            detail,
            contested=mm is not None,
        )
    wet = is_wet(current)
    return PrecipNow(
        wet,
        wet_kind(current) if wet else None,
        "amount",
        bundle.current_source,
        detail,
        contested=False,
    )


_ALL_CONDITIONS: tuple[Condition, ...] = get_args(Condition)


def condition_from_string(value: str | None) -> Condition | None:
    return next((condition for condition in _ALL_CONDITIONS if condition == value), None)
