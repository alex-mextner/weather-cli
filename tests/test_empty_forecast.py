"""A forecast that arrives EMPTY is unknown, never "nothing coming".

An empty hourly series (a provider's 200 with no hours, a cache entry from before the flag
existed, a bundle whose hours have all passed) must never be spoken as "no rain expected".
"""

from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest
from builders import HOUR, PLACE, T, bundle, moment
from payloads import NOW, ha_forecast, ha_state, iso, om_ok, open_meteo_payload, reply, router

from weather_cli.cache import MemoryStore, bundle_key, read_bundle
from weather_cli.compose import say
from weather_cli.decisions.advice import describe_dress, umbrella_advice
from weather_cli.providers.home_assistant import HaConnection, parse_home_assistant
from weather_cli.providers.json import HttpInit, JsonValue, StaticResponse
from weather_cli.providers.merge import merge_bundles
from weather_cli.providers.open_meteo import parse_open_meteo
from weather_cli.service import get_weather
from weather_cli.units import parse_iso_ms

HA = HaConnection(url="http://ha", token="t", entity_id="weather.forecast_home")
DRY_CLAIM = "дождя не ожидается"
UNKNOWN = "прогноза на ближайшие часы нет"


def empty_forecast() -> JsonValue:
    return {"changed_states": [], "service_response": {"weather.forecast_home": {"forecast": []}}}


def ha_router(forecast: JsonValue, om=None):
    state = ha_state()
    fetcher, calls = router(om=om, ha=None)

    def route(url, init):
        if "open-meteo" in url:
            return fetcher(url, init)
        return reply(forecast) if "get_forecasts" in url else reply(state)

    return route, calls


class TestUmbrellaWithoutHours:
    def test_an_empty_hourly_series_is_not_a_dry_answer(self):
        b = replace(bundle(now=NOW), hourly=[])
        advice = umbrella_advice(b, NOW, T)
        assert DRY_CLAIM not in advice.phrase
        assert UNKNOWN in advice.phrase
        assert not advice.needed

    def test_it_says_so_in_every_mode_that_speaks_the_umbrella(self):
        b = replace(bundle(now=NOW), hourly=[])
        assert UNKNOWN in say(bundle=b, now=NOW, mode="umbrella").text
        assert DRY_CLAIM not in describe_dress(b, NOW, T).phrase  # type: ignore[operator]

    def test_hours_that_have_all_passed_are_no_forecast_either(self):
        # A stale cached bundle: its 48 hours ended long before this question.
        later = NOW + 72 * HOUR
        advice = umbrella_advice(bundle(now=NOW), later, T)
        assert DRY_CLAIM not in advice.phrase
        assert UNKNOWN in advice.phrase

    def test_rain_now_with_no_hours_does_not_promise_an_end(self):
        b = replace(bundle(now=NOW, current={"condition": "rain", "precip": 1}), hourly=[])
        phrase = umbrella_advice(b, NOW, T).phrase
        assert "неизвестно" in phrase
        assert "скоро" not in phrase

    def test_a_real_dry_forecast_is_still_a_dry_answer(self):
        assert DRY_CLAIM in umbrella_advice(bundle(now=NOW), NOW, T).phrase


class TestProvidersWithAnEmptyForecast:
    def test_open_meteo_with_no_hours_is_a_missing_forecast(self):
        payload = open_meteo_payload()
        assert isinstance(payload, dict)
        payload["hourly"] = {"time": [], "temperature_2m": []}
        parsed = parse_open_meteo(payload, PLACE, NOW)
        assert parsed.hourly == []
        assert parsed.forecast_missing
        assert UNKNOWN in umbrella_advice(parsed, NOW, T).phrase

    def test_open_meteo_with_only_expired_hours_is_a_missing_forecast(self):
        payload = open_meteo_payload()
        assert isinstance(payload, dict)
        hourly = payload["hourly"]
        assert isinstance(hourly, dict) and isinstance(hourly["time"], list)
        hourly["time"] = [t - 72 * 3600 for t in hourly["time"]]
        parsed = parse_open_meteo(payload, PLACE, NOW)
        assert parsed.hourly
        assert parsed.forecast_missing

    def test_home_assistant_with_only_expired_hours_is_a_missing_forecast(self):
        old = ha_forecast("hourly")
        assert isinstance(old, dict)
        entries = old["service_response"]["weather.forecast_home"]["forecast"]  # type: ignore[index]
        for index, entry in enumerate(entries):
            entry["datetime"] = iso(NOW - 72 * HOUR + index * HOUR)
        parsed = parse_home_assistant(ha_state(), old, ha_forecast("daily"), PLACE, NOW)
        assert parsed.hourly
        assert parsed.forecast_missing

    def test_open_meteo_alone_with_only_expired_hours_is_warned_about_and_not_cached(self):
        payload = open_meteo_payload()
        assert isinstance(payload, dict)
        hourly = payload["hourly"]
        assert isinstance(hourly, dict) and isinstance(hourly["time"], list)
        hourly["time"] = [t - 72 * 3600 for t in hourly["time"]]
        fetcher, _ = router(om=lambda: reply(payload))
        store = MemoryStore()
        result = get_weather(place=PLACE, store=store, now=NOW, fetch_impl=fetcher)
        assert "open-meteo: forecast unavailable" in result.errors
        assert read_bundle(store, bundle_key(PLACE)) is None

    def test_open_meteo_with_hours_is_not_marked_missing(self):
        assert not parse_open_meteo(open_meteo_payload(), PLACE, NOW).forecast_missing

    def test_home_assistant_with_an_empty_hourly_list_is_a_missing_forecast(self):
        empty = empty_forecast()
        parsed = parse_home_assistant(ha_state(), empty, ha_forecast("daily"), PLACE, NOW)
        assert parsed.hourly == []
        assert parsed.forecast_missing
        assert UNKNOWN in umbrella_advice(parsed, NOW, T).phrase

    def test_home_assistant_with_hours_is_not_marked_missing(self):
        parsed = parse_home_assistant(
            ha_state(), ha_forecast("hourly"), ha_forecast("daily"), PLACE, NOW
        )
        assert not parsed.forecast_missing


class TestServiceFallsBack:
    def test_ha_alone_with_empty_forecasts_answers_unknown_and_is_not_cached(self):
        fetcher, _ = ha_router(empty_forecast())
        store = MemoryStore()
        result = get_weather(
            place=PLACE, ha=HA, open_meteo=False, store=store, now=NOW, fetch_impl=fetcher
        )
        assert result.origin == "live"
        assert result.bundle.forecast_missing
        assert "home-assistant: forecast unavailable" in result.errors
        assert UNKNOWN in umbrella_advice(result.bundle, NOW, T).phrase
        assert read_bundle(store, bundle_key(PLACE)) is None

    def test_an_empty_ha_forecast_falls_back_to_open_meteo_hours(self):
        fetcher, _ = ha_router(empty_forecast(), om=om_ok)
        result = get_weather(place=PLACE, ha=HA, store=MemoryStore(), now=NOW, fetch_impl=fetcher)
        assert result.bundle.hourly
        assert not result.bundle.forecast_missing
        advice = umbrella_advice(result.bundle, NOW, T)
        assert advice.needed  # Open-Meteo has rain at 16:00

    def test_open_meteo_alone_with_no_hours_is_unknown_and_not_cached(self):
        payload = open_meteo_payload()
        assert isinstance(payload, dict)
        payload["hourly"] = {"time": []}
        fetcher, _ = router(om=lambda: reply(payload))
        store = MemoryStore()
        result = get_weather(place=PLACE, store=store, now=NOW, fetch_impl=fetcher)
        assert result.bundle.forecast_missing
        assert UNKNOWN in umbrella_advice(result.bundle, NOW, T).phrase
        assert read_bundle(store, bundle_key(PLACE)) is None


class TestMergedFlag:
    @pytest.mark.parametrize(
        ("primary_hours", "secondary_hours", "missing"),
        [(False, True, False), (True, False, False), (False, False, True), (True, True, False)],
    )
    def test_the_merged_bundle_is_missing_only_when_no_source_has_hours(
        self, primary_hours, secondary_hours, missing
    ):
        def source(has_hours: bool):
            b = bundle(now=NOW)
            return b if has_hours else replace(b, hourly=[], forecast_missing=True)

        merged = merge_bundles(source(primary_hours), source(secondary_hours), NOW)
        assert merged.forecast_missing is missing
        assert bool(merged.hourly) is (not missing)


def ha_hours(count: int, shift_ms: float = 0) -> JsonValue:
    """HA's hourly answer cut to `count` entries (the fourth is rainy), moved by `shift_ms`."""
    full = ha_forecast("hourly")
    assert isinstance(full, dict)
    entries = full["service_response"]["weather.forecast_home"]["forecast"][:count]  # type: ignore[index]
    for entry in entries:
        entry["datetime"] = iso(parse_iso_ms(entry["datetime"]) + shift_ms)
    return {
        "changed_states": [],
        "service_response": {"weather.forecast_home": {"forecast": entries}},
    }


class TestMergeChoosesUsableHours:
    def test_ha_hours_that_still_lie_ahead_beat_a_thinner_open_meteo_answer(self):
        # Eight HA entries, the first three already past (so five usable, rain in the fourth hour
        # of the series), against an Open-Meteo answer that holds only the current dry hour.
        full = ha_hours(8)
        assert isinstance(full, dict)
        entries = full["service_response"]["weather.forecast_home"]["forecast"]  # type: ignore[index]
        for entry in entries[:3]:
            entry["datetime"] = iso(parse_iso_ms(entry["datetime"]) - 72 * HOUR)
        payload = open_meteo_payload()
        assert isinstance(payload, dict)
        hourly = payload["hourly"]
        assert isinstance(hourly, dict)
        hourly["time"] = hourly["time"][:2]
        fetcher, _ = ha_router(full, om=lambda: reply(payload))
        store = MemoryStore()
        result = get_weather(place=PLACE, ha=HA, store=store, now=NOW, fetch_impl=fetcher)
        assert umbrella_advice(result.bundle, NOW, T).needed
        assert not result.bundle.forecast_missing

    def test_expired_ha_hours_do_not_override_a_fresh_open_meteo_forecast(self):
        fetcher, _ = ha_router(ha_hours(8, shift_ms=-72 * HOUR), om=om_ok)
        store = MemoryStore()
        result = get_weather(place=PLACE, ha=HA, store=store, now=NOW, fetch_impl=fetcher)
        assert not result.bundle.forecast_missing
        assert umbrella_advice(result.bundle, NOW, T).needed  # Open-Meteo rains at 16:00
        assert read_bundle(store, bundle_key(PLACE)) is not None

    def test_a_short_but_valid_ha_forecast_is_kept_when_open_meteo_has_no_hours(self):
        payload = open_meteo_payload()
        assert isinstance(payload, dict)
        payload["hourly"] = {"time": []}
        fetcher, _ = ha_router(ha_hours(5), om=lambda: reply(payload))
        result = get_weather(place=PLACE, ha=HA, store=MemoryStore(), now=NOW, fetch_impl=fetcher)
        assert len(result.bundle.hourly) == 5
        assert not result.bundle.forecast_missing
        assert umbrella_advice(result.bundle, NOW, T).needed  # HA rains in the fourth hour

    def test_a_short_primary_that_wins_keeps_the_secondarys_hours_after_its_last(self):
        # Five dry primary hours (now..+4h) against four secondary ones (now, +1h, +2h, +8h
        # with rain): the primary leads where it has hours, the rain at +8h must survive.
        def series(offsets, **over):
            return [moment(NOW + h * HOUR, precip=0, **over) for h in offsets]

        primary = replace(bundle(now=NOW), hourly=series(range(5)), sources=["home-assistant"])
        rainy = moment(NOW + 8 * HOUR, precip=3, condition="rain")
        secondary = replace(bundle(now=NOW), hourly=[*series((0, 1, 2)), rainy])
        merged = merge_bundles(primary, secondary, NOW)
        assert [m.time for m in merged.hourly] == [NOW + h * HOUR for h in (0, 1, 2, 3, 4, 8)]
        assert merged.hourly[-1] is rainy
        assert umbrella_advice(merged, NOW, T).needed

    def test_a_short_primary_with_a_gap_takes_the_secondarys_hour_inside_it(self):
        def series(offsets, **over):
            return [moment(NOW + h * HOUR, precip=0, **over) for h in offsets]

        primary = replace(
            bundle(now=NOW), hourly=series((0, 1, 3, 4, 5)), sources=["home-assistant"]
        )
        rainy = moment(NOW + 2 * HOUR, precip=3, condition="rain")
        secondary = replace(bundle(now=NOW), hourly=[*series((0, 1)), rainy, *series((6,))])
        merged = merge_bundles(primary, secondary, NOW)
        assert [m.time for m in merged.hourly] == [NOW + h * HOUR for h in range(7)]
        assert umbrella_advice(merged, NOW, T).needed

    def test_a_short_secondary_that_wins_keeps_the_primarys_hours_it_lacks(self):
        # Primary: six hours with rain at +2h, the first one already past (five usable). Secondary:
        # six dry hours that skip +2h (a null row). The secondary leads on count; the rain stays.
        def series(offsets, **over):
            return [moment(NOW + h * HOUR, precip=0, **over) for h in offsets]

        rainy = moment(NOW + 2 * HOUR, precip=3, condition="rain")
        primary = replace(
            bundle(now=NOW),
            hourly=[*series((-2, 0, 1)), rainy, *series((3, 4))],
            sources=["home-assistant"],
        )
        secondary = replace(bundle(now=NOW), hourly=series((0, 1, 3, 4, 5, 6)))
        merged = merge_bundles(primary, secondary, NOW)
        assert NOW + 2 * HOUR in {m.time for m in merged.hourly}
        assert NOW - 2 * HOUR not in {m.time for m in merged.hourly}
        assert umbrella_advice(merged, NOW, T).needed

    def test_a_shifted_hour_grid_is_not_a_gap(self):
        # Primary rows sit at :30, the secondary's at :00 and it leads on count: the primary's
        # rows lie inside the secondary's coverage, so they are not added back.
        def series(offsets, **over):
            return [moment(NOW + h * HOUR, precip=0, **over) for h in offsets]

        rainy = moment(NOW + HOUR // 2, precip=3, condition="rain")
        primary = replace(
            bundle(now=NOW), hourly=[rainy, *series((1.5, 2.5, 3.5))], sources=["home-assistant"]
        )
        secondary = replace(bundle(now=NOW), hourly=series(range(13)))
        merged = merge_bundles(primary, secondary, NOW)
        assert [m.time for m in merged.hourly] == [NOW + h * HOUR for h in range(13)]

    def test_a_winning_secondary_is_filled_from_the_primarys_matching_hours(self):
        def series(offsets, **over):
            return [moment(NOW + h * HOUR, **over) for h in offsets]

        primary = replace(
            bundle(now=NOW),
            hourly=[
                *series((-2, 0, 1), precip=0),
                moment(NOW + 2 * HOUR, precip=3),
                *series((3, 4), precip=0),
            ],
            sources=["home-assistant"],
        )
        secondary = replace(
            bundle(now=NOW),
            hourly=[
                moment(NOW + h * HOUR, precip=None, condition="cloudy") for h in (0, 1, 2, 3, 4, 5)
            ],
        )
        merged = merge_bundles(primary, secondary, NOW)
        assert [m.precip for m in merged.hourly if m.time == NOW + 2 * HOUR] == [3]
        assert umbrella_advice(merged, NOW, T).needed

    def test_unusable_leader_hours_do_not_cover_the_others(self):
        def series(offsets, **over):
            return [moment(NOW + h * HOUR, precip=0, **over) for h in offsets]

        # Primary rows at 0..3h and one just past the 12 h horizon; the secondary leads on count
        # and holds rain at +12h, which the primary's +12.25h row must not hide.
        primary = replace(
            bundle(now=NOW),
            hourly=series((0, 1, 2, 3, 12.25)),
            sources=["home-assistant"],
        )
        rainy = moment(NOW + 12 * HOUR, precip=3, condition="rain")
        secondary = replace(
            bundle(now=NOW), hourly=[*series((0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)), rainy]
        )
        merged = merge_bundles(primary, secondary, NOW)
        assert rainy in merged.hourly
        # An expired row half an hour back does not hide the hour now either.
        stale = replace(
            bundle(now=NOW), hourly=[moment(NOW - HOUR // 2, precip=0), *series((1, 2, 3, 4, 5))]
        )
        now_rain = replace(bundle(now=NOW), hourly=[moment(NOW, precip=3), *series((1, 2))])
        kept = merge_bundles(stale, now_rain, NOW)
        assert any(m.time == NOW and m.precip == 3 for m in kept.hourly)

    def test_the_losing_sources_rows_beyond_the_horizon_are_not_added_beside_the_leaders(self):
        def series(offsets, **over):
            return [moment(NOW + h * HOUR, precip=0, **over) for h in offsets]

        # The secondary leads and has a dry row three minutes past the 12 h horizon; the
        # primary's conflicting rain row there must not ride along (it would count once the
        # cached bundle is reused a few minutes later).
        edge = 12 * HOUR + 3 * 60_000
        primary = replace(
            bundle(now=NOW),
            hourly=[*series((0, 1, 2, 3)), moment(NOW + edge, precip=3, condition="rain")],
            sources=["home-assistant"],
        )
        secondary = replace(
            bundle(now=NOW), hourly=[*series(range(12)), moment(NOW + edge, precip=0)]
        )
        merged = merge_bundles(primary, secondary, NOW)
        assert [m.precip for m in merged.hourly if m.time == NOW + edge] == [0]

    def test_duplicate_rows_of_one_hour_are_not_coverage(self):
        dry = moment(NOW + HOUR, precip=0)
        rainy = moment(NOW + HOUR, precip=3, condition="rain")
        primary = replace(bundle(now=NOW), hourly=[dry] * 5, sources=["home-assistant"])
        secondary = replace(
            bundle(now=NOW),
            hourly=[
                moment(NOW, precip=0),
                rainy,
                moment(NOW + 2 * HOUR, precip=0),
                moment(NOW + 3 * HOUR, precip=0),
            ],
        )
        merged = merge_bundles(primary, secondary, NOW)
        assert rainy in merged.hourly

    def test_a_flood_of_duplicate_timestamps_is_merged_quickly(self):
        flood = [moment(NOW + HOUR, precip=0)] * 20_000
        primary = replace(bundle(now=NOW), hourly=[moment(NOW + 24 * HOUR)] * 20_000)
        secondary = replace(bundle(now=NOW), hourly=flood)
        started = time.monotonic()
        merge_bundles(primary, secondary, NOW)
        assert time.monotonic() - started < 2

    def test_a_long_valid_ha_forecast_still_leads(self):
        fetcher, _ = ha_router(ha_hours(8), om=om_ok)
        result = get_weather(place=PLACE, ha=HA, store=MemoryStore(), now=NOW, fetch_impl=fetcher)
        assert len(result.bundle.hourly) == 8


def test_merge_keeps_an_unmatched_rain_hour_past_the_horizon_for_the_cache_reuse() -> None:
    raw = ha_forecast("hourly")
    rows = raw["service_response"]["weather.forecast_home"]["forecast"]
    dry = [
        {**rows[0], "datetime": iso(NOW + h * HOUR), "condition": "cloudy", "precipitation": 0}
        for h in range(5)
    ]
    rain = {
        **rows[0],
        "datetime": iso(NOW + 12 * HOUR + 3 * 60_000),
        "condition": "rainy",
        "precipitation": 3,
    }
    raw["service_response"]["weather.forecast_home"]["forecast"] = [*dry, rain]
    om = open_meteo_payload()
    om["hourly"]["time"] = [int((NOW + h * HOUR) // 1000) for h in range(7)]
    for key, value in (("precipitation", 0), ("precipitation_probability", 0), ("weather_code", 3)):
        om["hourly"][key] = [value] * 7

    def fetch(url: str, init: HttpInit) -> StaticResponse:
        if "open-meteo" in url:
            return reply(om)
        if "get_forecasts" in url:
            return reply(raw if json.loads(init.body)["type"] == "hourly" else ha_forecast("daily"))
        return reply(ha_state())

    ha = HaConnection(url="http://ha", token="t", entity_id="weather.forecast_home")
    store = MemoryStore()
    get_weather(place=PLACE, ha=ha, store=store, now=NOW, fetch_impl=fetch)
    later = get_weather(place=PLACE, ha=ha, store=store, now=NOW + 5 * 60_000, fetch_impl=fetch)
    assert later.origin == "cache"
    assert "Нет" not in say(bundle=later.bundle, now=NOW + 5 * 60_000, mode="umbrella").text
