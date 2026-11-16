"""Decisions about the current air: the spoken temperature, the feels-like clause, the sky.

Accessed via: `say()` for the "now" mode (compose.py) and the `explain` command.

Assumptions: the feels-like gap is judged on WHOLE spoken degrees (7 vs 1 is a 6° gap), so
a listener never hears «7, ощущается как 1» for a rule that saw 5.6. Boundary: gap ≥ feels_gap
fires.
"""

from __future__ import annotations

from weather_cli.conditions import PrecipNow, precip_kind_of
from weather_cli.providers.json import js_number
from weather_cli.ru import degrees, signed, to_fixed
from weather_cli.thresholds import Thresholds
from weather_cli.types import Condition, Decision, Moment
from weather_cli.units import apparent_temperature, spoken


def describe_temperature(current: Moment) -> Decision:
    value = spoken(current.temp)
    return Decision(
        id="temperature",
        fired=True,
        phrase=degrees(value),
        weight=100,
        why=f"air {to_fixed(current.temp, 1)}° → spoken {js_number(value)}",
    )


def feels_like(moment: Moment) -> float | None:
    """The provider's apparent temperature, else Steadman from temp/humidity/wind, else unknown."""
    if moment.feels is not None:
        return moment.feels
    if moment.humidity is not None and moment.wind is not None:
        return apparent_temperature(moment.temp, moment.humidity, moment.wind)
    return None


def _feels_phrase(current: Moment, air: float, felt: float, thresholds: Thresholds) -> str:
    gap = felt - air
    wind = current.wind or 0
    if gap < 0 and wind >= thresholds.wind.strong:
        return f"дует — ощущается как {signed(felt)}"
    if gap < 0 and wind >= thresholds.wind.breezy:
        return f"на ветру ощущается как {signed(felt)}"
    if gap > 0 and (current.humidity or 0) >= 60 and air >= 22:
        return f"из-за влажности ощущается как {signed(felt)}"
    return f"ощущается как {signed(felt)}"


def describe_feels(current: Moment, thresholds: Thresholds) -> Decision:
    feels = feels_like(current)
    if feels is None:
        return Decision(
            id="feels",
            fired=False,
            phrase=None,
            weight=70,
            why="no apparent temperature and not enough data to compute it",
        )
    air = spoken(current.temp)
    felt = spoken(feels)
    gap = felt - air
    why = (
        f"air {js_number(air)}°, feels {js_number(felt)}° → gap {js_number(gap)}° "
        f"(needs ≥ {js_number(thresholds.feels_gap)}° either way)"
    )
    if abs(gap) < thresholds.feels_gap:
        return Decision(id="feels", fired=False, phrase=None, weight=70, why=why)
    phrase = _feels_phrase(current, air, felt, thresholds)
    return Decision(id="feels", fired=True, phrase=phrase, weight=70, why=why)


_SKY: dict[Condition, str] = {
    "clear": "ясно",
    "partly-cloudy": "облачно с прояснениями",
    "cloudy": "пасмурно",
    "fog": "туман",
    "drizzle": "моросит",
    "heavy-rain": "льёт сильный дождь",
    "thunderstorm": "гроза",
    "heavy-snow": "сильный снегопад",
    "sleet": "мокрый снег",
    "hail": "град",
}


def _rain_phrase(mm: float) -> str:
    if mm >= 4:
        return "сильный дождь"
    return "небольшой дождь" if 0 < mm < 1 else "идёт дождь"


def _sky_phrase(current: Moment) -> str:
    mm = current.precip or 0
    if current.condition == "rain":
        return _rain_phrase(mm)
    if current.condition == "snow":
        return "идёт сильный снег" if mm >= 2 else "идёт снег"
    return _SKY[current.condition]


def describe_sky(current: Moment, precipitation: PrecipNow | None = None) -> Decision:
    why = f"condition {current.condition} ({precip_kind_of(current.condition)})"
    note = precipitation.note() if precipitation else ""
    return Decision(
        id="sky",
        fired=True,
        phrase=_sky_phrase(current),
        weight=90,
        why=f"{why}; {note}" if note else why,
    )
