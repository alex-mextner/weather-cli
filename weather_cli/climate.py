"""Climate norms: per local month and hour mean/sd of humidity and temperature, built from
Open-Meteo's ERA5 archive and cached.

Accessed via: `load_climate()` on the hot path (cache read only, never the network) and
`refresh_climate()` from `weather climate update` or a background task of the host.

Assumptions: the archive request is ~2.7 MB and takes seconds, so it NEVER runs inside a voice
answer; the decision functions stay silent about humidity/anomaly until norms exist. The window
is the ten complete calendar years before the current one.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

from .cache import Store, climate_key, read_climate, write_climate
from .local_time import LocalParts, local_parts
from .providers.json import (
    Fetcher,
    JsonValue,
    ProviderError,
    as_array,
    as_number,
    form_encode,
    is_record,
    js_number,
    request_json,
)
from .types import Climate, ClimateSeries, Place
from .units import is_plausible_instant, js_round

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
# Norms older than this are rebuilt.
CLIMATE_MAX_AGE_MS = 180 * 86_400_000
YEARS = 10


def _grid() -> list[list[float]]:
    return [[0.0] * 24 for _ in range(12)]


def _round(value: float) -> float:
    return js_round(value * 100) / 100


class Accumulator:
    def __init__(self) -> None:
        self._count = _grid()
        self._mean = _grid()
        self._m2 = _grid()

    def add(self, month: int, hour: int, value: float) -> None:
        """Welford's online mean/variance per [month][hour]."""
        row = month - 1
        n = self._count[row][hour] + 1
        previous_mean = self._mean[row][hour]
        delta = value - previous_mean
        next_mean = previous_mean + delta / n
        self._count[row][hour] = n
        self._mean[row][hour] = next_mean
        self._m2[row][hour] += delta * (value - next_mean)

    def series(self) -> ClimateSeries:
        return ClimateSeries(
            mean=[[_round(v) for v in row] for row in self._mean],
            sd=[[self._sd(r, h) for h in range(24)] for r in range(12)],
        )

    def _sd(self, row: int, hour: int) -> float:
        n = self._count[row][hour]
        return _round(math.sqrt(self._m2[row][hour] / (n - 1))) if n > 1 else 0.0


def archive_url(place: Place, years: tuple[int, int]) -> str:
    query = form_encode(
        {
            "latitude": js_number(place.lat),
            "longitude": js_number(place.lon),
            "start_date": f"{years[0]}-01-01",
            "end_date": f"{years[1]}-12-31",
            "hourly": "temperature_2m,relative_humidity_2m",
            "timezone": place.tz,
            "timeformat": "unixtime",
        }
    )
    return f"{ARCHIVE}?{query}"


def _at(values: list[float | None], index: int) -> float | None:
    return values[index] if index < len(values) else None


def _add_reading(target: Accumulator, local: LocalParts, value: float | None) -> None:
    if value is not None:
        target.add(local.month, local.hour, value)


def parse_archive(
    json: JsonValue, place: Place, years: tuple[int, int], built_at: float
) -> Climate:
    hourly = json.get("hourly") if is_record(json) else None
    if not is_record(hourly):
        raise ProviderError("open-meteo-archive", "unexpected payload")
    times = [as_number(v) for v in as_array(hourly.get("time"))]
    temps = [as_number(v) for v in as_array(hourly.get("temperature_2m"))]
    humidities = [as_number(v) for v in as_array(hourly.get("relative_humidity_2m"))]
    if not times:
        raise ProviderError("open-meteo-archive", "empty archive")
    temp = Accumulator()
    humidity = Accumulator()
    for i, stamp in enumerate(times):
        if stamp is not None and is_plausible_instant(stamp * 1000):
            local = local_parts(stamp * 1000, place.tz)
            _add_reading(temp, local, _at(temps, i))
            _add_reading(humidity, local, _at(humidities, i))
    return Climate(
        place=place, years=years, humidity=humidity.series(), temp=temp.series(), built_at=built_at
    )


def archive_years(now: float) -> tuple[int, int]:
    """The ten complete years before the one containing `now`."""
    year = (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=now)).year
    return (year - YEARS, year - 1)


def refresh_climate(
    store: Store,
    place: Place,
    *,
    timeout_ms: float,
    now: float | None = None,
    fetch_impl: Fetcher | None = None,
) -> Climate:
    moment = int(time.time() * 1000) if now is None else now
    years = archive_years(moment)
    json = request_json(
        "open-meteo-archive",
        archive_url(place, years),
        timeout_ms=timeout_ms,
        fetch_impl=fetch_impl,
    )
    climate = parse_archive(json, place, years, moment)
    write_climate(store, climate_key(place), climate)
    return climate


def load_climate(store: Store, place: Place) -> Climate | None:
    """Cache read only: the hot path never waits for the network."""
    climate = read_climate(store, climate_key(place))
    # The grid is indexed by local month and hour in the zone it was built for.
    return climate if climate is not None and climate.place.tz == place.tz else None


def climate_is_stale(climate: Climate | None, now: float) -> bool:
    return climate is None or now - climate.built_at > CLIMATE_MAX_AGE_MS
