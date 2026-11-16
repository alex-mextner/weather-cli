"""CLI configuration: where the place, Home Assistant and the cache directory come from.

Accessed via: weather_cli/cli/main.py only (the library takes explicit options).

Assumptions: order is flags → environment → config file → Home Assistant's own /api/config.
The Home Assistant token is read from the environment only; it is never written to the config
file or the cache.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..providers.home_assistant import HaConnection, fetch_ha_place
from ..providers.json import as_number, as_string, call_within, is_record, parse_json
from ..types import Place
from ..units import parse_js_number
from .args import UsageError

# The one version constant; tests keep it in sync with pyproject.toml.
VERSION = "0.2.0"

Env = Mapping[str, str]

NO_LOCATION = (
    "no location: pass --lat/--lon, set WEATHER_LAT/WEATHER_LON, "
    "write ~/.config/weather-cli/config.json, "
    "or configure Home Assistant (WEATHER_HA_URL + WEATHER_HA_TOKEN)"
)


@dataclass(frozen=True, slots=True)
class FileConfig:
    lat: float | None = None
    lon: float | None = None
    tz: str | None = None
    ha_url: str | None = None
    ha_entity: str | None = None


@dataclass(frozen=True, slots=True)
class PlaceFlags:
    lat: str | None = None
    lon: str | None = None
    tz: str | None = None


def _home(env: Env) -> Path | None:
    """The injected environment's home directory; None when it has none (cache and file config
    are then simply off, never an error)."""
    return _absolute(env.get("HOME") or env.get("USERPROFILE"))


def _absolute(value: str | None) -> Path | None:
    """XDG rule: an empty or relative path counts as unset (it would resolve against the cwd,
    where an untrusted directory could plant a config or collect cache files)."""
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else None


def config_dir(env: Env) -> Path | None:
    base = _absolute(env.get("XDG_CONFIG_HOME"))
    if base is not None:
        return base / "weather-cli"
    home = _home(env)
    return None if home is None else home / ".config" / "weather-cli"


def cache_dir(env: Env) -> Path | None:
    explicit = _absolute(env.get("WEATHER_CACHE_DIR"))
    if explicit is not None:
        return explicit
    base = _absolute(env.get("XDG_CACHE_HOME"))
    if base is not None:
        return base / "weather-cli"
    home = _home(env)
    return None if home is None else home / ".cache" / "weather-cli"


def read_file_config(env: Env) -> FileConfig:
    directory = config_dir(env)
    if directory is None:
        return FileConfig()
    try:
        text = (directory / "config.json").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return FileConfig()
    json = parse_json(text)
    if not is_record(json):
        return FileConfig()
    return FileConfig(
        lat=as_number(json.get("lat")),
        lon=as_number(json.get("lon")),
        tz=as_string(json.get("tz")),
        ha_url=as_string(json.get("haUrl")),
        ha_entity=as_string(json.get("haEntity")),
    )


def ha_connection(env: Env, file: FileConfig) -> HaConnection | None:
    # A blank variable (template `.env` line, CRLF leftovers) must not shadow a real one.
    url = _filled(env.get("WEATHER_HA_URL"), env.get("HA_URL"), file.ha_url)
    token = _filled(env.get("WEATHER_HA_TOKEN"), env.get("HA_TOKEN"))
    if url is None or token is None:
        return None
    entity_id = _filled(env.get("WEATHER_HA_ENTITY"), file.ha_entity)
    return HaConnection(url=url.rstrip("/"), token=token, entity_id=entity_id)


def local_zone_name(env: Env) -> str:
    """The machine's IANA zone, as `Intl.DateTimeFormat().resolvedOptions().timeZone` reports."""
    named = env.get("TZ", "").lstrip(":")
    if named and _loads(named):  # TZ may be a POSIX rule ("UTC0") or a file path: not a zone name
        return named
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        return "UTC"
    marker = "zoneinfo/"
    found = target.split(marker, 1)[1] if marker in target else "UTC"
    return found if _loads(found) else "UTC"


def _loads(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ValueError, OSError, KeyError):  # KeyError: ZoneInfoNotFoundError
        return False
    return True


def _filled(*values: str | None) -> str | None:
    """The first value that says something: blank or whitespace-only counts as unset, so a
    template `WEATHER_LAT=` line does not silently mean latitude 0."""
    return next((v.strip() for v in values if v is not None and v.strip() != ""), None)


def _checked_zone(tz: str) -> str:
    """A zone name the machine knows, else the same error the `--bundle` path gives (before any
    network request, instead of an opaque provider failure)."""
    try:
        ZoneInfo(tz)
    except (ValueError, OSError) as error:  # not a key at all (empty, `..`) or a directory key
        raise ZoneInfoNotFoundError(f"No time zone found with key {tz}") from error
    return tz


def _coordinate(name: str, text: str | None, fallback: float | None) -> float | None:
    """A given value must be a finite number: `--lat 44,8` is a mistake to report, not a reason
    to fall through to another place (HA's home, say) without a word."""
    if text is None:
        return fallback
    value = parse_js_number(text)
    if not math.isfinite(value):
        raise UsageError(f"{name} must be a number, got {text!r}")
    return value


def resolve_place(
    flags: PlaceFlags,
    env: Env,
    file: FileConfig,
    ha: HaConnection | None,
    timeout_ms: float,
) -> Place:
    lat = _coordinate("latitude", _filled(flags.lat, env.get("WEATHER_LAT")), file.lat)
    lon = _coordinate("longitude", _filled(flags.lon, env.get("WEATHER_LON")), file.lon)
    if lat is not None and lon is not None:
        tz = _filled(flags.tz, env.get("WEATHER_TZ"), file.tz)
        return Place(lat=lat, lon=lon, tz=_checked_zone(tz or local_zone_name(env)))
    if ha is not None:
        place = call_within(lambda: fetch_ha_place(ha, timeout_ms=timeout_ms), timeout_ms / 1000)
        return replace(place, tz=_checked_zone(place.tz))
    raise RuntimeError(NO_LOCATION)
