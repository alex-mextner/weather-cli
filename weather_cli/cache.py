"""Key/value store seam for cached bundles and climate norms, plus the memory store.

Accessed via: `get_weather()` and the climate helpers; the CLI and the voice host pass a
file-backed store (cache_fs.py), tests pass the memory one.

Assumptions: values are JSON text. A read that fails or returns garbage is a cache miss — the
cache is an optimisation and an offline fallback, never a source of truth. Decoders rebuild every
object field by field instead of asserting the parsed shape. On disk the keys stay camelCase
(`fetchedAt`, `precipProb`) so files written by the TypeScript version keep working.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol, TypeVar

from .conditions import condition_from_string
from .providers.json import (
    JsonRecord,
    JsonValue,
    as_array,
    as_number,
    as_string,
    dumps,
    is_record,
    js_number,
    parse_json,
)
from .types import Bundle, Climate, ClimateSeries, Day, Moment, Place, SourceName
from .units import is_plausible_instant

T = TypeVar("T")


class Store(Protocol):
    def read(self, key: str) -> str | None: ...

    def write(self, key: str, value: str) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self.entries: dict[str, str] = {}

    def read(self, key: str) -> str | None:
        return self.entries.get(key)

    def write(self, key: str, value: str) -> None:
        self.entries[key] = value


SCHEMA = 1


def _key_part(value: float) -> str:
    """`Number.prototype.toFixed(2)`: exact ties round away from zero, not to even."""
    if value == 0:
        return "0.00"
    if abs(value) >= 1e21:
        return js_number(value)
    return format(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def bundle_key(place: Place) -> str:
    return f"bundle-{_key_part(place.lat)}_{_key_part(place.lon)}.json"


def climate_key(place: Place) -> str:
    return f"climate-{_key_part(place.lat)}_{_key_part(place.lon)}.json"


def _is_schema(value: object) -> bool:
    return as_number(value) == SCHEMA


# --- decoders -------------------------------------------------------------------------------


def _decode_moment(value: JsonValue | None) -> Moment | None:
    if not is_record(value):
        return None
    time = as_number(value.get("time"))
    temp = as_number(value.get("temp"))
    condition = condition_from_string(as_string(value.get("condition")))
    if time is None or temp is None or condition is None or not is_plausible_instant(time):
        return None
    return Moment(
        time=time,
        temp=temp,
        condition=condition,
        feels=as_number(value.get("feels")),
        humidity=as_number(value.get("humidity")),
        wind=as_number(value.get("wind")),
        gust=as_number(value.get("gust")),
        precip=as_number(value.get("precip")),
        precip_prob=as_number(value.get("precipProb")),
    )


def _instant(value: JsonValue | None) -> float | None:
    number = as_number(value)
    return number if number is not None and is_plausible_instant(number) else None


_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _valid_date(text: str | None) -> str | None:
    """`YYYY-MM-DD` of a real calendar day, else None (later arithmetic would raise on it)."""
    if text is None or _DATE.fullmatch(text) is None:
        return None
    try:
        datetime.date(int(text[:4]), int(text[5:7]), int(text[8:]))
    except ValueError:
        return None
    return text


def _decode_day(value: JsonValue | None) -> Day | None:
    if not is_record(value):
        return None
    date = _valid_date(as_string(value.get("date")))
    low = as_number(value.get("min"))
    high = as_number(value.get("max"))
    condition = condition_from_string(as_string(value.get("condition")))
    if date is None or low is None or high is None or condition is None:
        return None
    precip = as_number(value.get("precip"))
    snow = as_number(value.get("snow"))
    return Day(
        date=date,
        min=low,
        max=high,
        condition=condition,
        precip=0 if precip is None else precip,
        snow=0 if snow is None else snow,
        precip_prob=as_number(value.get("precipProb")),
        wind_max=as_number(value.get("windMax")),
        gust_max=as_number(value.get("gustMax")),
    )


def _decode_place(value: JsonValue | None) -> Place | None:
    if not is_record(value):
        return None
    lat = as_number(value.get("lat"))
    lon = as_number(value.get("lon"))
    tz = as_string(value.get("tz"))
    return None if lat is None or lon is None or tz is None else Place(lat=lat, lon=lon, tz=tz)


def _decode_source(value: JsonValue | None) -> SourceName | None:
    if value == "home-assistant":
        return "home-assistant"
    return "open-meteo" if value == "open-meteo" else None


def _decode_sources(value: JsonValue | None) -> list[SourceName]:
    names: list[SourceName] = []
    for entry in as_array(value):
        if entry == "home-assistant":
            names.append("home-assistant")
        elif entry == "open-meteo":
            names.append("open-meteo")
    return names


def _decode_list(
    value: JsonValue | None, decode: Callable[[JsonValue], T | None]
) -> list[T] | None:
    out: list[T] = []
    for entry in as_array(value):
        decoded = decode(entry)
        if decoded is None:
            return None
        out.append(decoded)
    return out


def _decode_bundle(value: JsonValue | None) -> Bundle | None:
    if not is_record(value):
        return None
    place = _decode_place(value.get("place"))
    fetched_at = _instant(value.get("fetchedAt"))
    current = _decode_moment(value.get("current"))
    hourly = _decode_list(value.get("hourly"), _decode_moment)
    daily = _decode_list(value.get("daily"), _decode_day)
    if place is None or fetched_at is None or current is None or hourly is None or daily is None:
        return None
    return Bundle(
        place=place,
        fetched_at=fetched_at,
        current=current,
        hourly=hourly,
        daily=daily,
        sources=_decode_sources(value.get("sources")),
        current_source=_decode_source(value.get("currentSource")),
    )


def _unwrap(text: str | None) -> JsonRecord | None:
    if text is None:
        return None
    parsed = parse_json(text)
    return parsed if is_record(parsed) and _is_schema(parsed.get("schema")) else None


def _safe_read(store: Store, key: str) -> str | None:
    try:
        return store.read(key)
    except Exception:
        return None


def read_bundle(store: Store, key: str) -> Bundle | None:
    wrapper = _unwrap(_safe_read(store, key))
    return _decode_bundle(wrapper.get("bundle") if wrapper else None)


# --- encoders (camelCase on disk; key order is the order the TypeScript writer produced) ----


def _put(target: JsonRecord, key: str, value: JsonValue | None) -> None:
    if value is not None:
        target[key] = value


def place_to_json(place: Place) -> JsonRecord:
    return {"lat": place.lat, "lon": place.lon, "tz": place.tz}


def moment_to_json(moment: Moment) -> JsonRecord:
    out: JsonRecord = {"time": moment.time, "temp": moment.temp, "condition": moment.condition}
    _put(out, "precip", moment.precip)
    _put(out, "feels", moment.feels)
    _put(out, "humidity", moment.humidity)
    _put(out, "wind", moment.wind)
    _put(out, "gust", moment.gust)
    _put(out, "precipProb", moment.precip_prob)
    return out


def day_to_json(day: Day) -> JsonRecord:
    out: JsonRecord = {
        "date": day.date,
        "min": day.min,
        "max": day.max,
        "condition": day.condition,
        "precip": day.precip,
        "snow": day.snow,
    }
    _put(out, "precipProb", day.precip_prob)
    _put(out, "windMax", day.wind_max)
    _put(out, "gustMax", day.gust_max)
    return out


def bundle_to_json(bundle: Bundle) -> JsonRecord:
    out: JsonRecord = {
        "place": place_to_json(bundle.place),
        "fetchedAt": bundle.fetched_at,
        "current": moment_to_json(bundle.current),
        "hourly": [moment_to_json(m) for m in bundle.hourly],
        "daily": [day_to_json(d) for d in bundle.daily],
        "sources": list(bundle.sources),
    }
    _put(out, "currentSource", bundle.current_source)
    return out


def write_bundle(store: Store, key: str, bundle: Bundle) -> None:
    try:
        store.write(key, dumps({"schema": SCHEMA, "bundle": bundle_to_json(bundle)}))
    except Exception:
        return


# --- climate --------------------------------------------------------------------------------


def _decode_grid(value: JsonValue | None) -> list[list[float]] | None:
    def row(cells: JsonValue) -> list[float] | None:
        numbers = [as_number(cell) for cell in as_array(cells)]
        checked = [n for n in numbers if n is not None]
        return checked if len(numbers) == 24 and len(checked) == 24 else None

    rows = _decode_list(value, row)
    return rows if rows is not None and len(rows) == 12 else None


def _decode_series(value: JsonValue | None) -> ClimateSeries | None:
    if not is_record(value):
        return None
    mean = _decode_grid(value.get("mean"))
    sd = _decode_grid(value.get("sd"))
    return None if mean is None or sd is None else ClimateSeries(mean=mean, sd=sd)


def _decode_climate(value: JsonValue | None) -> Climate | None:
    if not is_record(value):
        return None
    place = _decode_place(value.get("place"))
    humidity = _decode_series(value.get("humidity"))
    temp = _decode_series(value.get("temp"))
    built_at = _instant(value.get("builtAt"))
    years = [as_number(y) for y in as_array(value.get("years"))]
    if place is None or humidity is None or temp is None or built_at is None:
        return None
    if len(years) < 2 or years[0] is None or years[1] is None:
        return None
    return Climate(
        place=place,
        years=(int(years[0]), int(years[1])),
        humidity=humidity,
        temp=temp,
        built_at=built_at,
    )


def read_climate(store: Store, key: str) -> Climate | None:
    wrapper = _unwrap(_safe_read(store, key))
    return _decode_climate(wrapper.get("climate") if wrapper else None)


def _series_to_json(series: ClimateSeries) -> JsonRecord:
    return {"mean": [list(row) for row in series.mean], "sd": [list(row) for row in series.sd]}


def climate_to_json(climate: Climate) -> JsonRecord:
    return {
        "place": place_to_json(climate.place),
        "years": list(climate.years),
        "humidity": _series_to_json(climate.humidity),
        "temp": _series_to_json(climate.temp),
        "builtAt": climate.built_at,
    }


def write_climate(store: Store, key: str, climate: Climate) -> None:
    store.write(key, dumps({"schema": SCHEMA, "climate": climate_to_json(climate)}))


def decode_climate_text(text: str) -> Climate | None:
    """Climate norms from a cache file's text (the `{"schema":1,"climate":…}` wrapper)."""
    wrapper = _unwrap(text)
    return _decode_climate(wrapper.get("climate") if wrapper else None)


def decode_bundle_text(text: str) -> Bundle | None:
    """A bundle from JSON text: either the cache wrapper or a bare bundle (CLI `--bundle`)."""
    parsed = parse_json(text)
    if not is_record(parsed):
        return None
    return _decode_bundle(parsed.get("bundle") if _is_schema(parsed.get("schema")) else parsed)
