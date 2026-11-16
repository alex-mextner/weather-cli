"""`get_weather()`: providers + cache + deadline in one call.

Accessed via: the CLI and the voice host before `say()`.

Assumptions: a voice turn has ~1.5 s of patience, so every provider shares one hard timeout
(default 1.2 s) and runs concurrently. A fresh cache hit (default 10 min) does no network at all;
when every provider fails the last cached bundle is returned (up to 12 h old) and `origin` says
`stale`, so the caller can still answer instead of going silent. The timeout is also the overall
wall-clock deadline: a provider that has not answered by then counts as failed, whatever its
socket is doing.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .cache import Store, bundle_key, read_bundle, write_bundle
from .providers.home_assistant import HaConnection, fetch_home_assistant
from .providers.json import Fetcher, Outcome, ProviderError, gather
from .providers.merge import merge_bundles
from .providers.open_meteo import fetch_open_meteo
from .types import Bundle, Place

DEFAULT_TIMEOUT_MS = 1200
DEFAULT_FRESH_MS = 10 * 60_000
DEFAULT_MAX_STALE_MS = 12 * 3_600_000
# A cache entry stamped further ahead than this is clock skew, not freshness: treat it as a miss.
MAX_CLOCK_SKEW_MS = 5 * 60_000

WeatherOrigin = Literal["cache", "live", "stale"]


@dataclass(frozen=True, slots=True)
class WeatherResult:
    bundle: Bundle
    origin: WeatherOrigin
    # One line per provider that failed.
    errors: list[str] = field(default_factory=list)


def _failure_line(name: str, outcome: Outcome) -> str:
    error = outcome.error
    if isinstance(error, TimeoutError):
        return f"{name}: request failed (deadline exceeded)"
    return str(error) if isinstance(error, ProviderError) else f"{name}: {error}"


def _fetch_all(
    tasks: dict[str, Callable[[], Bundle]], deadline_s: float
) -> tuple[dict[str, Bundle], list[str]]:
    names = list(tasks)
    outcomes = gather([tasks[name] for name in names], deadline_s)
    bundles: dict[str, Bundle] = {}
    errors: list[str] = []
    for name, outcome in zip(names, outcomes, strict=True):
        if outcome.error is not None:
            errors.append(_failure_line(name, outcome))
        elif isinstance(outcome.value, Bundle):
            bundles[name] = outcome.value
    return bundles, errors


def _from_cache(
    cached: Bundle | None, moment: float, fresh: float, offline: bool, any_provider: bool
) -> WeatherResult | None:
    """The cached answer when no request is wanted; None means "go and fetch"."""
    if offline:
        if cached is not None:
            return WeatherResult(cached, "cache")
        if not any_provider:
            return _offline_answer(None, moment, None, [])
        raise RuntimeError("no weather data: offline and nothing cached")
    if cached is not None and -MAX_CLOCK_SKEW_MS <= moment - cached.fetched_at < fresh:
        return WeatherResult(cached, "cache")
    return None


def _providers_wanted(ha: HaConnection | None, open_meteo: bool) -> set[str]:
    return ({"home-assistant"} if ha is not None else set()) | (
        {"open-meteo"} if open_meteo else set()
    )


def _usable(cached: Bundle | None, place: Place, wanted: set[str], offline: bool) -> Bundle | None:
    """The cached bundle if it may answer this request. It must come from providers now asked for
    and be built for the same zone (its own `place.tz` drives every local-time phrase). Offline,
    or with no provider to ask, whatever is cached is the only answer there can be."""
    if cached is None or offline or not wanted:
        return cached
    return cached if set(cached.sources) <= wanted and cached.place.tz == place.tz else None


def _remember(store: Store | None, key: str, bundle: Bundle) -> None:
    """Persist a live bundle unless it is a failed fetch in disguise (no forecast at all, or a
    forecast request that failed): that must never be served later as fresh."""
    if store is not None and not bundle.forecast_missing and (bundle.hourly or bundle.daily):
        write_bundle(store, key, bundle)


def get_weather(
    *,
    place: Place,
    ha: HaConnection | None = None,
    open_meteo: bool = True,
    store: Store | None = None,
    timeout_ms: float | None = None,
    fresh_ms: float | None = None,
    max_stale_ms: float | None = None,
    now: float | None = None,
    offline: bool = False,
    fetch_impl: Fetcher | None = None,
) -> WeatherResult:
    """`offline` answers from the cache only, of any age: no request is made, ever."""
    moment = int(time.time() * 1000) if now is None else now
    key = bundle_key(place)
    cached = read_bundle(store, key) if store is not None else None
    fresh = DEFAULT_FRESH_MS if fresh_ms is None else fresh_ms
    wanted = _providers_wanted(ha, open_meteo)
    cached = _usable(cached, place, wanted, offline)
    answer = _from_cache(cached, moment, fresh, offline, bool(wanted))
    if answer is not None:
        return answer

    timeout = DEFAULT_TIMEOUT_MS if timeout_ms is None else timeout_ms
    request = {"timeout_ms": timeout, "fetch_impl": fetch_impl, "fetched_at": moment}
    tasks: dict[str, Callable[[], Bundle]] = {}
    if ha:
        tasks["home-assistant"] = lambda: fetch_home_assistant(ha, place, **request)
    if open_meteo:
        tasks["open-meteo"] = lambda: fetch_open_meteo(place, **request)
    bundles, errors = _fetch_all(tasks, timeout / 1000)

    primary = bundles.get("home-assistant")
    secondary = bundles.get("open-meteo")
    if primary is None and secondary is None:
        return _offline_answer(cached, moment, max_stale_ms, errors)

    bundle = merge_bundles(primary, secondary, moment)
    if primary is not None and primary.forecast_missing:
        errors.append("home-assistant: forecast unavailable")
    _remember(store, key, bundle)
    return WeatherResult(bundle, "live", errors)


def _offline_answer(
    cached: Bundle | None, now: float, max_stale_ms: float | None, errors: list[str]
) -> WeatherResult:
    max_stale = DEFAULT_MAX_STALE_MS if max_stale_ms is None else max_stale_ms
    if cached is not None and -MAX_CLOCK_SKEW_MS <= now - cached.fetched_at <= max_stale:
        return WeatherResult(cached, "stale", errors)
    reason = "; ".join(errors) or "no provider configured"
    raise RuntimeError(f"no weather data: {reason}")
