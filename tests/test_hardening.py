"""Regressions for the review findings: credentials, timeouts, clock skew, bad numbers and files."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from builders import bundle
from payloads import NOW, ha_forecast, ha_state, om_ok, open_meteo_payload, reply
from runner import ROOT, weather

import weather_cli.cli.main as cli_main
import weather_cli.providers.json as pjson
from weather_cli.cache import MemoryStore, bundle_to_json, decode_bundle_text
from weather_cli.cache_fs import FileStore
from weather_cli.cli import config
from weather_cli.cli.config import FileConfig, PlaceFlags, read_file_config, resolve_place
from weather_cli.compose import say
from weather_cli.providers.home_assistant import (
    HaConnection,
    fetch_home_assistant,
    parse_home_assistant,
)
from weather_cli.providers.json import HttpInit, ProviderError, as_number, request_json
from weather_cli.providers.open_meteo import parse_open_meteo
from weather_cli.service import get_weather
from weather_cli.types import Place
from weather_cli.units import is_plausible_instant, parse_iso_ms, parse_js_number

SECRET = "s3cret-token"


class Origin:
    """A throwaway local HTTP server; `answer(handler)` decides each reply, `seen` records them."""

    def __init__(self, answer) -> None:
        self.seen: list[dict[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.seen.append({k.lower(): v for k, v in self.headers.items()})
                answer(self)

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("content-length", 0)))
                self.do_GET()

            def log_message(self, *_args) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def ok(handler: BaseHTTPRequestHandler) -> None:
    body = b'{"ok": true}'
    handler.send_response(200)
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def redirect_to(target: str):
    def answer(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(302)
        handler.send_header("location", target)
        handler.send_header("content-length", "0")
        handler.end_headers()

    return answer


def authorised(timeout_s: float = 5) -> HttpInit:
    return HttpInit(
        method="GET", headers={"authorization": f"Bearer {SECRET}"}, timeout_s=timeout_s
    )


@pytest.fixture
def origins(monkeypatch):
    # The module's opener was built at import with the environment's proxy settings; a proxy
    # would swallow requests to 127.0.0.1 and make these tests fail like a security regression.
    monkeypatch.setattr(
        pjson,
        "_OPENER",
        pjson._build_opener(urllib.request.ProxyHandler({})),
    )
    made: list[Origin] = []

    def make(answer) -> Origin:
        made.append(Origin(answer))
        return made[-1]

    yield make
    for origin in made:
        origin.close()


class TestRedirects:
    def test_the_token_is_not_sent_to_another_origin(self, origins):
        elsewhere = origins(ok)
        home = origins(redirect_to(f"{elsewhere.url}/landing"))
        response = pjson.default_fetch(f"{home.url}/api", authorised())
        assert response.ok
        assert home.seen[0]["authorization"] == f"Bearer {SECRET}"
        assert "authorization" not in elsewhere.seen[0]

    def test_the_token_stays_on_a_redirect_within_the_same_origin(self, origins):
        holder: dict[str, Origin] = {}

        def answer(handler: BaseHTTPRequestHandler) -> None:
            if handler.path == "/final":
                ok(handler)
            else:
                redirect_to(f"{holder['same'].url}/final")(handler)

        holder["same"] = origins(answer)
        pjson.default_fetch(f"{holder['same'].url}/start", authorised())
        assert [seen["authorization"] for seen in holder["same"].seen] == [f"Bearer {SECRET}"] * 2

    def test_only_http_and_https_urls_are_fetched(self):
        with pytest.raises(OSError, match="http"):
            pjson.default_fetch("file:///etc/passwd", authorised())
        with pytest.raises(OSError, match="http"):
            pjson.default_fetch("ftp://127.0.0.1/x", authorised())

    def test_a_redirect_to_another_scheme_is_refused(self, origins):
        home = origins(redirect_to("ftp://127.0.0.1/secret"))
        response = pjson.default_fetch(f"{home.url}/api", authorised())
        assert not response.ok

    def test_an_https_to_http_downgrade_is_refused(self):
        handler = pjson._SafeRedirects()
        request = urllib.request.Request("https://ha.example/api", headers={"authorization": "x"})
        with pytest.raises(urllib.error.HTTPError):
            handler.redirect_request(request, None, 302, "Found", {}, "http://ha.example/api")


class TestResponseSize:
    def test_a_response_beyond_the_cap_is_an_error(self, origins, monkeypatch):
        monkeypatch.setattr(pjson, "MAX_RESPONSE_BYTES", 50)

        def big(handler: BaseHTTPRequestHandler) -> None:
            body = b"[" + b"1," * 100 + b"1]"
            handler.send_response(200)
            handler.send_header("content-length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        home = origins(big)
        with pytest.raises(ProviderError, match="response too large"):
            request_json("open-meteo", f"{home.url}/api", timeout_ms=5000)

    def test_a_response_within_the_cap_is_read(self, origins):
        home = origins(ok)
        assert request_json("open-meteo", f"{home.url}/api", timeout_ms=5000) == {"ok": True}

    def test_an_error_status_is_an_error_response_not_an_exception(self, origins):
        def missing(handler: BaseHTTPRequestHandler) -> None:
            handler.send_response(404)
            handler.send_header("content-length", "0")
            handler.end_headers()

        home = origins(missing)
        with pytest.raises(ProviderError, match="HTTP 404"):
            request_json("open-meteo", f"{home.url}/api", timeout_ms=5000)


class TestSlowDrip:
    def test_a_peer_dripping_bytes_is_cut_at_the_deadline(self, origins):
        def drip(handler: BaseHTTPRequestHandler) -> None:
            handler.send_response(200)
            handler.send_header("content-length", "1000")
            handler.end_headers()
            try:
                for _ in range(200):
                    handler.wfile.write(b" ")
                    handler.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass

        home = origins(drip)
        started = time.monotonic()
        with pytest.raises(ProviderError, match=r"request failed \(timeout\)"):
            request_json("open-meteo", f"{home.url}/api", timeout_ms=400)
        # Each byte arrives well inside the socket timeout: only the wall-clock deadline stops it.
        assert time.monotonic() - started < 5


class TestRedirectBodies:
    """The size cap and the wall-clock deadline hold for the body of a redirect answer too:
    `urllib` swallows it before following, unbounded, unless the handler reads it first."""

    @staticmethod
    def redirect_with_body(target: str, body: bytes):
        def answer(handler: BaseHTTPRequestHandler) -> None:
            handler.send_response(302)
            handler.send_header("location", target)
            handler.send_header("content-length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        return answer

    def test_a_redirect_body_beyond_the_cap_is_an_error(self, origins, monkeypatch):
        monkeypatch.setattr(pjson, "MAX_RESPONSE_BYTES", 50)
        elsewhere = origins(ok)
        home = origins(self.redirect_with_body(f"{elsewhere.url}/x", b"x" * 500))
        with pytest.raises(ProviderError, match="response too large"):
            request_json("open-meteo", f"{home.url}/api", timeout_ms=5000)
        assert elsewhere.seen == []

    def test_the_cap_is_shared_by_every_hop_of_a_chain(self, origins, monkeypatch):
        monkeypatch.setattr(pjson, "MAX_RESPONSE_BYTES", 100)
        holder: dict[str, Origin] = {}

        def answer(handler: BaseHTTPRequestHandler) -> None:
            hop = int(handler.path.strip("/") or 0)
            if hop < 5:
                self.redirect_with_body(f"{holder['o'].url}/{hop + 1}", b"x" * 30)(handler)
            else:
                ok(handler)

        holder["o"] = origins(answer)
        with pytest.raises(ProviderError, match="response too large"):
            request_json("open-meteo", f"{holder['o'].url}/0", timeout_ms=5000)

    def test_a_redirect_body_within_the_cap_is_followed(self, origins):
        elsewhere = origins(ok)
        home = origins(self.redirect_with_body(f"{elsewhere.url}/x", b"moved"))
        assert request_json("open-meteo", f"{home.url}/api", timeout_ms=5000) == {"ok": True}

    def test_a_peer_dripping_a_redirect_body_is_cut_at_the_deadline(self, origins):
        elsewhere = origins(ok)

        def drip(handler: BaseHTTPRequestHandler) -> None:
            handler.send_response(302)
            handler.send_header("location", f"{elsewhere.url}/x")
            handler.send_header("content-length", "1000")
            handler.end_headers()
            try:
                for _ in range(200):
                    handler.wfile.write(b" ")
                    handler.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass

        home = origins(drip)
        started = time.monotonic()
        with pytest.raises(ProviderError, match=r"request failed \(timeout\)"):
            request_json("open-meteo", f"{home.url}/api", timeout_ms=400)
        assert time.monotonic() - started < 5
        assert elsewhere.seen == []

    @staticmethod
    def spying_opener(monkeypatch):
        """Records every redirect answer the handler is given, to see what became of it."""
        seen: list = []

        class Spy(pjson._SafeRedirects):
            def redirect_request(self, req, fp, *rest):
                seen.append(fp)
                return super().redirect_request(req, fp, *rest)

        opener = pjson._build_opener(urllib.request.ProxyHandler({}), redirects=Spy())
        monkeypatch.setattr(pjson, "_OPENER", opener)
        return seen

    @pytest.mark.parametrize("code", [307, 308])
    @pytest.mark.parametrize("body_kind", ["stalled", "oversized"])
    def test_a_post_that_cannot_be_replayed_answers_its_status_without_draining_the_body(
        self, origins, monkeypatch, code, body_kind
    ):
        monkeypatch.setattr(pjson, "MAX_RESPONSE_BYTES", 50)
        seen = self.spying_opener(monkeypatch)
        elsewhere = origins(ok)

        def refuse(handler: BaseHTTPRequestHandler) -> None:
            handler.send_response(code)
            handler.send_header("location", f"{elsewhere.url}/x")
            handler.send_header("content-length", "200000")
            handler.end_headers()
            if body_kind == "oversized":
                handler.wfile.write(b"x" * 200_000)
            else:
                time.sleep(2)

        home = origins(refuse)
        started = time.monotonic()
        with pytest.raises(ProviderError, match=f"HTTP {code}"):
            request_json("home-assistant", f"{home.url}/api", timeout_ms=1500, body={"a": 1})
        assert time.monotonic() - started < 1
        assert elsewhere.seen == []
        # Python 3.10 has no 308 handler: the answer never reaches the redirect handler there.
        if hasattr(urllib.request.HTTPRedirectHandler, "http_error_308") or code == 307:
            assert [fp.isclosed() for fp in seen] == [True]

    def test_a_redirect_answer_over_the_cap_is_closed_not_left_to_the_collector(
        self, origins, monkeypatch
    ):
        monkeypatch.setattr(pjson, "MAX_RESPONSE_BYTES", 50)
        seen = self.spying_opener(monkeypatch)
        elsewhere = origins(ok)
        # More than one read's worth, so the answer is still open when the cap is hit.
        home = origins(self.redirect_with_body(f"{elsewhere.url}/x", b"x" * 200_000))
        with pytest.raises(ProviderError, match="response too large"):
            request_json("open-meteo", f"{home.url}/api", timeout_ms=5000)
        assert [fp.isclosed() for fp in seen] == [True]

    def test_a_redirect_answer_cut_at_the_deadline_is_closed(self, origins, monkeypatch):
        seen = self.spying_opener(monkeypatch)
        elsewhere = origins(ok)

        def drip(handler: BaseHTTPRequestHandler) -> None:
            handler.send_response(302)
            handler.send_header("location", f"{elsewhere.url}/x")
            handler.send_header("content-length", "1000")
            handler.end_headers()
            try:
                for _ in range(40):
                    handler.wfile.write(b" ")
                    handler.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass

        home = origins(drip)
        with pytest.raises(ProviderError, match=r"request failed \(timeout\)"):
            request_json("open-meteo", f"{home.url}/api", timeout_ms=300)
        assert [fp.isclosed() for fp in seen] == [True]

    def test_a_redirect_that_ends_after_the_deadline_is_not_followed(self, origins):
        elsewhere = origins(ok)

        def late_end(handler: BaseHTTPRequestHandler) -> None:
            handler.send_response(302)
            handler.send_header("location", f"{elsewhere.url}/x")
            handler.send_header("content-length", "2")
            handler.end_headers()
            time.sleep(0.25)
            handler.wfile.write(b"x")
            handler.wfile.flush()
            time.sleep(0.25)  # the connection ends after the 400 ms deadline, a byte short

        home = origins(late_end)
        with pytest.raises(ProviderError, match=r"request failed \(timeout\)"):
            request_json("open-meteo", f"{home.url}/api", timeout_ms=400)
        assert elsewhere.seen == []

    @staticmethod
    def chunked_trailer_drip(status: int, location: str | None):
        """A chunked answer whose terminating chunk is followed by endless trailer lines: every
        line arrives inside the socket timeout, and `http.client` never returns to its caller."""

        def answer(handler: BaseHTTPRequestHandler) -> None:
            handler.send_response(status)
            if location is not None:
                handler.send_header("location", location)
            handler.send_header("transfer-encoding", "chunked")
            handler.end_headers()
            try:
                handler.wfile.write(b"0\r\n")
                for _ in range(200):
                    handler.wfile.write(b"x-t: 1\r\n")
                    handler.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass

        return answer

    def test_a_chunked_redirect_dripping_trailers_is_cut_at_the_deadline(self, origins):
        elsewhere = origins(ok)
        home = origins(self.chunked_trailer_drip(302, f"{elsewhere.url}/x"))
        started = time.monotonic()
        with pytest.raises(ProviderError, match=r"request failed \(timeout\)"):
            request_json("open-meteo", f"{home.url}/api", timeout_ms=400)
        assert time.monotonic() - started < 3
        assert elsewhere.seen == []

    def test_a_chunked_final_answer_dripping_trailers_is_cut_at_the_deadline(self, origins):
        home = origins(self.chunked_trailer_drip(200, None))
        started = time.monotonic()
        with pytest.raises(ProviderError, match=r"request failed \(timeout\)"):
            request_json("open-meteo", f"{home.url}/api", timeout_ms=400)
        assert time.monotonic() - started < 3

    def test_the_deadline_spans_the_whole_redirect_chain(self, origins):
        holder: dict[str, Origin] = {}

        def answer(handler: BaseHTTPRequestHandler) -> None:
            hop = int(handler.path.strip("/") or 0)
            if hop < 6:
                time.sleep(0.15)
                self.redirect_with_body(f"{holder['o'].url}/{hop + 1}", b"")(handler)
            else:
                ok(handler)

        holder["o"] = origins(answer)
        started = time.monotonic()
        with pytest.raises(ProviderError, match=r"request failed \((timeout|timed out)\)"):
            request_json("open-meteo", f"{holder['o'].url}/0", timeout_ms=400)
        assert time.monotonic() - started < 3


class TestTokenStaysOutOfMessages:
    def test_a_token_with_a_trailing_newline_is_stripped_by_the_config(self):
        connection = config.ha_connection(
            {"WEATHER_HA_URL": "http://h", "WEATHER_HA_TOKEN": f"{SECRET}\r\n"}, FileConfig()
        )
        assert connection is not None
        assert connection.token == SECRET

    def test_a_blank_token_is_no_connection(self):
        env = {"WEATHER_HA_URL": "http://h", "WEATHER_HA_TOKEN": " \r\n"}
        assert config.ha_connection(env, FileConfig()) is None

    @pytest.mark.parametrize("tail", ["\n", "\r", "a\r\nX-Evil: 1", "\u0442"])
    def test_an_unsendable_header_is_refused_before_any_exception_can_quote_it(self, tail):
        def never(_url: str, _init: HttpInit) -> pjson.HttpResponse:
            raise AssertionError("the request must not be attempted")

        with pytest.raises(ProviderError) as caught:
            request_json(
                "home-assistant",
                "http://ha/api",
                timeout_ms=5000,
                headers={"authorization": f"Bearer {SECRET}{tail}"},
                fetch_impl=never,
            )
        assert "invalid header value" in str(caught.value)
        # No exception was ever created from the value: nothing in the chain can quote it.
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert SECRET not in "".join(traceback.format_exception(caught.value))

    def test_default_fetch_refuses_it_too(self):
        bad = HttpInit(method="GET", headers={"authorization": f"Bearer {SECRET}\n"}, timeout_s=5)
        with pytest.raises(OSError, match="invalid header value") as caught:
            pjson.default_fetch("http://127.0.0.1:9/api", bad)
        assert SECRET not in str(caught.value)

    def test_a_value_error_from_a_host_fetcher_is_not_swallowed(self):
        def buggy(_url: str, _init: HttpInit) -> pjson.HttpResponse:
            raise ValueError("host bug")

        with pytest.raises(ProviderError, match="host bug"):
            request_json("open-meteo", "http://x/api", timeout_ms=100, fetch_impl=buggy)

    def test_the_request_description_does_not_print_its_headers(self):
        assert SECRET not in repr(authorised())


class TestHugeNumbers:
    def test_an_integer_beyond_the_float_range_reads_as_missing(self):
        assert as_number(10**400) is None
        assert as_number(10**15) == 10**15

    def test_a_config_file_with_a_huge_integer_is_survivable(self, tmp_path):
        directory = tmp_path / "weather-cli"
        directory.mkdir()
        (directory / "config.json").write_text(f'{{"lat": {10**400}, "lon": 6}}', encoding="utf-8")
        assert read_file_config({"XDG_CONFIG_HOME": str(tmp_path)}) == FileConfig(lon=6)

    def test_a_cache_file_with_a_huge_integer_is_a_miss_not_a_crash(self):
        text = json.dumps({"schema": 1, "bundle": {"fetchedAt": "x"}}).replace('"x"', "9" * 400)
        assert decode_bundle_text(text) is None


class StalledForecast:
    """State answers (after `state_delay` s); the two forecast calls hang until released."""

    def __init__(self, state_delay: float = 0) -> None:
        self.release = threading.Event()
        self.state_delay = state_delay

    def __call__(self, url: str, init: HttpInit) -> pjson.HttpResponse:
        if "get_forecasts" in url:
            self.release.wait(10)
            return reply(ha_forecast("hourly"))
        time.sleep(self.state_delay)
        return reply(ha_state())


# Generous on purpose: the assertions are ratios of the budget, not tight wall-clock races.
TIMEOUT_MS = 2000
BELGRADE = Place(44.8, 20.48, "Europe/Belgrade")


class TestDeadlines:
    HA = HaConnection(url="http://ha", token="t", entity_id="weather.forecast_home")

    def test_a_stalled_forecast_does_not_cost_the_state_that_already_arrived(self):
        stalled = StalledForecast()
        started = time.monotonic()
        try:
            bundle = fetch_home_assistant(
                self.HA, BELGRADE, timeout_ms=TIMEOUT_MS, fetch_impl=stalled
            )
        finally:
            stalled.release.set()
        assert time.monotonic() - started < TIMEOUT_MS / 1000
        assert bundle.sources == ["home-assistant"]
        assert bundle.hourly == [] and bundle.daily == []
        assert bundle.forecast_missing

    def test_a_slow_state_is_not_cut_at_the_forecast_share_of_the_budget(self):
        slow = StalledForecast(state_delay=TIMEOUT_MS / 1000 * 0.87)
        try:
            bundle = fetch_home_assistant(self.HA, BELGRADE, timeout_ms=TIMEOUT_MS, fetch_impl=slow)
        finally:
            slow.release.set()
        assert bundle.sources == ["home-assistant"]

    def test_through_get_weather_a_stalled_forecast_keeps_the_bundle_and_warns(self):
        stalled = StalledForecast()
        try:
            result = get_weather(
                place=BELGRADE,
                ha=self.HA,
                open_meteo=False,
                timeout_ms=TIMEOUT_MS,
                now=NOW,
                fetch_impl=stalled,
            )
        finally:
            stalled.release.set()
        assert result.origin == "live"
        assert result.errors == ["home-assistant: forecast unavailable"]
        assert result.bundle.current_source == "home-assistant"

    def test_a_bundle_without_any_forecast_is_not_cached_as_fresh(self):
        store = MemoryStore()
        stalled = StalledForecast()
        try:
            get_weather(
                place=BELGRADE,
                ha=self.HA,
                open_meteo=False,
                store=store,
                timeout_ms=TIMEOUT_MS,
                now=NOW,
                fetch_impl=stalled,
            )
        finally:
            stalled.release.set()
        assert store.entries == {}

    def test_a_forecast_the_server_refuses_is_reported_too(self):
        def no_forecasts(url: str, _init: HttpInit) -> pjson.HttpResponse:
            return reply({}, 400) if "get_forecasts" in url else reply(ha_state())

        result = get_weather(
            place=BELGRADE, ha=self.HA, open_meteo=False, now=NOW, fetch_impl=no_forecasts
        )
        assert result.errors == ["home-assistant: forecast unavailable"]

    def test_the_umbrella_answer_does_not_claim_dry_or_endless_without_a_forecast(self):
        dry = fetch_home_assistant(
            self.HA, BELGRADE, timeout_ms=TIMEOUT_MS, fetch_impl=_refusing_forecasts(ha_state())
        )
        assert say(bundle=dry, now=NOW, mode="umbrella").text == (
            "Сейчас без дождя, но прогноза на ближайшие часы нет."
        )
        rainy = ha_state()
        assert isinstance(rainy, dict)
        rainy["state"] = "rainy"
        wet = fetch_home_assistant(
            self.HA, BELGRADE, timeout_ms=TIMEOUT_MS, fetch_impl=_refusing_forecasts(rainy)
        )
        text = say(bundle=wet, now=NOW, mode="umbrella").text
        assert "когда закончится, неизвестно" in text
        assert "не закончится" not in text

    def test_the_place_lookup_is_capped_by_the_timeout(self, monkeypatch):
        release = threading.Event()

        def hang(_url: str, _init: HttpInit) -> pjson.HttpResponse:
            release.wait(10)
            raise OSError("late")

        monkeypatch.setattr(pjson, "default_fetch", hang)
        started = time.monotonic()
        try:
            with pytest.raises(TimeoutError):
                resolve_place(PlaceFlags(), {}, FileConfig(), self.HA, 200)
        finally:
            release.set()
        assert time.monotonic() - started < 2


def _refusing_forecasts(state: pjson.JsonValue):
    def fetch(url: str, _init: HttpInit) -> pjson.HttpResponse:
        return reply({}, 400) if "get_forecasts" in url else reply(state)

    return fetch


class TestRelativeDirectories:
    @pytest.mark.parametrize("value", ["", "relative/dir", "./here"])
    def test_an_empty_or_relative_dir_variable_counts_as_unset(self, value, tmp_path):
        env = {
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": value,
            "XDG_CACHE_HOME": value,
            "WEATHER_CACHE_DIR": value,
        }
        assert config.config_dir(env) == tmp_path / ".config" / "weather-cli"
        assert config.cache_dir(env) == tmp_path / ".cache" / "weather-cli"

    def test_an_empty_xdg_does_not_read_a_config_from_the_working_directory(
        self, tmp_path, monkeypatch
    ):
        planted = tmp_path / "weather-cli"
        planted.mkdir()
        (planted / "config.json").write_text('{"haUrl": "http://evil"}', encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        assert read_file_config({"XDG_CONFIG_HOME": ""}) == FileConfig()

    def test_a_relative_home_is_no_home(self):
        assert config.cache_dir({"HOME": "relative"}) is None

    def test_absolute_values_are_honoured(self, tmp_path):
        assert config.cache_dir({"WEATHER_CACHE_DIR": str(tmp_path)}) == tmp_path
        assert config.cache_dir({"XDG_CACHE_HOME": str(tmp_path)}) == tmp_path / "weather-cli"


class TestBlankSettings:
    def test_a_blank_primary_token_does_not_shadow_the_real_one(self):
        env = {"WEATHER_HA_URL": "http://h", "WEATHER_HA_TOKEN": "  ", "HA_TOKEN": "real"}
        connection = config.ha_connection(env, FileConfig())
        assert connection is not None and connection.token == "real"

    def test_a_blank_url_falls_through_to_the_next_source(self):
        env = {"WEATHER_HA_URL": "", "HA_URL": "http://other/", "HA_TOKEN": "t"}
        connection = config.ha_connection(env, FileConfig())
        assert connection is not None and connection.url == "http://other"

    def test_surrounding_whitespace_is_stripped_from_every_filled_value(self):
        env = {
            "WEATHER_HA_URL": " http://h/ ",
            "WEATHER_HA_TOKEN": " tok\r\n",
            "WEATHER_HA_ENTITY": " weather.x ",
        }
        connection = config.ha_connection(env, FileConfig())
        assert connection == HaConnection(url="http://h", token="tok", entity_id="weather.x")
        flags = PlaceFlags(lat="1", lon="2", tz=" Asia/Tokyo ")
        assert resolve_place(flags, {}, FileConfig(), None, 100).tz == "Asia/Tokyo"

    def test_a_blank_zone_falls_back_to_the_machine_zone(self):
        env = {"TZ": "Asia/Tokyo"}
        place = resolve_place(PlaceFlags(lat="1", lon="2", tz=""), env, FileConfig(), None, 100)
        assert place.tz == "Asia/Tokyo"

    @pytest.mark.parametrize("zone", ["Mars/Olympus", "../etc/passwd", "/etc/passwd"])
    def test_an_unknown_zone_is_rejected_before_any_request(self, zone):
        with pytest.raises(cli_main.ZoneInfoNotFoundError):
            resolve_place(PlaceFlags(lat="1", lon="2", tz=zone), {}, FileConfig(), None, 100)

    def test_the_command_reports_it_like_the_bundle_path_and_makes_no_request(
        self, scrubbed_env, tmp_path, capsys
    ):
        def forbidden(_url: str, _init: HttpInit) -> pjson.HttpResponse:
            raise AssertionError("a request was made")

        scrubbed_env.setattr(pjson, "default_fetch", forbidden)
        scrubbed_env.setenv("HOME", str(tmp_path))
        code = cli_main.main(["say", "--lat", "44.8", "--lon", "20.48", "--tz", "Mars/Olympus"])
        assert code == 1
        assert capsys.readouterr().err == "weather: invalid time zone: Mars/Olympus\n"

    def test_a_zone_from_home_assistant_is_checked_too(self, monkeypatch):
        monkeypatch.setattr(config, "fetch_ha_place", lambda *_a, **_k: Place(1, 2, "Mars/Olympus"))
        ha = HaConnection(url="http://h", token="t")
        with pytest.raises(cli_main.ZoneInfoNotFoundError):
            resolve_place(PlaceFlags(), {}, FileConfig(), ha, 100)


class TestContextRepr:
    def test_the_token_never_appears_in_a_context_repr(self, scrubbed_env, tmp_path):
        env = {"WEATHER_HA_TOKEN": SECRET, "HOME": str(tmp_path)}
        ctx = cli_main._context("say", ["--lat", "1", "--lon", "2"], env)
        assert SECRET not in repr(ctx)


class TestLocalZone:
    @pytest.mark.parametrize("tz", [":/etc/localtime", "UTC0", "EST5EDT4,M3.2.0,M11.1.0", "../x"])
    def test_a_tz_value_that_is_not_a_zone_name_never_becomes_the_zone(self, tz):
        name = config.local_zone_name({"TZ": tz})
        assert name != tz.lstrip(":")
        ZoneInfo(name)  # loads: the answer is always a usable zone

    def test_a_real_tz_name_wins(self):
        assert config.local_zone_name({"TZ": ":Asia/Tokyo"}) == "Asia/Tokyo"


class TestBlankCoordinates:
    @pytest.mark.parametrize("blank", ["", "  ", "\t"])
    def test_a_blank_flag_or_env_value_is_unset_not_zero(self, blank):
        file = FileConfig(lat=1, lon=2, tz="UTC")
        flags = PlaceFlags(lat=blank, lon=blank)
        assert resolve_place(flags, {}, file, None, 100) == Place(1, 2, "UTC")
        env = {"WEATHER_LAT": blank, "WEATHER_LON": blank}
        assert resolve_place(PlaceFlags(), env, file, None, 100) == Place(1, 2, "UTC")

    def test_blank_everywhere_is_no_location(self):
        with pytest.raises(RuntimeError, match="no location"):
            resolve_place(PlaceFlags(lat=""), {"WEATHER_LON": " "}, FileConfig(), None, 100)

    def test_a_real_zero_still_counts(self):
        assert resolve_place(PlaceFlags(lat="0", lon="0", tz="UTC"), {}, FileConfig(), None, 1) == (
            Place(0, 0, "UTC")
        )


class TestDecimalNumbers:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("1.", 1), (".5", 0.5), ("+.5e1", 5), ("-1.5E-1", -0.15), ("1.e3", 1000), (" 7 ", 7)],
    )
    def test_valid(self, text, expected):
        assert parse_js_number(text) == expected

    @pytest.mark.parametrize("text", [".", "1.5.2", "e5", "1e", "--1", "1 2", "+-1", "1,5"])
    def test_malformed_is_nan(self, text):
        assert parse_js_number(text) != parse_js_number(text)


class TestImpossibleInstants:
    def test_a_cached_moment_beyond_year_9999_is_a_miss(self):
        data = bundle_to_json(bundle(now=NOW))
        assert decode_bundle_text(json.dumps({"schema": 1, "bundle": data})) is not None
        assert isinstance(data["current"], dict)
        data["current"]["time"] = 1e300
        assert decode_bundle_text(json.dumps({"schema": 1, "bundle": data})) is None

    def test_an_open_meteo_current_time_beyond_year_9999_is_an_error(self):
        payload = open_meteo_payload()
        assert isinstance(payload, dict) and isinstance(payload["current"], dict)
        payload["current"]["time"] = 1e300
        with pytest.raises(ProviderError, match="impossible time"):
            parse_open_meteo(payload, Place(1, 2, "UTC"), NOW)

    def test_an_open_meteo_hour_beyond_year_9999_is_skipped(self):
        payload = open_meteo_payload()
        assert isinstance(payload, dict) and isinstance(payload["hourly"], dict)
        times = payload["hourly"]["time"]
        assert isinstance(times, list)
        times[1] = 1e300
        parsed = parse_open_meteo(payload, Place(1, 2, "UTC"), NOW)
        assert all(is_plausible_instant(m.time) for m in parsed.hourly)
        assert len(parsed.hourly) == 4


class TestInstantGuardEverywhere:
    def test_the_bounds_leave_room_for_any_local_zone(self):
        from weather_cli.local_time import local_parts
        from weather_cli.units import MAX_EPOCH_MS, MIN_EPOCH_MS

        assert is_plausible_instant(MAX_EPOCH_MS) and not is_plausible_instant(MAX_EPOCH_MS + 1)
        assert is_plausible_instant(MIN_EPOCH_MS) and not is_plausible_instant(MIN_EPOCH_MS - 1)
        assert local_parts(MAX_EPOCH_MS, "Pacific/Kiritimati").year == 9999
        assert local_parts(MIN_EPOCH_MS, "Pacific/Pago_Pago").year >= 1

    @pytest.mark.parametrize("text", ["9999-12-31T23:59:59Z", "0001-01-01T00:00:00Z"])
    def test_an_iso_string_at_the_edge_of_the_calendar_is_not_a_time(self, text):
        assert parse_iso_ms(text) is None

    @pytest.mark.parametrize("field", ["fetchedAt", "builtAt"])
    def test_a_cached_stamp_beyond_year_9999_is_a_miss(self, field):
        from builders import climate

        from weather_cli.cache import climate_to_json, decode_climate_text

        if field == "fetchedAt":
            data = bundle_to_json(bundle(now=NOW))
            data[field] = 1e300
            assert decode_bundle_text(json.dumps({"schema": 1, "bundle": data})) is None
        else:
            data = climate_to_json(climate(60, 10))
            data[field] = 1e300
            assert decode_climate_text(json.dumps({"schema": 1, "climate": data})) is None

    def test_an_open_meteo_day_beyond_year_9999_is_skipped(self):
        payload = open_meteo_payload()
        assert isinstance(payload, dict) and isinstance(payload["daily"], dict)
        times = payload["daily"]["time"]
        assert isinstance(times, list)
        times[0] = 1e300
        parsed = parse_open_meteo(payload, Place(1, 2, "UTC"), NOW)
        assert len(parsed.daily) == 1

    def test_archive_stamps_beyond_year_9999_are_skipped(self):
        from weather_cli.climate import parse_archive

        archive = {
            "hourly": {
                "time": [1e300, 1_700_000_000],
                "temperature_2m": [1, 2],
                "relative_humidity_2m": [50, 60],
            }
        }
        parse_archive(archive, Place(1, 2, "UTC"), (2015, 2024), NOW)

    def test_a_ha_day_the_calendar_cannot_show_is_skipped_not_fatal(self):
        state = {"entity_id": "weather.x", "state": "sunny", "attributes": {"temperature": 5}}
        entries = [
            {"datetime": "9999-12-31T23:59:59-23:59", "temperature": 1},
            {"datetime": "2026-11-09T11:00:00+00:00", "temperature": 2},
        ]
        forecast = {"service_response": {"weather.x": {"forecast": entries}}}
        parsed = parse_home_assistant(state, None, forecast, Place(1, 2, "UTC"), NOW)
        assert [d.max for d in parsed.daily] == [2]

    def test_a_far_future_cache_is_not_served_as_stale_either(self):
        from weather_cli.cache import bundle_key, write_bundle
        from weather_cli.providers.json import StaticResponse

        def down(_url: str, _init: HttpInit) -> StaticResponse:
            raise OSError("offline")

        store = MemoryStore()
        place = Place(44.8, 20.48, "Europe/Belgrade")
        write_bundle(store, bundle_key(place), bundle(now=NOW + 24 * 3_600_000))
        with pytest.raises(RuntimeError, match="no weather data"):
            get_weather(place=place, store=store, now=NOW, fetch_impl=down)


class TestClimateZone:
    def test_norms_built_for_another_zone_are_not_loaded(self):
        from builders import climate

        from weather_cli.cache import climate_key, write_climate
        from weather_cli.climate import load_climate

        store = MemoryStore()
        built = climate(60, 10)
        write_climate(store, climate_key(built.place), built)
        assert load_climate(store, built.place) is not None
        assert load_climate(store, Place(built.place.lat, built.place.lon, "UTC")) is None


class TestHaBudgetsAndCaps:
    HA = HaConnection(url="http://ha", token="t", entity_id="weather.forecast_home")

    def test_a_failing_forecast_wrapper_keeps_the_state(self, monkeypatch):
        import weather_cli.providers.home_assistant as ha_module

        real = ha_module.gather
        calls: list[int] = []

        def flaky(tasks, timeout):
            calls.append(1)
            if len(calls) > 1:  # the inner forecast gather
                raise RuntimeError("wrapper failed")
            return real(tasks, timeout)

        monkeypatch.setattr(ha_module, "gather", flaky)
        bundle_ = fetch_home_assistant(
            self.HA, BELGRADE, timeout_ms=2000, fetch_impl=_refusing_forecasts(ha_state())
        )
        assert bundle_.sources == ["home-assistant"]
        assert bundle_.forecast_missing

    def test_entity_discovery_may_read_a_larger_body_than_other_calls(self):
        seen: dict[str, int | None] = {}

        def record(url: str, init: HttpInit) -> pjson.HttpResponse:
            seen[url.rsplit("/", 1)[-1]] = init.max_bytes
            if url.endswith("/api/states"):
                return reply([{"entity_id": "weather.forecast_home"}])
            return reply(ha_state()) if "get_forecasts" not in url else reply({}, 400)

        ha = HaConnection(url="http://ha", token="t")
        fetch_home_assistant(ha, BELGRADE, timeout_ms=2000, fetch_impl=record)
        from weather_cli.providers.home_assistant import MAX_STATES_BYTES

        assert seen["states"] == MAX_STATES_BYTES
        assert seen["weather.forecast_home"] is None


class TestSmallGuards:
    def test_cache_directories_are_owner_only(self, tmp_path):
        previous = os.umask(0)
        try:
            FileStore(tmp_path / "a" / "b").write("k.json", "v")
        finally:
            os.umask(previous)
        assert (tmp_path / "a" / "b").stat().st_mode & 0o777 == 0o700

    def test_a_directory_key_is_an_invalid_zone_not_a_path_error(self, monkeypatch):
        def directory(_key: str):
            raise IsADirectoryError("Europe")

        monkeypatch.setattr(config, "ZoneInfo", directory)
        with pytest.raises(cli_main.ZoneInfoNotFoundError):
            resolve_place(PlaceFlags(lat="1", lon="2", tz="Europe"), {}, FileConfig(), None, 100)

    def test_an_absurd_timeout_is_a_usage_error(self, capsys):
        bundle_ = ["--bundle", str(ROOT / "examples" / "calm-noon.json")]
        at = ["--at", "2026-10-12T12:15:00+02:00"]
        assert cli_main.main(["say", *bundle_, *at, "--timeout", "86400001"]) == 2
        assert "--timeout" in capsys.readouterr().err
        assert cli_main.main(["say", *bundle_, *at, "--timeout", "86400000"]) == 0


class TestCliArguments:
    def test_an_argument_that_is_not_utf8_is_reported_escaped_without_a_traceback(self):
        code, _, err = weather(os.fsdecode(b"\xff"))
        assert code == 2
        assert "Traceback" not in err
        assert "\\udcff" in err or "\\xff" in err


class TestRadixNumbers:
    @pytest.mark.parametrize(
        "text", ["0x-5", "0x1_0", "0x0x1f", "0x٢", "0xg", "0b2", "0o8", "0x", "0b", "0o", "0x 1"]
    )
    def test_malformed_radix_literals_are_nan(self, text):
        assert parse_js_number(text) != parse_js_number(text)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("0x1f", 31), ("0XFF", 255), ("0o17", 15), ("0b101", 5), (" 0x10 ", 16)],
    )
    def test_valid_radix_literals(self, text, expected):
        assert parse_js_number(text) == expected

    def test_a_huge_hex_literal_is_infinity_not_a_crash(self):
        assert parse_js_number("0x" + "f" * 400) == float("inf")


class TestCommandValidation:
    BUNDLE = ["--bundle", str(ROOT / "examples" / "calm-noon.json")]

    def test_a_mode_conflict_exits_2_before_any_fetch(self, scrubbed_env, tmp_path, capsys):
        def forbidden(_url: str, _init: HttpInit) -> pjson.HttpResponse:
            raise AssertionError("a request was made")

        scrubbed_env.setattr(pjson, "default_fetch", forbidden)
        scrubbed_env.setenv("HOME", str(tmp_path))
        argv = ["say", "--lat", "44.8", "--lon", "20.48", "--tz", "UTC", "--week", "--dress"]
        assert cli_main.main(argv) == 2
        assert "pick one mode" in capsys.readouterr().err

    def test_climate_update_without_a_cache_directory_fails_before_downloading(
        self, scrubbed_env, capsys
    ):
        def forbidden(_url: str, _init: HttpInit) -> pjson.HttpResponse:
            raise AssertionError("a request was made")

        scrubbed_env.setattr(pjson, "default_fetch", forbidden)
        scrubbed_env.delenv("HOME", raising=False)
        scrubbed_env.delenv("USERPROFILE", raising=False)
        code = cli_main.main(["climate", "update", "--lat", "44.8", "--lon", "20.48"])
        assert code == 1
        assert "no cache directory" in capsys.readouterr().err

    def test_climate_update_with_no_cache_flag_fails_too(self, scrubbed_env, tmp_path, capsys):
        scrubbed_env.setenv("HOME", str(tmp_path))
        argv = ["climate", "update", "--lat", "44.8", "--lon", "20.48", "--no-cache"]
        assert cli_main.main(argv) == 1
        assert "no cache directory" in capsys.readouterr().err


class TestHomeDirectory:
    def test_without_a_home_the_cache_and_file_config_are_simply_off(self):
        assert config.cache_dir({}) is None
        assert config.config_dir({}) is None
        assert read_file_config({}) == FileConfig()

    def test_the_injected_home_is_used_not_the_process_home(self, tmp_path):
        assert config.cache_dir({"HOME": str(tmp_path)}) == tmp_path / ".cache" / "weather-cli"

    def test_a_command_runs_with_no_home_at_all(self):
        out: list[str] = []
        err: list[str] = []
        argv = [
            "say",
            "--bundle",
            str(ROOT / "examples" / "calm-noon.json"),
            "--at",
            "2026-10-12T12:15:00+02:00",
        ]
        assert cli_main.run(argv, {}, out, err) == 0
        assert "".join(out) == "На улице 18 градусов, ясно.\n"


class TestOffsets:
    @pytest.mark.parametrize("zone", ["+24:00", "-24:00", "+25:00", "+23:99", "+01:60", "+99:99"])
    def test_an_out_of_range_offset_is_not_a_time(self, zone):
        assert parse_iso_ms(f"2026-10-12T10:00:00{zone}") is None

    @pytest.mark.parametrize("zone", ["+23:59", "-23:59", "+00:00", "Z", "+0530"])
    def test_a_valid_offset_parses(self, zone):
        assert parse_iso_ms(f"2026-10-12T10:00:00{zone}") is not None

    def test_non_ascii_digits_are_not_digits(self):
        assert parse_iso_ms("٢٠٢٦-١٠-١٢T10:00:00Z") is None
        assert parse_iso_ms("2026-10-12T10:00:00+٠١:٠٠") is None
        assert parse_js_number("١٢") != parse_js_number("١٢")  # NaN

    def test_at_with_a_bad_offset_is_a_usage_error(self, capsys):
        code = cli_main.main(
            [
                "say",
                "--bundle",
                str(ROOT / "examples" / "calm-noon.json"),
                "--at",
                "2026-10-12T10:00:00+24:00",
            ]
        )
        assert code == 2
        assert "--at" in capsys.readouterr().err

    def test_one_odd_forecast_datetime_skips_that_entry_only(self):
        state = {
            "entity_id": "weather.x",
            "state": "sunny",
            "attributes": {"temperature": 5},
        }
        entries = [
            {"datetime": "2026-11-09T10:00:00+24:00", "temperature": 1},
            {"datetime": "2026-11-09T11:00:00+00:00", "temperature": 2},
        ]
        forecast = {"service_response": {"weather.x": {"forecast": entries}}}
        parsed = parse_home_assistant(state, forecast, forecast, Place(1, 2, "UTC"), NOW)
        assert [m.temp for m in parsed.hourly] == [2]
        assert len(parsed.daily) == 1


class TestFileStoreAtomicity:
    def test_cache_files_are_owner_only_whatever_the_umask(self, tmp_path):
        previous = os.umask(0)
        try:
            FileStore(tmp_path).write("k.json", "v")
        finally:
            os.umask(previous)
        assert (tmp_path / "k.json").stat().st_mode & 0o777 == 0o600

    def test_a_failed_write_leaves_the_old_file_and_no_temp_file(self, tmp_path, monkeypatch):
        store = FileStore(tmp_path)
        store.write("k.json", "old")

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError, match="disk full"):
            store.write("k.json", "new")
        assert sorted(p.name for p in tmp_path.iterdir()) == ["k.json"]
        assert store.read("k.json") == "old"

    def test_concurrent_writers_never_share_a_temp_file(self, tmp_path):
        store = FileStore(tmp_path)
        errors: list[Exception] = []

        def write(text: str) -> None:
            try:
                for _ in range(50):
                    store.write("k.json", text * 1000)
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=write, args=(c,)) for c in "abcd"]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert sorted(p.name for p in tmp_path.iterdir()) == ["k.json"]
        text = store.read("k.json")
        assert text is not None
        assert len(set(text)) == 1 and len(text) == 1000


class TestZoneMessages:
    def test_an_unknown_zone_is_named(self):
        error = cli_main.ZoneInfoNotFoundError("No time zone found with key Mars/Olympus")
        assert cli_main._zone_message(error) == "invalid time zone: Mars/Olympus"

    def test_a_machine_without_a_tz_database_says_so(self, monkeypatch):
        def missing(_key: str):
            raise cli_main.ZoneInfoNotFoundError("No time zone found with key UTC")

        monkeypatch.setattr(cli_main, "ZoneInfo", missing)
        message = cli_main._zone_message(cli_main.ZoneInfoNotFoundError("Europe/Belgrade"))
        assert message.startswith("tz database not found")


class TestExamples:
    def test_make_examples_reproduces_the_committed_files(self):
        spec = importlib.util.spec_from_file_location(
            "make_examples", ROOT / "scripts" / "make_examples.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name in module.EXAMPLES:
            committed = (ROOT / "examples" / f"{name}.json").read_text(encoding="utf-8")
            assert module.render(name) == committed, name


def test_the_cache_is_not_stamped_by_a_made_up_clock(tmp_path, scrubbed_env, capsys):
    """`--at` far in the future with live data must not leave a "fresh" bundle behind."""
    scrubbed_env.setattr(pjson, "default_fetch", lambda _url, _init: om_ok())
    scrubbed_env.setenv("HOME", str(tmp_path))
    scrubbed_env.setenv("WEATHER_CACHE_DIR", str(tmp_path / "cache"))
    place = ["--lat", "44.8", "--lon", "20.48", "--tz", "Europe/Belgrade"]
    assert cli_main.main(["say", *place, "--at", "2099-01-01T12:00:00+01:00"]) == 0
    assert not (tmp_path / "cache").exists() or list((tmp_path / "cache").iterdir()) == []
    capsys.readouterr()
    # The canned forecast is from `NOW`: on the real clock its hours have long passed, and a
    # bundle with no usable hour is rightly never cached.
    scrubbed_env.setattr(time, "time", lambda: NOW / 1000)
    assert cli_main.main(["say", *place]) == 0
    assert list(Path(tmp_path / "cache").glob("bundle-*.json")) != []


class TestHangUp:
    def test_a_tls_socket_is_cut_at_the_tcp_layer_not_through_its_own_shutdown(self):
        """`ssl.SSLSocket.shutdown` removes the TLS layer before it shuts TCP down, so a thread
        writing in between would send its plaintext. The watchdog must use the plain call."""
        left, right = socket.socketpair()

        class LikeSslSocket(socket.socket):
            def shutdown(self, how: int) -> None:
                raise AssertionError("the overridable shutdown must not be used")

        wrapped = LikeSslSocket(fileno=left.detach())
        try:
            pjson._hang_up(wrapped)
            right.settimeout(2)
            assert right.recv(1) == b""
        finally:
            wrapped.close()
            right.close()
