"""Merge two provider bundles: the primary leads, the secondary fills what it lacks.

Accessed via: `get_weather()` after both providers answered (or one failed).

Assumptions: the primary (Home Assistant when configured) owns current values, hours and days it
can supply; the secondary only adds optional fields (gusts, probability, snowfall) and horizon
beyond the primary's last day. Apparent temperature from the secondary is applied as an OFFSET to
the primary's air temperature, so a 1.5° disagreement between sources never produces a fake
"feels-like" gap.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import replace

from ..horizon import GRACE_MS, hours_ahead
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


class _Grid:
    """Hours indexed by time: the one within half an hour of a given instant (the closest), if
    any. Two providers on shifted grids (:00 and :30) describe the same hour. A binary search, so
    a payload full of duplicate timestamps costs no more than a sort."""

    def __init__(self, moments: list[Moment]) -> None:
        self._moments = sorted(moments, key=lambda m: m.time)
        self._times = [m.time for m in self._moments]

    def nearest(self, time: float) -> Moment | None:
        at = bisect_left(self._times, time)
        near = self._moments[max(at - 1, 0) : at + 1]
        best = min(near, key=lambda m: abs(m.time - time), default=None)
        return best if best is not None and abs(best.time - time) <= GRACE_MS else None


def _merge_hourly(primary: Bundle, secondary: Bundle, now: float) -> list[Moment]:
    """The primary's hours when it has enough that still matter (filled from the secondary).

    Otherwise whichever source has more of them ahead leads (the secondary on a tie): its hours
    are filled from the other source's matching hours, and the other's upcoming hours (past the
    horizon too: a cached bundle is read again later)
    that the leader does not cover (after its last one, inside a gap) are added. A rain hour
    outside the leader's coverage, or hidden behind a null field, must not disappear; hours that
    have already passed are neither carried over nor count as coverage.
    """
    # Distinct instants: a payload repeating one hour is not coverage.
    usable = _coverage(primary, now)
    if usable >= MIN_HOURS:
        by_time = {m.time: m for m in secondary.hourly}
        return [_fill_moment(m, by_time.get(m.time)) for m in primary.hourly]
    leader, other = (
        (secondary.hourly, primary.hourly)
        if _coverage(secondary, now) >= usable
        else (primary.hourly, secondary.hourly)
    )
    return _lead_and_fill(leader, other, now)


def _coverage(bundle: Bundle, now: float) -> int:
    return len({m.time for m in hours_ahead(bundle.hourly, now)})


def _lead_and_fill(leader: list[Moment], other: list[Moment], now: float) -> list[Moment]:
    # Beyond the horizon too: a cached bundle outlives the fetch, and its later hours are what the
    # next question (minutes on) will look at.
    ahead = [m for m in other if m.time > now - GRACE_MS]
    matching = _Grid(ahead)
    filled = [_fill_moment(m, matching.nearest(m.time)) for m in leader]
    covering = _Grid([m for m in leader if m.time > now - GRACE_MS])
    extra = [m for m in ahead if covering.nearest(m.time) is None]
    return sorted([*filled, *extra], key=lambda m: m.time) if extra else filled


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
    hourly = _merge_hourly(primary, secondary, now)
    return Bundle(
        place=primary.place,
        fetched_at=max(primary.fetched_at, secondary.fetched_at),
        current=_merge_current(primary, secondary, now),
        hourly=hourly,
        daily=_merge_daily(primary, secondary),
        sources=list(dict.fromkeys([*primary.sources, *secondary.sources])),
        current_source=_merge_current_source(primary, secondary, now),
        # Unknown only when neither source has hours: the other one fills the gap.
        forecast_missing=not hours_ahead(hourly, now),
    )
