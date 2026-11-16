"""`weather` command line: say / explain / climate.

Accessed via: bin/weather and `python -m weather_cli`. Everything here is wiring; all wording and
decisions live in the library modules so the voice host and the CLI cannot disagree.

Assumptions: exit codes — 0 ok, 1 runtime failure (no data, no location), 2 usage error.
"""

from __future__ import annotations

import errno
import io
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..cache import MemoryStore, Store, decode_bundle_text
from ..cache_fs import FileStore
from ..climate import climate_is_stale, load_climate, refresh_climate
from ..compose import say
from ..providers.home_assistant import HaConnection
from ..providers.json import js_number
from ..service import get_weather
from ..types import Bundle, Mode, Place
from ..units import parse_iso_ms, parse_js_number
from .args import Parsed, UsageError, parse_args
from .config import (
    VERSION,
    FileConfig,
    PlaceFlags,
    cache_dir,
    ha_connection,
    read_file_config,
    resolve_place,
)
from .render import HELP, format_time, render_explain, report_json

__all__ = ["main", "run"]

MODES: tuple[Mode, ...] = ("now", "tomorrow", "week", "umbrella", "dress")
OPTIONS = {
    **dict.fromkeys(("now", "tomorrow", "week", "umbrella", "dress", "json"), "boolean"),
    **dict.fromkeys(("lat", "lon", "tz", "timeout", "bundle", "at"), "string"),
    **dict.fromkeys(("offline", "no-cache", "no-ha", "no-open-meteo"), "boolean"),
}
COMMANDS = ("say", "explain", "climate")
DEFAULT_TIMEOUT_MS = 1200
MAX_TIMEOUT_MS = 86_400_000
CLIMATE_TIMEOUT_MS = 60_000
HELP_WORDS = (None, "--help", "-h", "help")
VERSION_WORDS = ("--version", "-V")


@dataclass(frozen=True, slots=True)
class Answer:
    bundle: Bundle
    origin: str
    errors: list[str]


def _text(parsed: Parsed, name: str) -> str | None:
    value = parsed.values.get(name)
    return value if isinstance(value, str) else None


def _flag(parsed: Parsed, name: str) -> bool:
    return parsed.values.get(name) is True


def _mode_of(parsed: Parsed) -> Mode:
    chosen = [mode for mode in MODES if _flag(parsed, mode)]
    if len(chosen) > 1:
        raise UsageError(f"pick one mode, got: {', '.join(chosen)}")
    return chosen[0] if chosen else "now"


def _timeout_ms(parsed: Parsed) -> float:
    raw = _text(parsed, "timeout")
    value = DEFAULT_TIMEOUT_MS if raw is None else parse_js_number(raw)
    if not 0 < value < float("inf"):
        raise UsageError("--timeout must be a positive number")
    if value > MAX_TIMEOUT_MS:
        raise UsageError(f"--timeout must be at most {MAX_TIMEOUT_MS} ms (one day)")
    return value


def _at_ms(parsed: Parsed) -> float:
    raw = _text(parsed, "at")
    if raw is None:
        return int(time.time() * 1000)
    parsed_ms = parse_iso_ms(raw)
    if parsed_ms is None:
        raise UsageError("--at must be an ISO time with offset, e.g. 2026-11-09T22:30:00+01:00")
    return parsed_ms


def _node_error(error: OSError, path: str) -> str:
    code = errno.errorcode.get(error.errno or 0, "EIO")
    described = {
        "ENOENT": "no such file or directory, open",
        "EACCES": "permission denied, open",
        "EISDIR": "illegal operation on a directory, read",
    }.get(code, f"{error.strerror}, open")
    # Node leaves the path out of the message when it is empty or the read (not the open) failed.
    if code == "EISDIR" or not path:
        return f"{code}: {described}"
    return f"{code}: {described} '{path}'"


def _load_bundle_file(path: str) -> Bundle:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as error:
        raise RuntimeError(_node_error(error, path)) from error
    bundle = decode_bundle_text(text)
    if bundle is None:
        raise RuntimeError(f"{path}: not a weather bundle")
    return bundle


def _climate_command(ctx: Context, place: Place, out: list[str]) -> None:
    store = ctx.store
    action = ctx.parsed.positionals[0] if ctx.parsed.positionals else "show"
    if action == "update":
        if not ctx.persistent:
            raise RuntimeError(
                "no cache directory: climate norms would not be stored "
                "(drop --no-cache, or set HOME or WEATHER_CACHE_DIR)"
            )
        climate = refresh_climate(store, place, timeout_ms=CLIMATE_TIMEOUT_MS)
        out.append(
            f"climate norms built from {climate.years[0]}–{climate.years[1]} "
            f"for {js_number(place.lat)},{js_number(place.lon)}\n"
        )
    elif action == "show":
        climate = load_climate(store, place)
        if climate is None:
            out.append("no climate norms cached; run `weather climate update`\n")
        else:
            stale = " (stale)" if climate_is_stale(climate, ctx.at) else ""
            built = format_time(climate.built_at, place.tz)
            out.append(f"norms {climate.years[0]}–{climate.years[1]}, built {built}{stale}\n")
    else:
        raise UsageError("climate: expected `update` or `show`")


@dataclass(frozen=True, slots=True)
class Context:
    """Everything the command needs that is validated before any work starts."""

    command: str
    parsed: Parsed
    # Holds the Home Assistant token: never part of a repr.
    env: Mapping[str, str] = field(repr=False)
    file: FileConfig
    store: Store
    # False when the store is memory-only (`--no-cache`, or no cache directory): nothing persists.
    persistent: bool
    at: float
    timeout_ms: float
    mode: Mode

    @property
    def ha(self) -> HaConnection | None:
        return None if _flag(self.parsed, "no-ha") else ha_connection(self.env, self.file)


def _context(command: str, rest: list[str], env: Mapping[str, str]) -> Context:
    parsed = parse_args(rest, OPTIONS)
    if command not in COMMANDS:
        raise UsageError(f"unknown command: {command}")
    timeout_ms = _timeout_ms(parsed)
    at = _at_ms(parsed)
    file = read_file_config(env)
    directory = None if _flag(parsed, "no-cache") else cache_dir(env)
    store: Store = MemoryStore() if directory is None else FileStore(directory)
    # `climate` has no mode, so its flags are not judged; the others are, before any fetch.
    mode: Mode = "now" if command == "climate" else _mode_of(parsed)
    return Context(command, parsed, env, file, store, directory is not None, at, timeout_ms, mode)


def _place_of(ctx: Context) -> Place:
    flags = PlaceFlags(_text(ctx.parsed, "lat"), _text(ctx.parsed, "lon"), _text(ctx.parsed, "tz"))
    return resolve_place(flags, ctx.env, ctx.file, ctx.ha, ctx.timeout_ms)


class _ReadOnlyStore:
    """Reads through, drops writes: a what-if clock must not stamp the shared cache."""

    def __init__(self, inner: Store) -> None:
        self._inner = inner

    def read(self, key: str) -> str | None:
        return self._inner.read(key)

    def write(self, key: str, value: str) -> None:
        """Deliberately nothing."""


def _fetch_answer(ctx: Context, place: Place) -> Answer:
    # With an explicit `--at` the live bundle would be stamped with that made-up time; a future
    # `--at` would then look fresh to every later run. Such runs may read the cache, never write.
    store = ctx.store if _text(ctx.parsed, "at") is None else _ReadOnlyStore(ctx.store)
    result = get_weather(
        place=place,
        store=store,
        now=ctx.at,
        timeout_ms=ctx.timeout_ms,
        ha=ctx.ha,
        open_meteo=not _flag(ctx.parsed, "no-open-meteo"),
        offline=_flag(ctx.parsed, "offline"),
    )
    return Answer(result.bundle, result.origin, result.errors)


def _emit(ctx: Context, answer: Answer, place: Place, stdout: list[str], stderr: list[str]) -> None:
    report = say(
        bundle=answer.bundle,
        now=ctx.at,
        mode=ctx.mode,
        climate=load_climate(ctx.store, place),
    )
    stderr.extend(f"warning: {error}\n" for error in answer.errors)
    if answer.origin == "stale":
        stamp = format_time(answer.bundle.fetched_at, place.tz)
        stderr.append(f"warning: offline, using data from {stamp}\n")
    if _flag(ctx.parsed, "json"):
        stdout.append(f"{report_json(report, answer.origin)}\n")
    elif ctx.command == "explain":
        stdout.append(f"{render_explain(report, place.tz)}\n")
    else:
        stdout.append(f"{report.text}\n")


def run(argv: list[str], env: Mapping[str, str], stdout: list[str], stderr: list[str]) -> int:
    """Executes one command line; output is collected so callers decide when to print."""
    command = argv[0] if argv else None
    if command is None or command in HELP_WORDS:
        stdout.append(HELP)
        return 0
    if command in VERSION_WORDS:
        stdout.append(f"{VERSION}\n")
        return 0
    ctx = _context(command, argv[1:], env)
    path = _text(ctx.parsed, "bundle")
    bundle = None if path is None else _load_bundle_file(path)
    place = bundle.place if bundle is not None else _place_of(ctx)
    if command == "climate":
        _climate_command(ctx, place, stdout)
        return 0
    answer = Answer(bundle, "file", []) if bundle is not None else _fetch_answer(ctx, place)
    _emit(ctx, answer, place, stdout, stderr)
    return 0


def _zone_message(error: ZoneInfoNotFoundError) -> str:
    """A bad zone name, or a machine with no tz database at all (then every name fails)."""
    try:
        ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        return "tz database not found (install the OS tz data, or `pip install tzdata`)"
    zone = str(error.args[0]).removeprefix("No time zone found with key ")
    return f"invalid time zone: {zone}"


def _utf8(stream: TextIO, errors: str) -> None:
    if isinstance(stream, io.TextIOWrapper):
        stream.reconfigure(encoding="utf-8", errors=errors)


def main(argv: list[str] | None = None) -> int:
    _utf8(sys.stdout, "strict")
    # Messages echo user input (an argument that is not valid UTF-8 arrives as lone surrogates):
    # report it escaped rather than die in a UnicodeEncodeError traceback.
    _utf8(sys.stderr, "backslashreplace")
    stdout: list[str] = []
    stderr: list[str] = []
    try:
        code = run(sys.argv[1:] if argv is None else argv, os.environ, stdout, stderr)
    except UsageError as error:
        stderr.append(f"weather: {error}\n")
        code = 2
    except ZoneInfoNotFoundError as error:
        stderr.append(f"weather: {_zone_message(error)}\n")
        code = 1
    except Exception as error:
        stderr.append(f"weather: {error}\n")
        code = 1
    sys.stdout.write("".join(stdout))
    sys.stderr.write("".join(stderr))
    return code
