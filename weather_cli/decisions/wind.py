"""Wind as an umbrella question: silent when it does not matter, a category when it does.

Accessed via: `say()` (now mode), the lookahead decision (wind rising) and the umbrella answer.

Assumptions: speeds in m/s (provider boundary converts km/h). Sustained wind and gusts are
judged separately and the higher category wins, so a calm hour with 12 m/s gusts is not "calm".
The raw number is never spoken.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from weather_cli.ru import to_fixed
from weather_cli.thresholds import Thresholds
from weather_cli.types import Decision

WindLevel = Literal["none", "breezy", "strong", "storm"]

_ORDER: tuple[WindLevel, ...] = ("none", "breezy", "strong", "storm")


def wind_level_rank(level: WindLevel) -> int:
    return _ORDER.index(level)


def wind_level(wind: float | None, gust: float | None, thresholds: Thresholds) -> WindLevel:
    t = thresholds.wind
    sustained = wind or 0
    gusts = gust or 0
    if sustained >= t.storm or gusts >= t.gust_storm:
        return "storm"
    if sustained >= t.strong or gusts >= t.gust_strong:
        return "strong"
    if sustained >= t.breezy or gusts >= t.gust_breezy:
        return "breezy"
    return "none"


@dataclass(frozen=True, slots=True)
class WindInput:
    # Precipitation falls (or is about to): wind then decides whether an umbrella survives.
    wet: bool
    wind: float | None = None
    gust: float | None = None


def _breezy(wet: bool, gust_driven: bool) -> tuple[str, int]:
    if wet:
        return "ветрено, зонт придётся держать крепко", 80
    return ("порывистый ветер" if gust_driven else "ветрено"), 35


def _strong(wet: bool, gust_driven: bool) -> tuple[str, int]:
    if wet:
        return "сильный ветер, зонт может вывернуть", 88
    return ("сильные порывы ветра" if gust_driven else "сильный ветер"), 60


def _storm(wet: bool) -> tuple[str, int]:
    phrase = "штормовой ветер, зонт унесёт" if wet else "штормовой ветер, на улице опасно"
    return phrase, 92


def _phrase_for(level: WindLevel, wet: bool, gust_driven: bool) -> tuple[str, int]:
    if level == "breezy":
        return _breezy(wet, gust_driven)
    if level == "strong":
        return _strong(wet, gust_driven)
    return _storm(wet)


def describe_wind(inp: WindInput, thresholds: Thresholds) -> Decision:
    level = wind_level(inp.wind, inp.gust, thresholds)
    sustained = to_fixed(inp.wind or 0, 1)
    gusts = to_fixed(inp.gust or 0, 1)
    wet_note = ", wet" if inp.wet else ""
    why = f"wind {sustained} m/s, gust {gusts} m/s → {level}{wet_note}"
    if level == "none":
        return Decision(id="wind", fired=False, phrase=None, weight=80, why=why)
    gust_driven = wind_level(inp.wind, None, thresholds) != level
    phrase, weight = _phrase_for(level, inp.wet, gust_driven)
    return Decision(id="wind", fired=True, phrase=phrase, weight=weight, why=why)
