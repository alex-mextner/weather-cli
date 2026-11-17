"""Home Assistant provider: a `weather.*` entity plus `weather.get_forecasts`.

Accessed via: `get_weather()` (service.py) as the primary source when configured. Works through
the HA REST API directly or through a caching proxy with the same API (e.g. ha-cli).

Assumptions: forecast hours start at their `datetime` (HA convention, same as this library). HA
gives no apparent temperature or gusts for most integrations; `feels` stays unset and the
temperature decision computes it from temperature/humidity/wind. Units come from the entity's own
`*_unit` attributes and are converted at this boundary.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import cast

from ..conditions import condition_from_home_assistant
from ..horizon import hours_ahead
from ..local_time import local_date
from ..types import Bundle, Condition, Day, Moment, Place
from ..units import fahrenheit_to_celsius, kmh_to_ms, knots_to_ms, mph_to_ms, parse_iso_ms
from .json import (
    Fetcher,
    JsonRecord,
    JsonValue,
    Outcome,
    ProviderError,
    as_array,
    as_number,
    as_string,
    gather,
    is_record,
    request_json,
)

# `/api/states` lists every entity of the installation: far larger than any other answer.
MAX_STATES_BYTES = 32 * 1024 * 1024

# Shares of the caller's timeout. The forecasts (optional) stop earlier than the state (required),
# and both stop before the caller's own deadline so a late answer is never lost in a race.
_FORECAST_SHARE = 0.8
_STATE_SHARE = 0.95


@dataclass(frozen=True, slots=True)
class HaConnection:
    # Base URL without trailing slash, e.g. http://homeassistant.local:8123
    url: str
    token: str
    # `weather.*` entity id; discovered from /api/states when omitted.
    entity_id: str | None = None

    def __repr__(self) -> str:
        # The token must never end up in a log line or a traceback.
        return f"HaConnection(url={self.url!r}, entity_id={self.entity_id!r})"


@dataclass(frozen=True, slots=True)
class Units:
    fahrenheit: bool
    wind: Callable[[float], float]
    precip: Callable[[float], float]

    def temp(self, value: float) -> float:
        return fahrenheit_to_celsius(value) if self.fahrenheit else value


_WIND_CONVERTERS: dict[str, Callable[[float], float]] = {
    "m/s": lambda v: v,
    "mph": mph_to_ms,
    "kn": knots_to_ms,
    "ft/s": lambda v: v * 0.3048,
}


def _units_of(attributes: JsonRecord) -> Units:
    precip_unit = as_string(attributes.get("precipitation_unit"))
    wind_unit = as_string(attributes.get("wind_speed_unit"))
    return Units(
        fahrenheit=as_string(attributes.get("temperature_unit")) == "°F",
        wind=_WIND_CONVERTERS.get(wind_unit or "", kmh_to_ms),
        precip=(lambda v: v * 25.4) if precip_unit == "in" else (lambda v: v),
    )


def _condition_of(value: JsonValue | None) -> Condition:
    return condition_from_home_assistant(as_string(value) or "") or "cloudy"


def _scaled(value: object, convert: Callable[[float], float]) -> float | None:
    number = as_number(value)
    return None if number is None else convert(number)


def _forecast_moment(entry: JsonRecord, units: Units) -> Moment | None:
    stamp = parse_iso_ms(as_string(entry.get("datetime")) or "")
    temp = as_number(entry.get("temperature"))
    if stamp is None or temp is None:
        return None
    return Moment(
        time=stamp,
        temp=units.temp(temp),
        condition=_condition_of(entry.get("condition")),
        humidity=as_number(entry.get("humidity")),
        wind=_scaled(entry.get("wind_speed"), units.wind),
        gust=_scaled(entry.get("wind_gust_speed"), units.wind),
        precip=_scaled(entry.get("precipitation"), units.precip),
        precip_prob=as_number(entry.get("precipitation_probability")),
    )


def _forecast_day(entry: JsonRecord, units: Units, place: Place) -> Day | None:
    stamp = parse_iso_ms(as_string(entry.get("datetime")) or "")
    high = as_number(entry.get("temperature"))
    if stamp is None or high is None:
        return None
    low = as_number(entry.get("templow"))
    precip = as_number(entry.get("precipitation"))
    try:
        date = local_date(stamp, place.tz)
    except OverflowError:  # an instant the calendar cannot show: skip the entry
        return None
    return Day(
        date=date,
        min=units.temp(high if low is None else low),
        max=units.temp(high),
        condition=_condition_of(entry.get("condition")),
        precip=units.precip(0 if precip is None else precip),
        snow=0,
        precip_prob=as_number(entry.get("precipitation_probability")),
        wind_max=_scaled(entry.get("wind_speed"), units.wind),
        gust_max=_scaled(entry.get("wind_gust_speed"), units.wind),
    )


def _forecast_list(response: JsonValue | None, entity_id: str) -> list[JsonRecord]:
    service_response = response.get("service_response") if is_record(response) else None
    if not is_record(service_response):
        return []
    for_entity = service_response.get(entity_id)
    if not is_record(for_entity):
        return []
    return [entry for entry in as_array(for_entity.get("forecast")) if is_record(entry)]


def _current_moment(
    state: JsonRecord, attributes: JsonRecord, condition: Condition, units: Units, fetched_at: float
) -> Moment:
    temp = as_number(attributes.get("temperature"))
    if temp is None:
        raise ProviderError("home-assistant", "entity has no temperature")
    updated = parse_iso_ms(as_string(state.get("last_updated")) or "")
    return Moment(
        time=fetched_at if updated is None else updated,
        temp=units.temp(temp),
        condition=condition,
        humidity=as_number(attributes.get("humidity")),
        wind=_scaled(attributes.get("wind_speed"), units.wind),
        gust=_scaled(attributes.get("wind_gust_speed"), units.wind),
        feels=_scaled(attributes.get("apparent_temperature"), units.temp),
    )


def parse_home_assistant(
    state: JsonValue,
    hourly_response: JsonValue | None,
    daily_response: JsonValue | None,
    place: Place,
    fetched_at: float,
) -> Bundle:
    if not is_record(state):
        raise ProviderError("home-assistant", "unexpected state payload")
    entity_id = as_string(state.get("entity_id")) or ""
    attributes = state.get("attributes")
    condition = condition_from_home_assistant(as_string(state.get("state")) or "")
    if not is_record(attributes) or condition is None:
        raise ProviderError("home-assistant", f"weather entity {entity_id} is unavailable")
    units = _units_of(attributes)
    current = _current_moment(state, attributes, condition, units, fetched_at)
    hourly = [
        m
        for entry in _forecast_list(hourly_response, entity_id)
        if (m := _forecast_moment(entry, units)) is not None
    ]
    daily = [
        d
        for entry in _forecast_list(daily_response, entity_id)
        if (d := _forecast_day(entry, units, place)) is not None
    ]
    return Bundle(
        place=place,
        fetched_at=fetched_at,
        current=current,
        hourly=hourly,
        daily=daily,
        sources=["home-assistant"],
        current_source="home-assistant",
        forecast_missing=not hours_ahead(hourly, fetched_at),
    )


def _auth_headers(connection: HaConnection) -> dict[str, str]:
    return {"authorization": f"Bearer {connection.token}"}


def fetch_ha_place(
    connection: HaConnection, *, timeout_ms: float, fetch_impl: Fetcher | None = None
) -> Place:
    """Home coordinates and zone from HA's own configuration."""
    config = request_json(
        "home-assistant",
        f"{connection.url}/api/config",
        timeout_ms=timeout_ms,
        headers=_auth_headers(connection),
        fetch_impl=fetch_impl,
    )
    if is_record(config):
        lat = as_number(config.get("latitude"))
        lon = as_number(config.get("longitude"))
        tz = as_string(config.get("time_zone"))
        if lat is not None and lon is not None and tz is not None:
            return Place(lat=lat, lon=lon, tz=tz)
    raise ProviderError("home-assistant", "/api/config has no coordinates")


def _discover_entity(
    connection: HaConnection, timeout_ms: float, fetch_impl: Fetcher | None
) -> str:
    states = request_json(
        "home-assistant",
        f"{connection.url}/api/states",
        timeout_ms=timeout_ms,
        max_bytes=MAX_STATES_BYTES,
        headers=_auth_headers(connection),
        fetch_impl=fetch_impl,
    )
    for state in as_array(states):
        entity_id = as_string(state.get("entity_id")) if is_record(state) else None
        if entity_id is not None and entity_id.startswith("weather."):
            return entity_id
    raise ProviderError("home-assistant", "no weather.* entity found")


def fetch_home_assistant(
    connection: HaConnection,
    place: Place,
    *,
    timeout_ms: float,
    fetch_impl: Fetcher | None = None,
    fetched_at: float | None = None,
) -> Bundle:
    started = time.monotonic()
    headers = _auth_headers(connection)
    entity_id = connection.entity_id or _discover_entity(connection, timeout_ms, fetch_impl)

    def call(url: str, body: JsonValue = None) -> JsonValue:
        return request_json(
            "home-assistant",
            url,
            timeout_ms=timeout_ms,
            headers=headers,
            body=body,
            fetch_impl=fetch_impl,
        )

    def forecast(kind: str) -> Callable[[], JsonValue]:
        url = f"{connection.url}/api/services/weather/get_forecasts?return_response"
        return lambda: call(url, {"entity_id": entity_id, "type": kind})

    def forecasts() -> list[Outcome]:
        # Optional data: it may use only part of the budget and may fail without failing HA.
        share = max(0.0, timeout_ms / 1000 * _FORECAST_SHARE - (time.monotonic() - started))
        return gather([forecast("hourly"), forecast("daily")], share)

    # The state is required and gets nearly the whole budget; the forecasts are cut earlier, so a
    # stalled forecast cannot cost the answer a state that already arrived.
    state_budget = max(0.0, timeout_ms / 1000 * _STATE_SHARE - (time.monotonic() - started))
    state, forecast_outcomes = gather(
        [lambda: call(f"{connection.url}/api/states/{entity_id}"), forecasts], state_budget
    )
    if state.error is not None:
        raise state.error
    # A failing forecast wrapper (timeout, a bug) means both forecasts failed; the state that
    # already arrived is still an answer.
    if forecast_outcomes.error is not None:
        hourly = daily = Outcome(error=forecast_outcomes.error)
    else:
        hourly, daily = cast(list[Outcome], forecast_outcomes.value)
    bundle = parse_home_assistant(
        state.value,
        None if hourly.error else hourly.value,
        None if daily.error else daily.value,
        place,
        int(time.time() * 1000) if fetched_at is None else fetched_at,
    )
    if hourly.error is None and daily.error is None:
        return bundle
    return replace(bundle, forecast_missing=True)
