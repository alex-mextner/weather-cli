"""CLI configuration layers, the file store, and flag error codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from runner import ROOT, weather

import weather_cli.cli.config as config
from weather_cli.cache_fs import FileStore
from weather_cli.cli.args import UsageError
from weather_cli.cli.config import FileConfig, PlaceFlags, read_file_config, resolve_place
from weather_cli.providers.home_assistant import HaConnection
from weather_cli.types import Place

HA = HaConnection(url="http://ha", token="t")


def write_config(home: Path, text: str) -> dict[str, str]:
    directory = home / ".config" / "weather-cli"
    directory.mkdir(parents=True)
    (directory / "config.json").write_text(text, encoding="utf-8")
    return {"XDG_CONFIG_HOME": str(home / ".config")}


class TestPlacePrecedence:
    FILE = FileConfig(lat=1, lon=2, tz="Asia/Tokyo")

    def test_flags_beat_env(self):
        env = {"WEATHER_LAT": "10", "WEATHER_LON": "20", "WEATHER_TZ": "UTC"}
        flags = PlaceFlags(lat="3", lon="4", tz="Europe/Rome")
        assert resolve_place(flags, env, self.FILE, None, 100) == Place(3, 4, "Europe/Rome")

    def test_env_beats_the_config_file(self):
        env = {"WEATHER_LAT": "10", "WEATHER_LON": "20", "WEATHER_TZ": "UTC"}
        assert resolve_place(PlaceFlags(), env, self.FILE, None, 100) == Place(10, 20, "UTC")

    def test_config_file_beats_home_assistant(self, monkeypatch):
        monkeypatch.setattr(config, "fetch_ha_place", lambda *_a, **_k: Place(9, 9, "UTC"))
        assert resolve_place(PlaceFlags(), {}, self.FILE, HA, 100) == Place(1, 2, "Asia/Tokyo")

    def test_home_assistant_is_the_last_resort(self, monkeypatch):
        monkeypatch.setattr(config, "fetch_ha_place", lambda *_a, **_k: Place(9, 9, "UTC"))
        assert resolve_place(PlaceFlags(), {}, FileConfig(), HA, 100) == Place(9, 9, "UTC")

    def test_nothing_configured_is_an_error(self):
        with pytest.raises(RuntimeError, match="no location"):
            resolve_place(PlaceFlags(), {}, FileConfig(), None, 100)

    @pytest.mark.parametrize("bad", ["abc", "44,8", "Infinity", "NaN"])
    def test_a_malformed_coordinate_is_a_usage_error_not_a_fall_through(self, bad):
        with pytest.raises(UsageError, match="latitude must be a number"):
            resolve_place(PlaceFlags(lat=bad, lon="1"), {}, FileConfig(), HA, 100)
        with pytest.raises(UsageError, match="longitude must be a number"):
            resolve_place(
                PlaceFlags(), {"WEATHER_LAT": "1", "WEATHER_LON": bad}, FileConfig(), HA, 100
            )

    def test_xdg_config_home_is_respected(self, tmp_path):
        env = write_config(tmp_path, '{"lat": 5, "lon": 6, "tz": "UTC"}')
        assert read_file_config(env) == FileConfig(lat=5, lon=6, tz="UTC")

    @pytest.mark.parametrize("text", ["{not json", "[1, 2]", '"text"', ""])
    def test_malformed_config_file_is_ignored(self, tmp_path, text):
        assert read_file_config(write_config(tmp_path, text)) == FileConfig()

    def test_wrongly_typed_config_fields_are_dropped(self, tmp_path):
        env = write_config(tmp_path, '{"lat": "5", "lon": 6, "tz": 7}')
        assert read_file_config(env) == FileConfig(lon=6)

    def test_ha_needs_both_url_and_token_and_the_token_is_env_only(self):
        assert config.ha_connection({"WEATHER_HA_URL": "http://h/"}, FileConfig()) is None
        file = FileConfig(ha_url="http://file/", ha_entity="weather.x")
        assert config.ha_connection({"HA_TOKEN": "t"}, file) == HaConnection(
            url="http://file", token="t", entity_id="weather.x"
        )


class TestHaToken:
    def test_a_token_in_the_config_file_is_never_used(self, tmp_path):
        env = write_config(
            tmp_path, '{"haUrl": "http://file", "haToken": "from-file", "haEntity": "weather.x"}'
        )
        file = read_file_config(env)
        assert config.ha_connection({}, file) is None
        assert config.ha_connection({"HA_TOKEN": "from-env"}, file) == HaConnection(
            url="http://file", token="from-env", entity_id="weather.x"
        )


class TestFileStore:
    def test_round_trip_creates_the_directory(self, tmp_path):
        store = FileStore(tmp_path / "a" / "b")
        assert store.read("k.json") is None
        store.write("k.json", "значение")
        assert store.read("k.json") == "значение"

    def test_a_missing_or_unreadable_key_is_a_miss(self, tmp_path):
        (tmp_path / "dir.json").mkdir()
        assert FileStore(tmp_path).read("dir.json") is None
        assert FileStore(tmp_path / "nope").read("k.json") is None

    def test_a_write_into_an_unwritable_place_raises_for_the_caller_to_swallow(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        with pytest.raises(OSError):
            FileStore(blocker / "sub").write("k.json", "v")


BUNDLE = ["--bundle", "examples/calm-noon.json", "--at", "2026-10-12T12:15:00+02:00"]


class TestFlagErrors:
    @pytest.mark.parametrize("command", ["say", "explain", "climate"])
    def test_unknown_flag_and_missing_value_exit_1(self, command):
        assert weather(command, "--bogus", *BUNDLE)[0] == 1
        code, _, err = weather(command, "--lat")
        assert (code, "argument missing" in err) == (1, True)

    @pytest.mark.parametrize("command", ["say", "explain", "climate"])
    def test_bad_values_exit_2(self, command):
        assert weather(command, *BUNDLE, "--timeout", "0")[0] == 2
        assert weather(command, *BUNDLE, "--at", "yesterday")[0] == 2

    def test_other_usage_errors_exit_2(self):
        assert weather("bogus")[0] == 2
        assert weather("climate", "foo", *BUNDLE)[0] == 2
        assert weather("explain", "--week", "--dress", *BUNDLE)[0] == 2

    def test_a_boolean_flag_with_a_value_exits_1(self):
        assert weather("say", "--json=1", *BUNDLE)[0] == 1

    def test_invalid_time_zone_is_reported_plainly(self, tmp_path):
        bundle = json.loads((ROOT / "examples" / "calm-noon.json").read_text(encoding="utf-8"))
        bundle["place"]["tz"] = "Mars/Olympus"
        path = tmp_path / "b.json"
        path.write_text(json.dumps(bundle), encoding="utf-8")
        code, _, err = weather("say", "--bundle", str(path), "--at", "2026-10-12T12:15:00+02:00")
        assert (code, err) == (1, "weather: invalid time zone: Mars/Olympus")
