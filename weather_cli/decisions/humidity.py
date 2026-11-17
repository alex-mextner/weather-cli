"""Humidity as a sensation relative to the month's norm — never as a percentage.

Accessed via: `say()` (now mode) and `explain`.

Assumptions: the norm is the mean/sd of the same local month AND hour of day over the
archive years (night air is humid every night: comparing it to a daily mean would call every
evening damp). With no climate loaded the decision stays silent — it never invents a norm.
Damp air is not called out while it rains or fogs (redundant).
"""

from __future__ import annotations

from dataclasses import dataclass

from weather_cli.decisions.norms import norm_at
from weather_cli.local_time import local_parts
from weather_cli.providers.json import js_number
from weather_cli.ru import month_genitive, to_fixed
from weather_cli.thresholds import Thresholds
from weather_cli.types import Climate, Decision
from weather_cli.units import js_round


@dataclass(frozen=True, slots=True)
class HumidityInput:
    humidity: float | None
    climate: Climate | None
    time: float
    tz: str
    # It is raining/snowing/foggy: "damp" would only repeat the obvious.
    wet_or_fog: bool


def _silent(why: str) -> Decision:
    return Decision(id="humidity", fired=False, phrase=None, weight=40, why=why)


def _spoken(phrase: str, why: str) -> Decision:
    return Decision(id="humidity", fired=True, phrase=phrase, weight=40, why=why)


def describe_humidity(inp: HumidityInput, thresholds: Thresholds) -> Decision:
    if inp.humidity is None:
        return _silent("no humidity reading")
    if inp.climate is None:
        return _silent("no climate norms cached (run `weather climate update`)")
    local = local_parts(inp.time, inp.tz)
    norm = norm_at(inp.climate.humidity, local)
    if norm is None:
        return _silent("norm missing for this month/hour")
    mean, raw_sd = norm
    # A degenerate sd would turn rounding noise into a "notable" deviation.
    sd = max(raw_sd, 3)
    gap = inp.humidity - mean
    z = gap / sd
    t = thresholds.humidity
    why = (
        f"humidity {js_number(js_round(inp.humidity))}% vs norm "
        f"{js_number(js_round(mean))}±{js_number(js_round(sd))}% "
        f"(month {local.month}, {local.hour}h) → z={to_fixed(z, 2)}, "
        f"gap {js_number(js_round(gap))} "
        f"(needs |z| ≥ {js_number(t.sd)}, |gap| ≥ {js_number(t.gap)})"
    )
    month = month_genitive(local.month)
    if z <= -t.sd and -gap >= t.gap:
        phrase = f"очень сухо для {month}" if z <= -t.strong_sd else f"суше обычного для {month}"
        return _spoken(phrase, why)
    if z >= t.sd and gap >= t.gap:
        if inp.wet_or_fog:
            return _silent(f"{why}; damp but it rains/fogs")
        phrase = f"очень сыро для {month}" if z >= t.strong_sd else f"влажнее обычного для {month}"
        return _spoken(phrase, why)
    return _silent(why)
