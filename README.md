# weather-cli

Weather in human Russian. Raw forecast data goes in; short phrases a person would say out loud
come out — «На улице 8 градусов, идёт дождь. Сильный ветер, зонт может вывернуть.» — built from a
library of small, tested **decision functions**: what is worth saying, what is better left silent.

```
$ weather say
На улице 7 градусов, пасмурно. Скоро начнётся дождь. Послезавтра потеплеет до 20 градусов.

$ weather say --umbrella
Да, скоро начнётся дождь.

$ weather say --tomorrow
Завтра днём 15, ночью 5 градусов, пасмурно, возможен дождь. Послезавтра потеплеет до 20 градусов.
```

## What it decides

| Question     | Rule                                                                                                                                                                                                                                                                                                             |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Temperature  | Whole degrees only, `-0` never: «минус 3 градуса», «ноль градусов».                                                                                                                                                                                                                                              |
| Feels like   | Said **only** when it differs from the air by **≥ 6°** either way (on the rounded numbers a listener hears). Wording follows the cause: «на ветру ощущается как минус 2», «дует — ощущается как минус 7», «из-за влажности ощущается как 35». Computed (Steadman) when the provider has no apparent temperature. |
| Humidity     | Never as a percentage. Compared with the **month × hour-of-day norm** of the place (ten years of ERA5): «суше обычного для ноября», «очень сыро для ноября» — silent inside the norm, and not repeated while it rains.                                                                                           |
| Wind         | Categories that matter for an umbrella, gusts included: nothing below ~6 m/s; «ветрено»; «сильный ветер»; «штормовой ветер, зонт унесёт». Never a speed.                                                                                                                                                         |
| Next hour    | If within ~1 h rain/snow starts or ends, thunder is due, the air jumps ≥ 3°, or the wind picks up: «через час начнётся дождь», «дождь скоро закончится».                                                                                                                                                         |
| Late evening | From 21:00 local a "now" answer also carries tomorrow. `--tomorrow` asks for it any time.                                                                                                                                                                                                                        |
| Insight      | One line about the coming days, only if something changes: warming/cooling (≥ 5° against today/tomorrow), first snow, rain onset/end, first frost, heat. A quiet week says nothing.                                                                                                                              |
| Advice       | `--umbrella` («Да, вечером будет дождь.»), `--dress` («Прохладно: тёплая куртка и шарф.»), plus a short umbrella hint in a rainy "now" answer.                                                                                                                                                                   |

Every threshold lives in [`src/thresholds.ts`](src/thresholds.ts) and is overridable. A voice answer
is a headline plus at most two more sentences; extra findings compete by weight and the losers are
reported by `weather explain`.

More answers, from the bundled [`examples/`](examples) (saved bundles, so the output is reproducible):

```
$ weather say --bundle examples/rain-wind-evening.json --at 2026-11-09T21:40:00+01:00
На улице 8 градусов, идёт дождь. Сильный ветер, зонт может вывернуть. Завтра днём 11, ночью 6 градусов, пасмурно.

$ weather say --bundle examples/windy-cold-morning.json --at 2026-12-15T08:00:00+01:00
На улице 2 градуса, дует — ощущается как минус 7, облачно с прояснениями. Сильный ветер.

$ weather say --bundle examples/calm-noon.json --at 2026-10-12T12:15:00+02:00
На улице 18 градусов, ясно.

$ weather say --week --bundle examples/snow-week.json --at 2026-12-01T09:00:00+01:00
На неделе от минус 9 до 4 градусов, осадки в 3 из 8 дней. Послезавтра пойдёт снег. Послезавтра ночью заморозки до минус 2 градусов. Осадки закончатся к пятнице.

$ weather say --dress --bundle examples/windy-cold-morning.json --at 2026-12-15T08:00:00+01:00
Мороз: тёплая зимняя куртка, шапка и перчатки.
```

The calm noon is one short sentence: nothing else was worth saying.

## Why did it say that? `weather explain`

```
$ weather explain
mode now · data as of 2026-11-09 01:28 (home-assistant+open-meteo)

✔ temperature   «7 градусов»
    air 6.9° → spoken 7
· feels         silent
    air 7°, feels 4° → gap -3° (needs ≥ 6° either way)
✔ sky           «пасмурно»
✔ lookahead     «скоро начнётся дождь»
    start: wet hour at +32min (rain, 0.4 mm)
· wind          silent
    wind 3.1 m/s, gust 6.5 m/s → none, wet
· humidity      silent
    humidity 87% vs norm 86±10% (month 11, 1h) → z=0.10, gap 1 (needs |z| ≥ 1, |gap| ≥ 10)
✔ insight       «послезавтра потеплеет до 20 градусов»
✂ advice        «возьми зонт»
→ На улице 7 градусов, пасмурно. Скоро начнётся дождь. Послезавтра потеплеет до 20 градусов.
  (✂ fired but over the sentence budget: advice)
```

## Install

Needs [bun](https://bun.sh) ≥ 1.4.

```
git clone https://github.com/alex-mextner/weather-cli && cd weather-cli
bun install && ./install.sh          # links ~/.local/bin/weather
weather climate update               # once: monthly norms for humidity/temperature (≈ 2.7 MB, seconds)
```

Set your place once (kept outside the repo, never commit it):

```
mkdir -p ~/.config/weather-cli
echo '{"lat": 44.8, "lon": 20.4, "tz": "Europe/Belgrade"}' > ~/.config/weather-cli/config.json
```

`install.sh` creates the symlink `~/.local/bin/weather -> <checkout>/bin/weather`; callers that run
`weather` by name (e.g. a voice assistant) need `~/.local/bin` on `PATH`.

## Where the data comes from

- **Open-Meteo** (free, no key): current, 48 hours, 8 days — the only source of apparent temperature,
  gusts and precipitation probability. Always on, `--no-open-meteo` to switch off.
- **Home Assistant** (primary when configured): the `weather.*` entity and `weather.get_forecasts`.
  Set `WEATHER_HA_URL` and `WEATHER_HA_TOKEN` (or `HA_URL` / `HA_TOKEN`; optional
  `WEATHER_HA_ENTITY`). Works through a caching proxy with the same API. The token is read from the
  environment only and is never stored.
- Both run concurrently under one hard deadline (`--timeout`, default **1200 ms**). A fresh cache
  (10 min) answers without network; if every provider fails the last cached data (up to 12 h)
  answers, with a warning on stderr — a voice assistant never waits and never goes silent.
- Place: `--lat --lon [--tz]`, `WEATHER_LAT/LON/TZ`, `~/.config/weather-cli/config.json`
  (`{"lat":…,"lon":…,"tz":"Europe/Belgrade"}`), or Home Assistant's home zone.

## CLI

```
weather say [--now|--tomorrow|--week|--umbrella|--dress] [--json] [--lat N --lon N --tz ZONE]
weather explain [mode flags]        decisions that fired or stayed silent, and why
weather climate update|show         monthly norms for the place
  --timeout MS   --offline   --no-cache   --no-ha   --no-open-meteo
  --bundle FILE --at ISO_TIME       answer from a saved bundle at a given moment
```

## Library

```ts
import { FileStore, getWeather, loadClimate, say } from 'weather-cli';

const place = { lat: 44.8, lon: 20.48, tz: 'Europe/Belgrade' };
const store = new FileStore('/var/cache/weather');
const { bundle } = await getWeather({ place, store, ha: { url, token } });
const report = say({
  bundle,
  now: Date.now(),
  mode: 'now',
  climate: await loadClimate(store, place),
});
report.text; // «На улице 7 градусов, пасмурно. Скоро начнётся дождь.»
report.decisions; // every decision with `fired` and `why`
```

Each decision is also exported (`describeFeels`, `describeHumidity`, `describeWind`,
`describeLookahead`, `describeTomorrow`, `weekInsights`, …) — pure functions of data and thresholds.
Hosts that cannot install packages can vendor `src/` (without `src/cli/`) at a release tag: imports are
extensionless and only `node:` built-ins and global `fetch` are used.

## Development

```
bun install
bun run check     # oxlint, oxfmt --check, tsc --noEmit, bun test
```

Tests are table-driven per decision (norm vs off-norm, the 5°/6° feels-like boundary, wind
thresholds, 20:59 vs 21:00, the two DST days) plus a property test: 1500 random worlds × 5 modes never
produce a decimal, a speed unit, a percentage or a minus sign. See [AGENTS.md](AGENTS.md) for the layout.

MIT
