"""Merge two provider bundles: the primary leads, the secondary fills what it lacks.

Accessed via: `get_weather()` after both providers answered (or one failed).

Assumptions: the primary (Home Assistant when configured) owns current values, hours and days it
can supply; the secondary only adds optional fields (gusts, probability, snowfall) and horizon
beyond the primary's last day. Apparent temperature from the secondary is applied as an OFFSET to
the primary's air temperature, so a 1.5° disagreement between sources never produces a fake
"feels-like" gap.
"""

from __future__ import annotations

from dataclasses import replace

from ..types import Bundle, Day, Moment, SourceName

# Primary current data older than this is replaced by the secondary's.
CURRENT_MAX_AGE_MS = 90 * 60_000
# Hours/days needed before the primary's series is trusted over the secondary's.
MIN_HOURS = 6
MIN_DAYS = 3


def _fill_moment(target: Moment, source: Moment | None) -> Moment:
    if source is None:
        return target
    fills: dict[str, float] = {}
    if target.feels is None and source.feels is not None:
        fills["feels"] = target.temp + (source.feels - source.temp)
    for name in ("gust", "precip_prob", "humidity", "wind", "precip"):
        if getattr(target, name) is None and getattr(source, name) is not None:
            fills[name] = getattr(source, name)
    return replace(target, **fills)


def _fill_day(target: Day, source: Day | None) -> Day:
    if source is None:
        return target
    fills: dict[str, float] = {}
    if target.snow == 0 and source.snow > 0:
        fills["snow"] = source.snow
    for name in ("precip_prob", "gust_max", "wind_max"):
        if getattr(target, name) is None and getattr(source, name) is not None:
            fills[name] = getattr(source, name)
    return replace(target, **fills)


def _merge_current(primary: Bundle, secondary: Bundle, now: float) -> Moment:
    if now - primary.current.time <= CURRENT_MAX_AGE_MS:
        return _fill_moment(primary.current, secondary.current)
    return secondary.current


def _merge_current_source(primary: Bundle, secondary: Bundle, now: float) -> SourceName | None:
    leader = primary if now - primary.current.time <= CURRENT_MAX_AGE_MS else secondary
    if leader.current_source is not None:
        return leader.current_source
    return leader.sources[0] if len(leader.sources) == 1 else None


def _merge_hourly(primary: Bundle, secondary: Bundle) -> list[Moment]:
    if len(primary.hourly) < MIN_HOURS:
        return secondary.hourly
    by_time = {m.time: m for m in secondary.hourly}
    return [_fill_moment(m, by_time.get(m.time)) for m in primary.hourly]


def _merge_daily(primary: Bundle, secondary: Bundle) -> list[Day]:
    if len(primary.daily) < MIN_DAYS:
        return secondary.daily
    by_date = {d.date: d for d in secondary.daily}
    last_date = primary.daily[-1].date
    return [_fill_day(d, by_date.get(d.date)) for d in primary.daily] + [
        d for d in secondary.daily if d.date > last_date
    ]


def merge_bundles(primary: Bundle | None, secondary: Bundle | None, now: float) -> Bundle:
    if primary is None:
        if secondary is None:
            raise ValueError("nothing to merge")
        return secondary
    if secondary is None:
        return primary
    return Bundle(
        place=primary.place,
        fetched_at=max(primary.fetched_at, secondary.fetched_at),
        current=_merge_current(primary, secondary, now),
        hourly=_merge_hourly(primary, secondary),
        daily=_merge_daily(primary, secondary),
        sources=list(dict.fromkeys([*primary.sources, *secondary.sources])),
        current_source=_merge_current_source(primary, secondary, now),
    )
