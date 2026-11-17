"""Cache edge cases, the provider fallback matrix (service and CLI), units and time zones."""

from __future__ import annotations

from pathlib import Path

import pytest
from builders import HOUR, PLACE, bundle
from payloads import NOW, ha_forecast, ha_state, om_ok, open_meteo_payload, reply, router
from runner import weather

import weather_cli.providers.json as pjson
from weather_cli.cache import MemoryStore, bundle_key, bundle_to_json, read_bundle, write_bundle
from weather_cli.cache_fs import FileStore
from weather_cli.cli.main import main
from weather_cli.providers.home_assistant import HaConnection, parse_home_assistant
from weather_cli.service import get_weather

HA = HaConnection(url="http://ha", token="t", entity_id="weather.forecast_home")


def seeded(fetched_at: float) -> MemoryStore:
    store = MemoryStore()
    write_bundle(store, bundle_key(PLACE), bundle(now=fetched_at, current={"temp": 1}))
    return store


class TestCacheEdges:
    def test_a_slightly_future_cache_entry_is_fresh(self):
        # A few minutes of clock skew between writer and reader is tolerated.
        fetcher, calls = router()
        result = get_weather(
            place=PLACE, store=seeded(NOW + 4 * 60_000), now=NOW, fetch_impl=fetcher
        )
        assert result.origin == "cache"
        assert calls == []

    def test_a_far_future_cache_entry_is_a_miss_and_is_replaced(self):
        # A bad clock (or a fake --at) once stamped this; it must not stay "fresh" until then.
        store = seeded(NOW + HOUR)
        fetcher, calls = router(om=om_ok)
        result = get_weather(place=PLACE, store=store, now=NOW, fetch_impl=fetcher)
        assert result.origin == "live"
        assert calls != []
        stored = read_bundle(store, bundle_key(PLACE))
        assert stored is not None
        assert stored.fetched_at == NOW

    def test_a_falsy_but_present_store_is_still_used(self):
        class EmptyDict(dict):
            """A host store that is falsy while empty."""

            def read(self, key):
                return self.get(key)

            def write(self, key, value):
                self[key] = value

        store = EmptyDict()
        fetcher, _ = router(om=om_ok)
        get_weather(place=PLACE, store=store, now=NOW, fetch_impl=fetcher)
        assert bundle_key(PLACE) in store

    def test_offline_never_fetches_and_an_empty_cache_is_an_error(self):
        fetcher, calls = router(om=om_ok)
        with pytest.raises(RuntimeError, match="offline"):
            get_weather(place=PLACE, store=MemoryStore(), now=NOW, offline=True, fetch_impl=fetcher)
        assert calls == []

    def test_offline_serves_an_old_cache_of_any_age_with_no_request(self):
        fetcher, calls = router(om=om_ok)
        result = get_weather(
            place=PLACE,
            store=seeded(NOW - 30 * HOUR),
            now=NOW,
            offline=True,
            fetch_impl=fetcher,
        )
        assert (result.origin, result.errors, calls) == ("cache", [], [])

    def test_offline_does_not_resurrect_a_corrupt_cache_by_fetching(self):
        store = MemoryStore()
        store.write(bundle_key(PLACE), "\x00garbage")
        fetcher, calls = router(om=om_ok)
        with pytest.raises(RuntimeError):
            get_weather(place=PLACE, store=store, now=NOW, offline=True, fetch_impl=fetcher)
        assert calls == []

    def test_a_single_provider_bundle_names_its_current_source(self):
        fetcher, _ = router(om=om_ok)
        result = get_weather(place=PLACE, store=MemoryStore(), now=NOW, fetch_impl=fetcher)
        assert result.bundle.sources == ["open-meteo"]
        assert result.bundle.current_source == "open-meteo"
        only_ha = get_weather(
            place=PLACE,
            ha=HA,
            open_meteo=False,
            now=NOW,
            fetch_impl=ha_router(),
        )
        assert only_ha.bundle.current_source == "home-assistant"

    @pytest.mark.parametrize(
        "text", ["", "\x00\x01", '{"schema": 1}', '{"schema": 1, "bundle": 5}']
    )
    def test_a_corrupt_cache_file_is_refetched(self, text):
        store = MemoryStore()
        store.write(bundle_key(PLACE), text)
        result = get_weather(place=PLACE, store=store, now=NOW, fetch_impl=router(om=om_ok)[0])
        assert result.origin == "live"
        assert result.bundle.current.temp == 7.4


def ha_router(state_body=None, om=om_ok):
    def ha_handler_for(url: str, init: pjson.HttpInit) -> pjson.HttpResponse:
        if "get_forecasts" in url:
            return reply(ha_forecast("hourly" if "hourly" in (init.body or "") else "daily"))
        return reply(state_body or ha_state())

    base, _ = router(om=om)

    def fetcher(url: str, init: pjson.HttpInit) -> pjson.HttpResponse:
        return base(url, init) if "open-meteo" in url else ha_handler_for(url, init)

    return fetcher


class TestFallbackMatrix:
    def test_ha_down_open_meteo_ok(self):
        result = get_weather(place=PLACE, now=NOW, ha=HA, fetch_impl=router(om=om_ok)[0])
        assert result.bundle.sources == ["open-meteo"]
        assert [e.split(":")[0] for e in result.errors] == ["home-assistant"]

    def test_ha_ok_open_meteo_down(self):
        result = get_weather(place=PLACE, now=NOW, ha=HA, fetch_impl=ha_router(om=None))
        assert result.bundle.sources == ["home-assistant"]
        assert [e.split(":")[0] for e in result.errors] == ["open-meteo"]

    def test_ha_unavailable_falls_back_to_open_meteo(self):
        state = {"entity_id": "weather.forecast_home", "state": "unavailable", "attributes": {}}
        result = get_weather(place=PLACE, now=NOW, ha=HA, fetch_impl=ha_router(state))
        assert result.bundle.sources == ["open-meteo"]
        assert "unavailable" in ",".join(result.errors)

    def test_both_down_with_a_fresh_cache_does_no_network(self):
        fetcher, calls = router()
        result = get_weather(
            place=PLACE, store=seeded(NOW - 5 * 60_000), now=NOW, ha=HA, fetch_impl=fetcher
        )
        assert (result.origin, calls) == ("cache", [])

    def test_both_down_with_a_stale_cache_reports_both_errors(self):
        result = get_weather(
            place=PLACE, store=seeded(NOW - 3 * HOUR), now=NOW, ha=HA, fetch_impl=router()[0]
        )
        assert result.origin == "stale"
        assert len(result.errors) == 2

    def test_both_down_without_cache_names_both_providers(self):
        with pytest.raises(RuntimeError) as caught:
            get_weather(place=PLACE, now=NOW, ha=HA, fetch_impl=router()[0])
        assert "home-assistant" in str(caught.value)
        assert "open-meteo" in str(caught.value)


def run_cli(args: list[str], cache: Path, monkeypatch, capsys) -> tuple[int, str, str]:
    """`main()` in-process, exactly as the console script runs it, with the network down."""

    def offline(_url: str, _init: pjson.HttpInit) -> pjson.HttpResponse:
        raise OSError("offline")

    monkeypatch.setattr(pjson, "default_fetch", offline)
    monkeypatch.setenv("HOME", str(cache.parent))
    monkeypatch.setenv("WEATHER_CACHE_DIR", str(cache))
    code = main(["say", "--lat", "44.8", "--lon", "20.48", "--tz", "Europe/Belgrade", *args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.usefixtures("scrubbed_env")
class TestCliFallback:
    AT = ["--at", "2026-11-09T14:10:00+01:00"]

    def test_stale_cache_warns_and_exits_0(self, tmp_path, monkeypatch, capsys):
        FileStore(tmp_path).write(
            bundle_key(PLACE),
            pjson.dumps(
                {
                    "schema": 1,
                    "bundle": bundle_to_json(bundle(now=NOW - 3 * HOUR)),
                }
            ),
        )
        code, out, err = run_cli(self.AT, tmp_path, monkeypatch, capsys)
        assert code == 0
        assert out.strip() != ""
        assert err == (
            "warning: open-meteo: request failed (offline)\n"
            "warning: offline, using data from 2026-11-09 11:10\n"
        )

    def test_no_cache_exits_1(self, tmp_path, monkeypatch, capsys):
        code, out, err = run_cli(self.AT, tmp_path, monkeypatch, capsys)
        assert (code, out) == (1, "")
        assert err == ("weather: no weather data: open-meteo: request failed (offline)\n")

    def test_offline_flag_serves_an_old_cache_without_network(self, tmp_path, monkeypatch, capsys):
        FileStore(tmp_path).write(
            bundle_key(PLACE),
            pjson.dumps(
                {
                    "schema": 1,
                    "bundle": bundle_to_json(bundle(now=NOW - 2 * HOUR)),
                }
            ),
        )
        code, _, err = run_cli([*self.AT, "--offline"], tmp_path, monkeypatch, capsys)
        assert (code, err) == (0, "")

    def test_offline_flag_with_no_cache_exits_1_without_touching_the_network(
        self, tmp_path, monkeypatch, capsys
    ):
        code, out, err = run_cli([*self.AT, "--offline"], tmp_path, monkeypatch, capsys)
        assert (code, out) == (1, "")
        assert err == "weather: no weather data: offline and nothing cached\n"


class TestUnits:
    def test_open_meteo_wind_is_taken_as_metres_per_second(self):
        from weather_cli.providers.open_meteo import open_meteo_url, parse_open_meteo

        assert "wind_speed_unit=ms" in open_meteo_url(PLACE)
        parsed = parse_open_meteo(open_meteo_payload(), PLACE, NOW)
        assert (parsed.current.wind, parsed.current.gust) == (7, 12)

    @pytest.mark.parametrize(
        ("unit", "expected"),
        [("m/s", 10), ("kn", 5.14444), ("ft/s", 3.048), ("km/h", 10 / 3.6), ("odd", 10 / 3.6)],
    )
    def test_home_assistant_wind_units(self, unit, expected):
        state = {
            "entity_id": "weather.x",
            "state": "sunny",
            "attributes": {"temperature": 5, "wind_speed": 10, "wind_speed_unit": unit},
        }
        wind = parse_home_assistant(state, None, None, PLACE, NOW).current.wind
        assert wind == pytest.approx(expected, abs=1e-5)

    def test_inches_become_millimetres(self):
        state = {
            "entity_id": "weather.x",
            "state": "rainy",
            "attributes": {"temperature": 5, "precipitation_unit": "in"},
        }
        forecast = {
            "service_response": {
                "weather.x": {
                    "forecast": [
                        {
                            "datetime": "2026-11-09T10:00:00+00:00",
                            "temperature": 5,
                            "precipitation": 1,
                        }
                    ]
                }
            }
        }
        hourly = parse_home_assistant(state, forecast, None, PLACE, NOW).hourly
        assert hourly[0].precip == pytest.approx(25.4)


class TestTimeZones:
    def test_place_zone_not_host_zone_decides_the_words(self):
        outputs = {}
        for host in ("America/Los_Angeles", "Asia/Tokyo"):
            _, outputs[host], _ = weather(
                "say",
                "--bundle",
                "examples/rain-wind-evening.json",
                "--at",
                "2026-11-09T21:40:00+01:00",
                extra={"TZ": host},
            )
        assert outputs["America/Los_Angeles"] == outputs["Asia/Tokyo"]
        assert "Завтра" in outputs["Asia/Tokyo"]
