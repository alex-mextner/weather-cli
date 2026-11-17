"""Providers, merge, service (cache/deadline/offline), cache decoding and climate norms."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace

import pytest
from builders import HOUR, PLACE, TZ, at, bundle
from payloads import NOW, S, ha_forecast, ha_state, om_ok, open_meteo_payload, reply, router

from weather_cli.cache import (
    MemoryStore,
    bundle_key,
    climate_key,
    read_bundle,
    read_climate,
    write_bundle,
)
from weather_cli.climate import archive_years, load_climate, parse_archive, refresh_climate
from weather_cli.providers.home_assistant import (
    HaConnection,
    fetch_ha_place,
    fetch_home_assistant,
    parse_home_assistant,
)
from weather_cli.providers.json import (
    HttpInit,
    HttpResponse,
    JsonValue,
    ProviderError,
)
from weather_cli.providers.merge import merge_bundles
from weather_cli.providers.open_meteo import fetch_open_meteo, open_meteo_url, parse_open_meteo
from weather_cli.service import get_weather
from weather_cli.types import Bundle


class TestOpenMeteo:
    def test_parses_units_relabels_precipitation_to_the_hour_start_and_drops_the_unknown_last_hour(
        self,
    ):
        b = parse_open_meteo(open_meteo_payload(), PLACE, NOW)
        assert b.current.temp == 7.4
        assert b.current.feels == 1.1
        assert b.current.humidity == 70
        assert b.current.wind == 7
        assert b.current.gust == 12
        assert b.current.condition == "cloudy"
        assert len(b.hourly) == 5
        rainy = next(m for m in b.hourly if m.precip == 1.4)
        assert rainy.time == at("2026-11-09", 15)
        assert rainy.condition == "rain"
        assert rainy.precip_prob == 90
        assert [d.date for d in b.daily] == ["2026-11-09", "2026-11-10"]
        second = b.daily[1]
        assert (second.max, second.min, second.condition) == (14.2, 6.1, "rain")
        assert (second.precip, second.gust_max) == (5, 16)

    def test_request_asks_for_metres_per_second_and_unix_time(self):
        url = open_meteo_url(PLACE)
        assert "wind_speed_unit=ms" in url
        assert "timeformat=unixtime" in url
        assert "timezone=Europe%2FBelgrade" in url

    def test_garbage_payloads_are_errors_not_bundles(self):
        with pytest.raises(ProviderError, match="open-meteo"):
            parse_open_meteo({}, PLACE, NOW)
        with pytest.raises(ProviderError, match="temperature"):
            parse_open_meteo({"current": {"temperature_2m": None}}, PLACE, NOW)


class TestHomeAssistant:
    def test_converts_kmh_to_ms_reads_condition_and_forecasts(self):
        b = parse_home_assistant(
            ha_state(), ha_forecast("hourly"), ha_forecast("daily"), PLACE, NOW
        )
        assert b.current.wind == pytest.approx(5, abs=1e-5)
        assert b.current.condition == "cloudy"
        assert b.current.feels is None
        assert len(b.hourly) == 8
        assert next(m for m in b.hourly if m.condition == "rain").precip == 0.8
        first = b.daily[0]
        assert (first.date, first.max, first.min, first.precip) == ("2026-11-09", 11.5, 5.6, 1.6)
        assert first.wind_max == pytest.approx(10, abs=1e-5)

    def test_fahrenheit_and_mph_are_converted(self):
        state: JsonValue = {
            "entity_id": "weather.x",
            "state": "sunny",
            "attributes": {
                "temperature": 50,
                "temperature_unit": "°F",
                "wind_speed": 10,
                "wind_speed_unit": "mph",
            },
        }
        b = parse_home_assistant(state, None, None, PLACE, NOW)
        assert b.current.temp == pytest.approx(10, abs=1e-5)
        assert b.current.wind == pytest.approx(4.4704, abs=1e-4)
        assert b.hourly == []

    def test_an_unavailable_entity_is_an_error(self):
        with pytest.raises(ProviderError, match="unavailable"):
            parse_home_assistant(
                {"entity_id": "weather.x", "state": "unavailable", "attributes": {}},
                None,
                None,
                PLACE,
                NOW,
            )

    def test_place_comes_from_ha_config(self):
        def fetcher(_url: str, _init: HttpInit) -> HttpResponse:
            return reply({"latitude": 44.8, "longitude": 20.48, "time_zone": TZ})

        place = fetch_ha_place(
            HaConnection(url="http://ha", token="t"), timeout_ms=100, fetch_impl=fetcher
        )
        assert place == PLACE


class TestMerge:
    @staticmethod
    def primary() -> Bundle:
        return parse_home_assistant(
            ha_state(), ha_forecast("hourly"), ha_forecast("daily"), PLACE, NOW
        )

    @staticmethod
    def secondary() -> Bundle:
        return parse_open_meteo(open_meteo_payload(), PLACE, NOW)

    def test_home_assistant_leads_open_meteo_adds_gusts_and_a_feels_like_offset(self):
        m = merge_bundles(self.primary(), self.secondary(), NOW)
        assert m.sources == ["home-assistant", "open-meteo"]
        assert m.current.temp == 6.9  # HA's own temperature
        assert m.current.gust == 12
        # OM says 7.4 → feels 1.1 (−6.3): applied as an offset to HA's 6.9, not copied
        assert m.current.feels == pytest.approx(6.9 - 6.3, abs=1e-5)

    def test_stale_primary_current_is_replaced(self):
        old = self.primary()
        old = replace(old, current=replace(old.current, time=NOW - 3 * HOUR))
        assert merge_bundles(old, self.secondary(), NOW).current.temp == 7.4

    def test_missing_provider_the_other_one_is_used_as_is(self):
        assert merge_bundles(None, self.secondary(), NOW).sources == ["open-meteo"]
        assert merge_bundles(self.primary(), None, NOW).sources == ["home-assistant"]

    def test_days_beyond_the_primary_horizon_come_from_the_secondary(self):
        p = self.primary()
        p = replace(
            p,
            daily=[
                p.daily[0],
                replace(p.daily[0], date="2026-11-10"),
                replace(p.daily[0], date="2026-11-11"),
            ],
        )
        s = self.secondary()
        s = replace(s, daily=[*s.daily, replace(s.daily[0], date="2026-11-12")])
        assert [d.date for d in merge_bundles(p, s, NOW).daily] == [
            "2026-11-09",
            "2026-11-10",
            "2026-11-11",
            "2026-11-12",
        ]


class TestGetWeather:
    def test_live_fetch_is_cached_a_second_call_within_ten_minutes_does_no_network(self):
        store = MemoryStore()
        fetcher, calls = router(om=om_ok)
        first = get_weather(place=PLACE, store=store, now=NOW, fetch_impl=fetcher)
        assert first.origin == "live"
        calls_after_first = len(calls)
        second = get_weather(place=PLACE, store=store, now=NOW + 5 * 60_000, fetch_impl=fetcher)
        assert second.origin == "cache"
        assert len(calls) == calls_after_first
        assert second.bundle.current.temp == 7.4

    def test_offline_the_last_cached_bundle_answers_flagged_stale(self):
        store = MemoryStore()
        get_weather(place=PLACE, store=store, now=NOW, fetch_impl=router(om=om_ok)[0])
        result = get_weather(place=PLACE, store=store, now=NOW + 3 * HOUR, fetch_impl=router()[0])
        assert result.origin == "stale"
        assert "open-meteo" in ",".join(result.errors)

    def test_offline_with_an_ancient_cache_or_none_is_an_error(self):
        store = MemoryStore()
        with pytest.raises(RuntimeError, match="no weather data"):
            get_weather(place=PLACE, store=store, now=NOW, fetch_impl=router()[0])
        get_weather(place=PLACE, store=store, now=NOW, fetch_impl=router(om=om_ok)[0])
        with pytest.raises(RuntimeError, match="no weather data"):
            get_weather(place=PLACE, store=store, now=NOW + 13 * HOUR, fetch_impl=router()[0])

    def test_one_provider_failing_still_answers_from_the_other_with_a_warning(self):
        fetcher, _ = router(om=om_ok)
        result = get_weather(
            place=PLACE,
            now=NOW,
            fetch_impl=fetcher,
            ha=HaConnection(url="http://ha", token="t", entity_id="weather.forecast_home"),
        )
        assert result.origin == "live"
        assert result.bundle.sources == ["open-meteo"]
        assert "home-assistant" in ",".join(result.errors)

    def test_a_hung_provider_is_cut_at_the_deadline_instead_of_delaying_the_answer(self):
        release = threading.Event()

        def hang(_url: str, init: HttpInit) -> HttpResponse:
            release.wait(init.timeout_s * 20)
            raise OSError("aborted")

        started = time.monotonic()
        with pytest.raises(RuntimeError, match="no weather data"):
            get_weather(place=PLACE, now=NOW, fetch_impl=hang, timeout_ms=80)
        assert time.monotonic() - started < 0.5
        release.set()

    def test_both_providers_combine_when_both_answer(self):
        fetcher, _ = router(om=om_ok, ha=lambda: reply(ha_state()))

        # the HA forecast calls are POSTs to the same host: reply with a forecast body for them
        def wrapped(url: str, init: HttpInit) -> HttpResponse:
            if "get_forecasts" in url:
                return reply(ha_forecast("hourly" if "hourly" in (init.body or "") else "daily"))
            return fetcher(url, init)

        result = get_weather(
            place=PLACE,
            now=NOW,
            fetch_impl=wrapped,
            ha=HaConnection(url="http://ha", token="t", entity_id="weather.forecast_home"),
        )
        assert result.bundle.sources == ["home-assistant", "open-meteo"]
        assert result.bundle.current.gust == 12

    def test_fetch_helpers_send_the_bearer_token(self):
        seen: dict[str, str] = {}

        def fetcher(_url: str, init: HttpInit) -> HttpResponse:
            seen["auth"] = init.headers.get("authorization", "")
            return reply({"latitude": 1, "longitude": 2, "time_zone": TZ})

        fetch_ha_place(
            HaConnection(url="http://ha", token="secret"), timeout_ms=100, fetch_impl=fetcher
        )
        assert seen["auth"] == "Bearer secret"
        with pytest.raises(ProviderError, match="HTTP 500"):
            fetch_open_meteo(PLACE, timeout_ms=100, fetch_impl=lambda *_: reply({}, 500))
        with pytest.raises(ProviderError, match="HTTP 401"):
            fetch_home_assistant(
                HaConnection(url="http://ha", token="t", entity_id="weather.x"),
                PLACE,
                timeout_ms=100,
                fetch_impl=lambda *_: reply({}, 401),
            )

    def test_the_token_never_appears_in_the_connection_repr(self):
        assert "secret" not in repr(HaConnection(url="http://ha", token="secret"))


class TestCacheDecoding:
    def test_a_corrupt_or_foreign_file_is_a_miss(self):
        store = MemoryStore()
        store.write(bundle_key(PLACE), "{not json")
        assert read_bundle(store, bundle_key(PLACE)) is None
        store.write(bundle_key(PLACE), json.dumps({"schema": 99, "bundle": {}}))
        assert read_bundle(store, bundle_key(PLACE)) is None
        store.write(climate_key(PLACE), json.dumps({"schema": 1, "climate": {"humidity": {}}}))
        assert read_climate(store, climate_key(PLACE)) is None

    def test_a_bundle_survives_a_round_trip(self):
        store = MemoryStore()
        b = bundle(now=NOW, current={"feels": 3, "gust": 8})
        write_bundle(store, bundle_key(PLACE), b)
        assert read_bundle(store, bundle_key(PLACE)) == b


class TestClimateNorms:
    def test_mean_and_sd_per_local_month_and_hour_from_archive_hours(self):
        times: list[int] = []
        temps: list[float] = []
        hums: list[float] = []
        # three November days at 14:00 local with humidity 60/70/80
        for i, date in enumerate(["2026-11-02", "2026-11-03", "2026-11-04"]):
            times.append(S(at(date, 14)))
            temps.append(10 + i)
            hums.append(60 + i * 10)
        climate = parse_archive(
            {"hourly": {"time": times, "temperature_2m": temps, "relative_humidity_2m": hums}},
            PLACE,
            (2015, 2024),
            NOW,
        )
        assert climate.humidity.mean[10][14] == 70
        assert climate.humidity.sd[10][14] == 10
        assert climate.temp.mean[10][14] == 11
        assert climate.humidity.mean[0][0] == 0

    def test_refresh_writes_the_cache_and_load_reads_it_back(
        self,
    ):
        store = MemoryStore()
        requested: list[str] = []

        def fetcher(url: str, _init: HttpInit) -> HttpResponse:
            requested.append(url)
            return reply(
                {
                    "hourly": {
                        "time": [S(at("2026-11-02", 14))],
                        "temperature_2m": [10],
                        "relative_humidity_2m": [70],
                    }
                }
            )

        assert load_climate(store, PLACE) is None
        refresh_climate(store, PLACE, timeout_ms=100, now=NOW, fetch_impl=fetcher)
        assert "start_date=2016-01-01" in requested[0]
        assert "end_date=2025-12-31" in requested[0]
        loaded = load_climate(store, PLACE)
        assert loaded is not None
        assert loaded.years == (2016, 2025)
        assert archive_years(NOW) == (2016, 2025)
