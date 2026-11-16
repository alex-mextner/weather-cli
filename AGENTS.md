# weather-cli — agent guide

Weather in human Russian. A pure library of **decision functions** that turn raw forecast data
into short spoken phrases, plus a CLI (`weather say`, `weather explain`). Python ≥ 3.10, standard
library only, plus a system tz database (Windows is untested; a `tzdata` wheel is declared for it); one
voice-assistant host (the AISIS speaker) vendors the `weather_cli` package minus `weather_cli/cli`; it must stay importable as top-level
`weather_cli` (some modules import `weather_cli.…` absolutely).

## Run it

```
./install.sh                     # links ~/.local/bin/weather; needs only python3 >= 3.10
bin/weather say --lat 44.8 --lon 20.48 --tz Europe/Belgrade
bin/weather explain --week
python -m pytest                 # tests
ruff check && ruff format --check
rig lint check                   # maintainer-only local gate (not in CI): ≤ 15 cognitive / ≤ 20 cyclomatic, file size
```

## Layout

| Path                                  | Responsibility                                                                |
| ------------------------------------- | ----------------------------------------------------------------------------- |
| `weather_cli/types.py`                | domain types, fixed units: °C, m/s, mm, %, epoch ms                           |
| `weather_cli/thresholds.py`           | every tunable number (`DEFAULT_THRESHOLDS`, `with_thresholds`)                |
| `weather_cli/local_time.py`           | local-time arithmetic (zoneinfo): calendar-day steps, never `ms + 24h`        |
| `weather_cli/ru.py`                   | Russian wording: plurals, signed degrees, weekday/month forms                 |
| `weather_cli/conditions.py`           | WMO / Home Assistant condition mapping, wet/snow classification               |
| `weather_cli/decisions/*`             | the decision functions; each returns a `Decision(fired, phrase, weight, why)` |
| `weather_cli/compose.py`              | `say()`: runs decisions for a mode, applies the sentence budget               |
| `weather_cli/providers/*`             | Open-Meteo, Home Assistant, merge; JSON guards, JSON writer, hard timeouts    |
| `weather_cli/cache.py`, `cache_fs.py` | `Store` seam, decoders/encoders, file store                                   |
| `weather_cli/climate.py`              | month × hour norms from the ERA5 archive (background refresh only)            |
| `weather_cli/service.py`              | `get_weather()`: providers + cache + deadline + offline fallback              |
| `weather_cli/units.py`                | conversions and the JS-compatible helpers (`js_round`, `parse_js_number`)     |
| `weather_cli/cli/*`                   | CLI wiring only — not vendored by hosts                                       |
| `bin/weather`                         | bash wrapper: resolves symlinks, runs `weather_cli.__main__` with python3     |

## Invariants

- **Spoken output is human.** No decimals, no km/h or m/s, no percentages, no `-0`. The property
  test in `tests/test_compose.py` runs 1500 random worlds through every mode; keep it green.
- **Silence is a decision.** Feels-like needs a ≥ 6° gap (on whole spoken degrees), humidity needs
  a notable deviation from the month/hour norm, wind needs a real category. Each decision records
  `why` even when silent — `weather explain` prints it.
- **Voice never waits.** Every request has a hard timeout (default 1.2 s) and `get_weather()` stops
  waiting for providers at that deadline; a fresh cache hit does no network; all providers failing
  falls back to the last cached bundle. The climate archive (2.7 MB) is never fetched on the answer
  path.
- **Calendar math is local.** "Tomorrow", months and evening checks use the place's IANA zone.
  `tests/test_time_ru.py` pins the DST days.
- **Provider payloads are never asserted.** Narrow with the guards in `providers/json.py`; cache
  decoders rebuild objects field by field.
- **JS-compatible output.** Saved bundles, cache files and `--json` keep the original camelCase keys
  and print numbers the way `JSON.stringify` did (`8`, not `8.0`); `js_round` replaces Python's
  banker's `round` wherever a value is spoken.
- **Host-portable code.** Standard library only; the network is injected as `fetch_impl`;
  `weather_cli/` (without `cli/`) is what the AISIS container vendors at a pinned tag.
- **Complexity gate.** Every function stays within cognitive ≤ 15 / cyclomatic ≤ 20; split into
  small named helpers instead of growing one.
- No secrets in the repo: the Home Assistant token comes from the environment only.

## Releasing

Bump `version` in `pyproject.toml` and `VERSION` in `weather_cli/cli/config.py` (a test keeps
them equal; `--version` prints the constant), update `CHANGELOG.md`, tag `vX.Y.Z`,
`gh release create`. Hosts re-vendor with their own script at the new tag.
