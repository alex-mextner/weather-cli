"""Open-Meteo forecast provider (free, no key): current, 48 forecast hours, 8 days.

Accessed via: `get_weather()` (service.py) as the secondary/enrichment source and the only source
that brings apparent temperature, gusts and precipitation probability.

Assumptions: requested with `timeformat=unixtime` and `wind_speed_unit=ms`, so no string date
parsing and no unit conversion. Open-Meteo labels hourly precipitation with the END of the hour;
this provider re-labels it to the hour's START (the library-wide convention), which is why the
last returned hour is dropped (its successor is unknown).
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from ..conditions import WET_MM, condition_from_wmo
from ..horizon import hours_ahead
from ..local_time import local_date
from ..types import Bundle, Day, Moment, Place
from ..units import is_plausible_instant
from .json import (
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

BASE = "https://api.open-meteo.com/v1/forecast"

CURRENT_FIELDS = [
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
]
HOURLY_FIELDS = [*CURRENT_FIELDS, "precipitation_probability"]
DAILY_FIELDS = [
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "snowfall_sum",
    "precipitation_probability_max",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
]

Column = Sequence[float | None]


def open_meteo_url(place: Place) -> str:
    query = form_encode(
        {
            "latitude": js_number(place.lat),
            "longitude": js_number(place.lon),
            "current": ",".join(CURRENT_FIELDS),
            "hourly": ",".join(HOURLY_FIELDS),
            "daily": ",".join(DAILY_FIELDS),
            "timezone": place.tz,
            "timeformat": "unixtime",
            "wind_speed_unit": "ms",
            "past_hours": "2",
            "forecast_hours": "48",
            "forecast_days": "8",
        }
    )
    return f"{BASE}?{query}"


def _column(block: JsonValue | None, name: str) -> list[float | None]:
    return [as_number(v) for v in as_array(block.get(name))] if is_record(block) else []


def _at(column: Column, index: int) -> float | None:
    return column[index] if 0 <= index < len(column) else None


def _parse_current(current: JsonValue | None) -> Moment:
    if not is_record(current):
        raise ProviderError("open-meteo", "no current block")
    temp = as_number(current.get("temperature_2m"))
    stamp = as_number(current.get("time"))
    if temp is None or stamp is None:
        raise ProviderError("open-meteo", "current block has no temperature")
    if not is_plausible_instant(stamp * 1000):
        raise ProviderError("open-meteo", "current block has an impossible time")
    code = as_number(current.get("weather_code"))
    return Moment(
        time=stamp * 1000,
        temp=temp,
        condition=condition_from_wmo(3 if code is None else code),
        feels=as_number(current.get("apparent_temperature")),
        humidity=as_number(current.get("relative_humidity_2m")),
        wind=as_number(current.get("wind_speed_10m")),
        gust=as_number(current.get("wind_gusts_10m")),
        precip=as_number(current.get("precipitation")),
    )


def _hour_condition(codes: Column, amount: float | None, i: int) -> int | float:
    # Precipitation and its sky code at the hour's END describe the hour that starts here.
    wet_code = _at(codes, i + 1) if amount is not None and amount >= WET_MM else None
    for code in (wet_code, _at(codes, i)):
        if code is not None:
            return code
    return 3


def _parse_hours(hourly: JsonValue | None) -> list[Moment]:
    times = _column(hourly, "time")
    cols = {
        name: _column(hourly, name)
        for name in (
            "temperature_2m",
            "apparent_temperature",
            "relative_humidity_2m",
            "wind_speed_10m",
            "wind_gusts_10m",
            "precipitation",
            "precipitation_probability",
            "weather_code",
        )
    }
    hours: list[Moment] = []
    for i in range(len(times) - 1):
        start = times[i]
        temp = _at(cols["temperature_2m"], i)
        if start is None or temp is None or not is_plausible_instant(start * 1000):
            continue
        amount = _at(cols["precipitation"], i + 1)
        hours.append(
            Moment(
                time=start * 1000,
                temp=temp,
                condition=condition_from_wmo(_hour_condition(cols["weather_code"], amount, i)),
                feels=_at(cols["apparent_temperature"], i),
                humidity=_at(cols["relative_humidity_2m"], i),
                wind=_at(cols["wind_speed_10m"], i),
                gust=_at(cols["wind_gusts_10m"], i),
                precip=amount,
                precip_prob=_at(cols["precipitation_probability"], i + 1),
            )
        )
    return hours


def _parse_days(daily: JsonValue | None, place: Place) -> list[Day]:
    times = _column(daily, "time")
    cols = {
        name: _column(daily, name)
        for name in (
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "snowfall_sum",
            "precipitation_probability_max",
            "wind_speed_10m_max",
            "wind_gusts_10m_max",
        )
    }
    days: list[Day] = []
    for i, stamp in enumerate(times):
        high = _at(cols["temperature_2m_max"], i)
        low = _at(cols["temperature_2m_min"], i)
        if stamp is None or high is None or low is None or not is_plausible_instant(stamp * 1000):
            continue
        code = _at(cols["weather_code"], i)
        precip = _at(cols["precipitation_sum"], i)
        snow = _at(cols["snowfall_sum"], i)
        days.append(
            Day(
                date=local_date(stamp * 1000, place.tz),
                min=low,
                max=high,
                condition=condition_from_wmo(3 if code is None else code),
                precip=0 if precip is None else precip,
                snow=0 if snow is None else snow,
                precip_prob=_at(cols["precipitation_probability_max"], i),
                wind_max=_at(cols["wind_speed_10m_max"], i),
                gust_max=_at(cols["wind_gusts_10m_max"], i),
            )
        )
    return days


def parse_open_meteo(json: JsonValue, place: Place, fetched_at: float) -> Bundle:
    if not is_record(json):
        raise ProviderError("open-meteo", "unexpected payload")
    current = _parse_current(json.get("current"))
    hours = _parse_hours(json.get("hourly"))
    days = _parse_days(json.get("daily"), place)
    if not days:
        raise ProviderError("open-meteo", "no daily forecast")
    return Bundle(
        place=place,
        fetched_at=fetched_at,
        current=current,
        hourly=hours,
        daily=days,
        sources=["open-meteo"],
        current_source="open-meteo",
        forecast_missing=not hours_ahead(hours, fetched_at),
    )


def fetch_open_meteo(
    place: Place,
    *,
    timeout_ms: float,
    fetch_impl: Fetcher | None = None,
    fetched_at: float | None = None,
) -> Bundle:
    json = request_json(
        "open-meteo", open_meteo_url(place), timeout_ms=timeout_ms, fetch_impl=fetch_impl
    )
    return parse_open_meteo(
        json, place, int(time.time() * 1000) if fetched_at is None else fetched_at
    )
