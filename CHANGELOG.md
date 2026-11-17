# Changelog

## 0.2.0

- Rain now has one source of truth (#2): an observed precipitating condition wins over a smaller model
  amount for the current moment, and the `now` sentence, the lookahead, `--umbrella` and `--dress` all derive from it.
  `weather explain` names the source that decided (`precipitation now: wet (condition, home-assistant: rain, 0 mm)`).
  Saved bundles may carry an optional `currentSource`.
- Every function is within the complexity limits and `--dress` reads the umbrella verdict instead of its wording (#4).
- `--at` accepts ISO-8601 only (the documented format); loose `Date.parse` forms exit 2 with a usage
  message, and so do offsets of 24 h or more or with minutes ≥ 60 (an HA forecast entry with such a
  `datetime` is skipped, not fatal). Only ASCII digits count as digits.
- Rewritten in Python (≥ 3.10, standard library only; a system tz database is required; Windows is untested) with spoken text, `explain` text, `--json` output, flags, exit codes and on-disk cache
  format identical to 0.1.0 except for the items listed in this entry. No bun or TypeScript any more.
- Install: `./install.sh` only needs `python3`; no `bun install` step.
- Library: `from weather_cli import get_weather, say, ...` (snake_case names, keyword-only options)
  instead of `import { getWeather, say } from 'weather-cli'`. Network access is injected with
  `fetch_impl`. Hosts may vendor the `weather_cli` package minus `weather_cli/cli`.
- `get_weather()` treats `timeout_ms` as one overall deadline for both providers; the Home Assistant
  forecast calls get a smaller share of it, so a stalled forecast no longer discards the state that
  already arrived. The place lookup (`resolve_place`) is capped by `--timeout` too.
- Security: the Home Assistant token is stripped of surrounding whitespace, never echoed in an error
  or a request repr, and `Authorization` is dropped on a redirect to another origin (an https→http
  redirect is refused); only `http`/`https` URLs are fetched.
- Cache: a bundle stamped more than 5 min in the future is a miss (clock skew), a run with an explicit
  `--at` reads the cache but never writes it, `--offline` never makes a request (cache only, any age;
  an empty cache is an error), and cache files are written through a unique temp file. No `HOME`
  means no cache, not a crash. Integer literals beyond the float range read as missing, not as a crash.
- Provider errors no longer repeat the provider name (`open-meteo: request failed (...)`), and a bundle
  from a single provider carries `currentSource`, so `explain` names it.
- Deliberate deviation from the 0.1.0 oracle: the spoken snow answer «Идёт снег, зонт не обязателен, но
  но скоро закончится.» had a doubled «но»; it is now «…, но скоро закончится.». 10 recorded parity
  cases changed accordingly (8 in `library_edge.json`, 2 in `library_random.json`; texts and the
  derived `explain`/`--json` hashes were re-derived and checked against the old hashes first). The
  parity case `cache/future-fetch` now expects the offline warning, because a bundle 15 min ahead of
  the clock is no longer "fresh".
- Home Assistant forecasts: a failed or slow forecast call adds `home-assistant: forecast unavailable`
  to the warnings, such a bundle (no hourly and no daily data) is not cached, and `--umbrella` then
  says «Сейчас без дождя, но прогноза на ближайшие часы нет.» or «…когда закончится, неизвестно»
  instead of claiming "dry" / "won't stop". The state request keeps 95% of the timeout, forecasts 80%.
- Empty or relative `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `WEATHER_CACHE_DIR` count as unset (never the
  working directory). Blank `--lat/--lon`/`WEATHER_LAT/LON` count as unset (the 0.1.0 oracle read them
  as 0; parity cases `place/lat-empty-is-zero`, `env/empty-lat-is-zero` now expect "no location").
- Conflicting mode flags exit 2 before any fetch or bundle read (parity case
  `modes/conflict-with-bad-bundle` now expects exit 2). `weather climate update` without a cache
  directory (`--no-cache`, no `HOME`) fails instead of downloading into a throwaway store.
- Cache hits: a cached bundle is skipped (treated as a miss) when its sources are not among the
  providers asked for now (`--no-ha`, `--no-open-meteo`, HA config) or its zone differs from the
  requested one; with no provider to ask, the cache still answers. A bundle whose HA forecast only
  partly failed is not cached either. Cache files whose `date` is not a real `YYYY-MM-DD` or whose
  norm grids are not 12×24 numbers are misses, not crashes.
- Provider responses are capped at 8 MiB (`response too large`); error responses are closed.
- Blank `WEATHER_HA_URL`/`WEATHER_HA_TOKEN`/`WEATHER_TZ`/`--tz` count as unset, and an unknown zone
  is rejected up front (`weather: invalid time zone: …`, exit 1) before any network request
  (parity case `cache/bad-flag-tz` now expects that).
- Cache files are now written owner-only (0600; the 0.1.0 files followed the umask). A host that
  shares a cache directory with the CLI must run as the same user, or reads are silent misses.
- `bin/weather` refuses to run, and never falls back to a package in the working directory, when it
  is run without its `weather_cli/` package.
- Response bodies are read in chunks under the same wall-clock deadline as the request (a peer
  dripping bytes ends in `request failed (timeout)`), as well as the 8 MiB cap.
  The cap and the deadline cover a redirect answer's body too and are shared by every hop of one
  request (`urllib` used to swallow that body unbounded before following).
  At the deadline the connections themselves are shut down, so a peer that drips chunk framing or
  trailers (where `http.client` never hands control back) ends in `request failed (timeout)` too.
- A forecast that arrives empty is unknown, never "nothing coming": a provider answering with no
  hourly forecast (Home Assistant or Open-Meteo), and a bundle whose hours have all passed, make
  `--umbrella` say «Сейчас без дождя, но прогноза на ближайшие часы нет.» / «…когда закончится,
  неизвестно» instead of «Нет, дождя не ожидается.» / «и скоро не закончится». Such a bundle is
  reported as `<provider>: forecast unavailable` and not cached. Home Assistant's hours lead only while
  at least 6 of them still lie ahead; otherwise Open-Meteo's are used, and a short Home Assistant
  forecast is kept when Open-Meteo has none. 72 recorded umbrella parity cases (stale bundles) now expect that wording.
- The "now" headline's sky follows the same precipitation decision as the umbrella answer: a wet
  amount under a dry-sounding code («пасмурно», 0.6 mm) says what falls, and a precipitating code
  the decision rejected says «пасмурно».
- `weather explain --week` marks a decision cut (✂) only when its sentence was left out; the oracle
  marked every insight cut as soon as any was (48 recorded library cases and one CLI case differ in those marks only).
- `TZ` is used as the local zone only if it names a zone the machine has (`UTC0`, `:/etc/localtime`
  fall through to `/etc/localtime`, then UTC). Surrounding whitespace is stripped from blank-checked
  settings. An argument that is not valid UTF-8 is reported escaped on stderr, not as a traceback.
  Epoch values within two days of the year 1 / 9999 limits (any zone could overflow there) are
  treated as missing everywhere they enter: cache `fetchedAt`/`builtAt`, provider times, `--at`.
- `--help` gained one line about negative numbers (`--lon=-74`); the recorded 0.1.0 help differs by
  exactly that line. `Authorization` is also dropped when a redirect only changes `http` to `https` on
  the same host (deliberate, like `fetch`): configure an https URL.
- Deliberate deviations (recorded cases noted in `tests/test_parity.py`): an explicit `--lat/--lon`
  (or `WEATHER_LAT/LON`) that is not a finite number is a usage error (exit 2) instead of falling
  through to another place; climate norms built for another zone read as "not cached"; a bundle
  stamped far in the future is not served as stale either; `--timeout` above one day is a usage
  error; cache directories are created 0700; HA keeps a state that arrived when the forecast
  wrapper fails, and `/api/states` (entity discovery) may be up to 32 MiB.
- `0x…`/`0o…`/`0b…` numbers are validated digit by digit; absurdly long ones read as Infinity.
- `weather_cli/providers` is a regular package; a `tzdata` dependency marker for Windows (untested); `bin/weather` checks for
  Python ≥ 3.10; `install.sh` probes the tz database; a missing tz database reports
  `tz database not found` instead of `invalid time zone`.

## 0.1.0

- Decision functions: rounded temperature, feels-like only on a ≥ 6° gap, humidity against the
  month/hour norm, wind by umbrella category (gusts included), next-hour lookahead, tomorrow
  (automatic after 21:00 local), week insights (warming/cooling, snow/rain onset and end, frost,
  heat), umbrella and clothing answers.
- Providers: Home Assistant (`weather.*` + `get_forecasts`) and Open-Meteo, merged; file cache,
  offline fallback, 1.2 s deadline.
- Climate norms from the ERA5 archive, cached per place.
- CLI: `weather say [--now|--tomorrow|--week|--umbrella|--dress] [--json]`, `weather explain`,
  `weather climate update|show`.
