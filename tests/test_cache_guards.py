"""What may and may not be served from the cache, and what a cache file may contain."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from builders import PLACE, bundle, climate
from payloads import NOW, ha_forecast, ha_state, om_ok, reply, router

from weather_cli.cache import (
    MemoryStore,
    bundle_key,
    bundle_to_json,
    climate_to_json,
    decode_climate_text,
    read_bundle,
    write_bundle,
)
from weather_cli.providers.home_assistant import HaConnection
from weather_cli.providers.json import HttpInit, HttpResponse
from weather_cli.service import get_weather

HA = HaConnection(url="http://ha", token="t", entity_id="weather.forecast_home")


def seeded(sources: list[str], tz: str = PLACE.tz) -> MemoryStore:
    store = MemoryStore()
    stored = replace(
        bundle(now=NOW, current={"temp": 1}),
        sources=sources,
        place=replace(PLACE, tz=tz),
    )
    write_bundle(store, bundle_key(PLACE), stored)
    return store


class TestCacheHitMatchesTheRequest:
    def test_a_bundle_from_a_provider_no_longer_asked_for_is_a_miss(self):
        fetcher, calls = router(om=om_ok)
        result = get_weather(
            place=PLACE,
            store=seeded(["home-assistant", "open-meteo"]),
            now=NOW,
            fetch_impl=fetcher,
        )
        assert result.origin == "live"
        assert result.bundle.sources == ["open-meteo"]
        assert calls != []

    def test_a_bundle_from_the_requested_providers_is_served(self):
        fetcher, calls = router(om=om_ok)
        result = get_weather(place=PLACE, store=seeded(["open-meteo"]), now=NOW, fetch_impl=fetcher)
        assert (result.origin, calls) == ("cache", [])

    def test_a_bundle_for_another_zone_is_a_miss(self):
        fetcher, calls = router(om=om_ok)
        result = get_weather(
            place=PLACE,
            store=seeded(["open-meteo"], tz="UTC"),
            now=NOW,
            fetch_impl=fetcher,
        )
        assert result.origin == "live"
        assert result.bundle.place.tz == PLACE.tz
        assert calls != []

    def test_with_no_provider_to_ask_the_cache_still_answers(self):
        result = get_weather(
            place=replace(PLACE, tz="UTC"),
            store=seeded(["open-meteo"]),
            open_meteo=False,
            now=NOW,
        )
        assert result.origin == "cache"


class TestPartialForecastIsNotCached:
    def test_one_failed_forecast_keeps_the_warning_and_skips_the_write(self):
        def daily_only(url: str, init: HttpInit) -> HttpResponse:
            if "get_forecasts" not in url:
                return reply(ha_state())
            if "hourly" in (init.body or ""):
                return reply({}, 400)
            return reply(ha_forecast("daily"))

        store = MemoryStore()
        result = get_weather(
            place=PLACE, ha=HA, open_meteo=False, store=store, now=NOW, fetch_impl=daily_only
        )
        assert result.bundle.daily != [] and result.bundle.hourly == []
        assert result.errors == ["home-assistant: forecast unavailable"]
        assert store.entries == {}


class TestDecodersRejectWhatWouldCrashLater:
    @staticmethod
    def stored_with_date(date: object) -> str:
        data = bundle_to_json(bundle(now=NOW))
        assert isinstance(data["daily"], list)
        data["daily"][0]["date"] = date  # type: ignore[index]
        return json.dumps({"schema": 1, "bundle": data})

    @pytest.mark.parametrize(
        "date", ["x", "2026-13-01", "2026-02-30", "20261109", "٢٠٢٦-١١-٠٩", ""]
    )
    def test_a_day_with_an_unreal_date_is_a_miss(self, date):
        store = MemoryStore()
        store.write(bundle_key(PLACE), self.stored_with_date(date))
        assert read_bundle(store, bundle_key(PLACE)) is None

    def test_a_real_date_is_kept(self):
        store = MemoryStore()
        store.write(bundle_key(PLACE), self.stored_with_date("2026-11-09"))
        assert read_bundle(store, bundle_key(PLACE)) is not None

    def test_a_garbage_date_cache_is_refetched_not_fatal(self):
        store = MemoryStore()
        store.write(bundle_key(PLACE), self.stored_with_date("x"))
        fetcher, _ = router(om=om_ok)
        result = get_weather(place=PLACE, store=store, now=NOW, fetch_impl=fetcher)
        assert result.origin == "live"

    def climate_text(self, edit) -> str:
        data = climate_to_json(climate(60, 10))
        edit(data)
        return json.dumps({"schema": 1, "climate": data})

    def test_a_full_grid_decodes(self):
        assert decode_climate_text(self.climate_text(lambda _d: None)) is not None

    def test_a_row_with_a_null_among_25_cells_is_rejected(self):
        def edit(data):
            data["humidity"]["mean"][0] = [None, *data["humidity"]["mean"][0]]

        assert decode_climate_text(self.climate_text(edit)) is None

    @pytest.mark.parametrize("rows", [11, 13])
    def test_a_grid_that_is_not_twelve_months_is_rejected(self, rows):
        def edit(data):
            grid = data["humidity"]["mean"]
            data["humidity"]["mean"] = grid[:rows] if rows < 12 else [*grid, grid[0]]

        assert decode_climate_text(self.climate_text(edit)) is None

    def test_a_row_with_a_null_in_place_of_a_number_is_rejected(self):
        def edit(data):
            data["temp"]["sd"][3][5] = None

        assert decode_climate_text(self.climate_text(edit)) is None
