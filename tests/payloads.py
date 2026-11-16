"""Canned provider payloads and a routing fetcher shared by the provider and service tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from builders import HOUR, at

from weather_cli.providers.json import HttpInit, HttpResponse, JsonValue, StaticResponse

NOW = at("2026-11-09", 14, 10)


def S(ms: float) -> int:
    return int(ms // 1000)


def open_meteo_payload() -> JsonValue:
    start = (NOW // HOUR) * HOUR - HOUR
    times = [S(start + i * HOUR) for i in range(6)]
    return {
        "current": {
            "time": S(NOW),
            "temperature_2m": 7.4,
            "relative_humidity_2m": 70,
            "apparent_temperature": 1.1,
            "precipitation": 0,
            "weather_code": 3,
            "wind_speed_10m": 7,
            "wind_gusts_10m": 12,
        },
        "hourly": {
            "time": times,
            "temperature_2m": [7, 7.4, 7.2, 6.8, 6.5, 6.2],
            "apparent_temperature": [2, 3, 3, 2, 1, 0],
            "relative_humidity_2m": [70, 70, 72, 74, 75, 76],
            "wind_speed_10m": [5, 6, 6, 7, 7, 7],
            "wind_gusts_10m": [9, 10, 11, 12, 12, 12],
            # values are labelled with the END of the hour: 1.4 mm at 16:00 fell during 15:00-16:00
            "precipitation": [0, 0, 0, 1.4, 0, 0],
            "precipitation_probability": [0, 0, 10, 90, 20, 0],
            "weather_code": [3, 3, 3, 61, 3, 3],
        },
        "daily": {
            "time": [S(at("2026-11-09", 0)), S(at("2026-11-10", 0))],
            "weather_code": [3, 61],
            "temperature_2m_max": [11.4, 14.2],
            "temperature_2m_min": [5.6, 6.1],
            "precipitation_sum": [1.4, 5],
            "snowfall_sum": [0, 0],
            "precipitation_probability_max": [90, 80],
            "wind_speed_10m_max": [8, 9],
            "wind_gusts_10m_max": [14, 16],
        },
    }


def iso(ms: float) -> str:
    stamp = datetime.fromtimestamp(ms / 1000, timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(ms % 1000):03d}Z"


def ha_state() -> JsonValue:
    return {
        "entity_id": "weather.forecast_home",
        "state": "cloudy",
        "last_updated": iso(NOW - 5 * 60_000),
        "attributes": {
            "temperature": 6.9,
            "humidity": 87,
            "wind_speed": 18,
            "wind_speed_unit": "km/h",
            "temperature_unit": "°C",
            "precipitation_unit": "mm",
        },
    }


def ha_forecast(kind: str) -> JsonValue:
    if kind == "hourly":
        entries = [
            {
                "condition": "rainy" if i == 3 else "cloudy",
                "datetime": iso((NOW // HOUR) * HOUR + (i - 1) * HOUR),
                "temperature": 7 - i * 0.2,
                "wind_speed": 18,
                "precipitation": 0.8 if i == 3 else 0,
                "humidity": 85,
            }
            for i in range(8)
        ]
    else:
        entries = [
            {
                "condition": "partlycloudy",
                "datetime": "2026-11-09T11:00:00+00:00",
                "temperature": 11.5,
                "templow": 5.6,
                "wind_speed": 36,
                "precipitation": 1.6,
            }
        ]
    return {
        "changed_states": [],
        "service_response": {"weather.forecast_home": {"forecast": entries}},
    }


def reply(body: JsonValue, status: int = 200) -> HttpResponse:
    return StaticResponse(ok=status < 400, status=status, body=json.dumps(body))


def router(om=None, ha=None):
    """A fetcher that answers Open-Meteo / Home Assistant URLs from the given handlers."""
    calls: list[str] = []

    def fetcher(url: str, init: HttpInit) -> HttpResponse:
        calls.append(url)
        handler = om if "open-meteo" in url else ha
        if handler is None:
            raise OSError("offline")
        return handler()

    return fetcher, calls


def om_ok() -> HttpResponse:
    return reply(open_meteo_payload())
