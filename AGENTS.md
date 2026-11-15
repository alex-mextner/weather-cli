# weather-cli — agent guide

Weather in human Russian. A pure library of **decision functions** that turn raw forecast data
into short spoken phrases, plus a CLI (`weather say`, `weather explain`). TypeScript on bun, zero
runtime dependencies, one voice-assistant host (the AISIS speaker) vendors `src/` minus `src/cli/`.

## Run it

```
bun install
bin/weather say --lat 44.8 --lon 20.48 --tz Europe/Belgrade
bin/weather explain --week
bun run check        # oxlint + oxfmt --check + tsc --noEmit + bun test
```

## Layout

| Path                          | Responsibility                                                                 |
| ----------------------------- | ------------------------------------------------------------------------------ |
| `src/types.ts`                | domain types, fixed units: °C, m/s, mm, %, epoch ms                            |
| `src/thresholds.ts`           | every tunable number (`DEFAULT_THRESHOLDS`, `withThresholds`)                  |
| `src/time.ts`                 | local-time arithmetic (Intl only): calendar-day steps, never `ms + 24h`        |
| `src/ru.ts`                   | Russian wording: plurals, signed degrees, weekday/month forms                  |
| `src/conditions.ts`           | WMO / Home Assistant condition mapping, wet/snow classification                |
| `src/decisions/*`             | the decision functions; each returns a `Decision {fired, phrase, weight, why}` |
| `src/compose.ts`              | `say()`: runs decisions for a mode, applies the sentence budget                |
| `src/providers/*`             | Open-Meteo, Home Assistant, merge; JSON guards; hard timeouts                  |
| `src/cache.ts`, `cache-fs.ts` | `Store` seam, decoders, file store                                             |
| `src/climate.ts`              | month × hour norms from the ERA5 archive (background refresh only)             |
| `src/service.ts`              | `getWeather()`: providers + cache + deadline + offline fallback                |
| `src/cli/*`                   | CLI wiring only — not vendored by hosts                                        |

## Invariants

- **Spoken output is human.** No decimals, no km/h or m/s, no percentages, no `-0`. The property
  test in `tests/compose.test.ts` runs 1500 random worlds through every mode; keep it green.
- **Silence is a decision.** Feels-like needs a ≥ 6° gap (on whole spoken degrees), humidity needs
  a notable deviation from the month/hour norm, wind needs a real category. Each decision records
  `why` even when silent — `weather explain` prints it.
- **Voice never waits.** Every request has `AbortSignal.timeout` (default 1.2 s); a fresh cache hit
  does no network; all providers failing falls back to the last cached bundle. The climate archive
  (2.7 MB) is never fetched on the answer path.
- **Calendar math is local.** "Tomorrow", months and evening checks use the place's IANA zone.
  `tests/time-ru.test.ts` pins the DST days.
- **Provider payloads are never asserted.** Narrow with the guards in `providers/json.ts`; cache
  decoders rebuild objects field by field.
- **Host-portable code.** Imports are extensionless, only `node:` builtins and global `fetch`;
  `src/` (without `cli/`) is what the AISIS container vendors at a pinned tag.
- No secrets in the repo: the Home Assistant token comes from the environment only.

## Releasing

Bump `version` in `package.json` (`--version` reads it), update `CHANGELOG.md`, tag `vX.Y.Z`,
`gh release create`. Hosts re-vendor with their own script at the new tag.
