"""Warm/cold for the month: current air against the same month and hour of the archive.

Accessed via: `say()` (now mode), low priority, mostly seen in `explain`.

Assumptions: silent without cached climate norms; both the z-score and the absolute gap must
pass, so a month with a tiny spread does not make 2° look dramatic.
"""

from __future__ import annotations

from weather_cli.decisions.norms import norm_at
from weather_cli.local_time import local_parts
from weather_cli.providers.json import js_number
from weather_cli.ru import month_genitive, to_fixed
from weather_cli.thresholds import Thresholds
from weather_cli.types import Climate, Decision, Moment


def _silent(why: str) -> Decision:
    return Decision(id="temp-anomaly", fired=False, phrase=None, weight=30, why=why)


def _spoken(phrase: str, why: str) -> Decision:
    return Decision(id="temp-anomaly", fired=True, phrase=phrase, weight=30, why=why)


def describe_temp_anomaly(
    current: Moment, climate: Climate | None, tz: str, thresholds: Thresholds
) -> Decision:
    if climate is None:
        return _silent("no climate norms cached")
    local = local_parts(current.time, tz)
    norm = norm_at(climate.temp, local)
    if norm is None:
        return _silent("norm missing for this month/hour")
    mean, raw_sd = norm
    sd = max(raw_sd, 1)
    gap = current.temp - mean
    z = gap / sd
    a = thresholds.anomaly
    why = (
        f"air {to_fixed(current.temp, 1)}° vs norm {to_fixed(mean, 1)}±{to_fixed(sd, 1)}° "
        f"→ z={to_fixed(z, 2)}, gap {to_fixed(gap, 1)}° "
        f"(needs |z| ≥ {js_number(a.sd)}, |gap| ≥ {js_number(a.gap)}°)"
    )
    month = month_genitive(local.month)
    if z >= a.sd and gap >= a.gap:
        return _spoken(f"для {month} необычно тепло", why)
    if z <= -a.sd and -gap >= a.gap:
        return _spoken(f"для {month} необычно холодно", why)
    return _silent(why)
