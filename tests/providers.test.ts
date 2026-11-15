import { describe, expect, test } from 'bun:test';

import {
  MemoryStore,
  bundleKey,
  climateKey,
  readBundle,
  readClimate,
  writeBundle,
} from '../src/cache';
import { archiveYears, loadClimate, parseArchive, refreshClimate } from '../src/climate';
import type { Fetcher, HttpResponse, JsonValue } from '../src/providers/json';
import {
  fetchHaPlace,
  fetchHomeAssistant,
  parseHomeAssistant,
} from '../src/providers/home-assistant';
import { mergeBundles } from '../src/providers/merge';
import { fetchOpenMeteo, openMeteoUrl, parseOpenMeteo } from '../src/providers/open-meteo';
import { getWeather } from '../src/service';
import type { Bundle } from '../src/types';
import { HOUR, PLACE, TZ, at, bundle } from './fixtures';

const NOW = at('2026-11-09', 14, 10);
const S = (ms: number): number => Math.floor(ms / 1000);

function openMeteoPayload(): JsonValue {
  const start = Math.floor(NOW / HOUR) * HOUR - HOUR;
  const times = Array.from({ length: 6 }, (_, i) => S(start + i * HOUR));
  return {
    current: {
      time: S(NOW),
      temperature_2m: 7.4,
      relative_humidity_2m: 70,
      apparent_temperature: 1.1,
      precipitation: 0,
      weather_code: 3,
      wind_speed_10m: 7,
      wind_gusts_10m: 12,
    },
    hourly: {
      time: times,
      temperature_2m: [7, 7.4, 7.2, 6.8, 6.5, 6.2],
      apparent_temperature: [2, 3, 3, 2, 1, 0],
      relative_humidity_2m: [70, 70, 72, 74, 75, 76],
      wind_speed_10m: [5, 6, 6, 7, 7, 7],
      wind_gusts_10m: [9, 10, 11, 12, 12, 12],
      // values are labelled with the END of the hour: 1.4 mm at 16:00 fell during 15:00-16:00
      precipitation: [0, 0, 0, 1.4, 0, 0],
      precipitation_probability: [0, 0, 10, 90, 20, 0],
      weather_code: [3, 3, 3, 61, 3, 3],
    },
    daily: {
      time: [S(at('2026-11-09', 0)), S(at('2026-11-10', 0))],
      weather_code: [3, 61],
      temperature_2m_max: [11.4, 14.2],
      temperature_2m_min: [5.6, 6.1],
      precipitation_sum: [1.4, 5],
      snowfall_sum: [0, 0],
      precipitation_probability_max: [90, 80],
      wind_speed_10m_max: [8, 9],
      wind_gusts_10m_max: [14, 16],
    },
  };
}

function haState(): JsonValue {
  return {
    entity_id: 'weather.forecast_home',
    state: 'cloudy',
    last_updated: new Date(NOW - 5 * 60_000).toISOString(),
    attributes: {
      temperature: 6.9,
      humidity: 87,
      wind_speed: 18,
      wind_speed_unit: 'km/h',
      temperature_unit: '°C',
      precipitation_unit: 'mm',
    },
  };
}

function haForecast(kind: 'hourly' | 'daily'): JsonValue {
  const entries =
    kind === 'hourly'
      ? Array.from({ length: 8 }, (_, i) => ({
          condition: i === 3 ? 'rainy' : 'cloudy',
          datetime: new Date(Math.floor(NOW / HOUR) * HOUR + (i - 1) * HOUR).toISOString(),
          temperature: 7 - i * 0.2,
          wind_speed: 18,
          precipitation: i === 3 ? 0.8 : 0,
          humidity: 85,
        }))
      : [
          {
            condition: 'partlycloudy',
            datetime: '2026-11-09T11:00:00+00:00',
            temperature: 11.5,
            templow: 5.6,
            wind_speed: 36,
            precipitation: 1.6,
          },
        ];
  return {
    changed_states: [],
    service_response: { 'weather.forecast_home': { forecast: entries } },
  };
}

describe('open-meteo', () => {
  test('parses units, relabels precipitation to the hour start and drops the unknown last hour', () => {
    const b = parseOpenMeteo(openMeteoPayload(), PLACE, NOW);
    expect(b.current).toMatchObject({
      temp: 7.4,
      feels: 1.1,
      humidity: 70,
      wind: 7,
      gust: 12,
      condition: 'cloudy',
    });
    expect(b.hourly).toHaveLength(5);
    const rainy = b.hourly.find((m) => m.precip === 1.4);
    expect(rainy?.time).toBe(at('2026-11-09', 15));
    expect(rainy?.condition).toBe('rain');
    expect(rainy?.precipProb).toBe(90);
    expect(b.daily.map((d) => d.date)).toEqual(['2026-11-09', '2026-11-10']);
    expect(b.daily[1]).toMatchObject({
      max: 14.2,
      min: 6.1,
      condition: 'rain',
      precip: 5,
      gustMax: 16,
    });
  });

  test('request asks for metres per second and unix time', () => {
    const url = openMeteoUrl(PLACE);
    expect(url).toContain('wind_speed_unit=ms');
    expect(url).toContain('timeformat=unixtime');
    expect(url).toContain('timezone=Europe%2FBelgrade');
  });

  test('garbage payloads are errors, not bundles', () => {
    expect(() => parseOpenMeteo({}, PLACE, NOW)).toThrow('open-meteo');
    expect(() => parseOpenMeteo({ current: { temperature_2m: null } }, PLACE, NOW)).toThrow(
      'temperature',
    );
  });
});

describe('home assistant', () => {
  test('converts km/h to m/s, reads condition and forecasts', () => {
    const b = parseHomeAssistant(haState(), haForecast('hourly'), haForecast('daily'), PLACE, NOW);
    expect(b.current.wind).toBeCloseTo(5, 5);
    expect(b.current.condition).toBe('cloudy');
    expect(b.current.feels).toBeUndefined();
    expect(b.hourly).toHaveLength(8);
    expect(b.hourly.find((m) => m.condition === 'rain')?.precip).toBe(0.8);
    expect(b.daily[0]).toMatchObject({ date: '2026-11-09', max: 11.5, min: 5.6, precip: 1.6 });
    expect(b.daily[0]?.windMax).toBeCloseTo(10, 5);
  });

  test('fahrenheit and mph are converted', () => {
    const state: JsonValue = {
      entity_id: 'weather.x',
      state: 'sunny',
      attributes: {
        temperature: 50,
        temperature_unit: '°F',
        wind_speed: 10,
        wind_speed_unit: 'mph',
      },
    };
    const b = parseHomeAssistant(state, null, null, PLACE, NOW);
    expect(b.current.temp).toBeCloseTo(10, 5);
    expect(b.current.wind).toBeCloseTo(4.4704, 4);
    expect(b.hourly).toEqual([]);
  });

  test('an unavailable entity is an error', () => {
    expect(() =>
      parseHomeAssistant(
        { entity_id: 'weather.x', state: 'unavailable', attributes: {} },
        null,
        null,
        PLACE,
        NOW,
      ),
    ).toThrow('unavailable');
  });

  test('place comes from HA config', async () => {
    const fetcher: Fetcher = async () => reply({ latitude: 44.8, longitude: 20.48, time_zone: TZ });
    expect(
      await fetchHaPlace({ url: 'http://ha', token: 't' }, { timeoutMs: 100, fetchImpl: fetcher }),
    ).toEqual(PLACE);
  });
});

function reply(body: JsonValue, status = 200): HttpResponse {
  return { ok: status < 400, status, text: async () => JSON.stringify(body) };
}

describe('merge', () => {
  const primary = (): Bundle =>
    parseHomeAssistant(haState(), haForecast('hourly'), haForecast('daily'), PLACE, NOW);
  const secondary = (): Bundle => parseOpenMeteo(openMeteoPayload(), PLACE, NOW);

  test('home assistant leads, open-meteo adds gusts and a feels-like offset', () => {
    const m = mergeBundles(primary(), secondary(), NOW);
    expect(m.sources).toEqual(['home-assistant', 'open-meteo']);
    expect(m.current.temp).toBe(6.9); // HA's own temperature
    expect(m.current.gust).toBe(12);
    // OM says 7.4 → feels 1.1 (−6.3): applied as an offset to HA's 6.9, not copied
    expect(m.current.feels).toBeCloseTo(6.9 - 6.3, 5);
  });

  test('stale primary current is replaced', () => {
    const old = primary();
    old.current.time = NOW - 3 * HOUR;
    expect(mergeBundles(old, secondary(), NOW).current.temp).toBe(7.4);
  });

  test('missing provider: the other one is used as is', () => {
    expect(mergeBundles(null, secondary(), NOW).sources).toEqual(['open-meteo']);
    expect(mergeBundles(primary(), null, NOW).sources).toEqual(['home-assistant']);
  });

  test('days beyond the primary horizon come from the secondary', () => {
    const p = primary();
    p.daily = [
      p.daily[0] as Bundle['daily'][number],
      { ...(p.daily[0] as Bundle['daily'][number]), date: '2026-11-10' },
      { ...(p.daily[0] as Bundle['daily'][number]), date: '2026-11-11' },
    ];
    const s = secondary();
    s.daily = [...s.daily, { ...(s.daily[0] as Bundle['daily'][number]), date: '2026-11-12' }];
    expect(mergeBundles(p, s, NOW).daily.map((d) => d.date)).toEqual([
      '2026-11-09',
      '2026-11-10',
      '2026-11-11',
      '2026-11-12',
    ]);
  });
});

describe('getWeather: caching, deadlines, offline', () => {
  function router(handlers: {
    om?: () => HttpResponse | Promise<HttpResponse>;
    ha?: () => HttpResponse | Promise<HttpResponse>;
  }): { fetcher: Fetcher; calls: string[] } {
    const calls: string[] = [];
    const fetcher: Fetcher = async (url) => {
      calls.push(url);
      if (url.includes('open-meteo')) {
        if (!handlers.om) throw new Error('offline');
        return handlers.om();
      }
      if (!handlers.ha) throw new Error('offline');
      return handlers.ha();
    };
    return { fetcher, calls };
  }
  const omOk = (): HttpResponse => reply(openMeteoPayload());

  test('live fetch is cached; a second call within ten minutes does no network', async () => {
    const store = new MemoryStore();
    const { fetcher, calls } = router({ om: omOk });
    const first = await getWeather({ place: PLACE, store, now: NOW, fetchImpl: fetcher });
    expect(first.origin).toBe('live');
    const callsAfterFirst = calls.length;
    const second = await getWeather({
      place: PLACE,
      store,
      now: NOW + 5 * 60_000,
      fetchImpl: fetcher,
    });
    expect(second.origin).toBe('cache');
    expect(calls.length).toBe(callsAfterFirst);
    expect(second.bundle.current.temp).toBe(7.4);
  });

  test('offline: the last cached bundle answers, flagged stale', async () => {
    const store = new MemoryStore();
    await getWeather({ place: PLACE, store, now: NOW, fetchImpl: router({ om: omOk }).fetcher });
    const result = await getWeather({
      place: PLACE,
      store,
      now: NOW + 3 * HOUR,
      fetchImpl: router({}).fetcher,
    });
    expect(result.origin).toBe('stale');
    expect(result.errors.join()).toContain('open-meteo');
  });

  test('offline with an ancient cache or none is an error', async () => {
    const store = new MemoryStore();
    await expect(
      getWeather({ place: PLACE, store, now: NOW, fetchImpl: router({}).fetcher }),
    ).rejects.toThrow('no weather data');
    await getWeather({ place: PLACE, store, now: NOW, fetchImpl: router({ om: omOk }).fetcher });
    await expect(
      getWeather({ place: PLACE, store, now: NOW + 13 * HOUR, fetchImpl: router({}).fetcher }),
    ).rejects.toThrow('no weather data');
  });

  test('one provider failing still answers from the other, with a warning', async () => {
    const { fetcher } = router({ om: omOk });
    const result = await getWeather({
      place: PLACE,
      now: NOW,
      fetchImpl: fetcher,
      ha: { url: 'http://ha', token: 't', entityId: 'weather.forecast_home' },
    });
    expect(result.origin).toBe('live');
    expect(result.bundle.sources).toEqual(['open-meteo']);
    expect(result.errors.join()).toContain('home-assistant');
  });

  test('a hung provider is cut at the deadline instead of delaying the answer', async () => {
    const hang: Fetcher = (_, init) =>
      new Promise((_resolve, reject) =>
        init.signal.addEventListener('abort', () => reject(new Error('aborted'))),
      );
    const started = Date.now();
    await expect(
      getWeather({ place: PLACE, now: NOW, fetchImpl: hang, timeoutMs: 80 }),
    ).rejects.toThrow('no weather data');
    expect(Date.now() - started).toBeLessThan(500);
  });

  test('both providers combine when both answer', async () => {
    const { fetcher } = router({
      om: omOk,
      ha: () => reply(haState()),
    });
    // the HA forecast calls are POSTs to the same host: reply with a forecast body for them
    const wrapped: Fetcher = async (url, init) =>
      url.includes('get_forecasts')
        ? reply(haForecast(init.body?.includes('hourly') ? 'hourly' : 'daily'))
        : fetcher(url, init);
    const result = await getWeather({
      place: PLACE,
      now: NOW,
      fetchImpl: wrapped,
      ha: { url: 'http://ha', token: 't', entityId: 'weather.forecast_home' },
    });
    expect(result.bundle.sources).toEqual(['home-assistant', 'open-meteo']);
    expect(result.bundle.current.gust).toBe(12);
  });

  test('fetch helpers send the bearer token', async () => {
    let auth = '';
    const fetcher: Fetcher = async (_url, init) => {
      auth = init.headers.authorization ?? '';
      return reply({ latitude: 1, longitude: 2, time_zone: TZ });
    };
    await fetchHaPlace(
      { url: 'http://ha', token: 'secret' },
      { timeoutMs: 100, fetchImpl: fetcher },
    );
    expect(auth).toBe('Bearer secret');
    await expect(
      fetchOpenMeteo(PLACE, { timeoutMs: 100, fetchImpl: async () => reply({}, 500) }),
    ).rejects.toThrow('HTTP 500');
    await expect(
      fetchHomeAssistant({ url: 'http://ha', token: 't', entityId: 'weather.x' }, PLACE, {
        timeoutMs: 100,
        fetchImpl: async () => reply({}, 401),
      }),
    ).rejects.toThrow('HTTP 401');
  });
});

describe('cache decoding', () => {
  test('a corrupt or foreign file is a miss', async () => {
    const store = new MemoryStore();
    await store.write(bundleKey(PLACE), '{not json');
    expect(await readBundle(store, bundleKey(PLACE))).toBeNull();
    await store.write(bundleKey(PLACE), JSON.stringify({ schema: 99, bundle: {} }));
    expect(await readBundle(store, bundleKey(PLACE))).toBeNull();
    await store.write(climateKey(PLACE), JSON.stringify({ schema: 1, climate: { humidity: {} } }));
    expect(await readClimate(store, climateKey(PLACE))).toBeNull();
  });

  test('a bundle survives a round trip', async () => {
    const store = new MemoryStore();
    const b = bundle({ now: NOW, current: { feels: 3, gust: 8 } });
    await writeBundle(store, bundleKey(PLACE), b);
    expect(await readBundle(store, bundleKey(PLACE))).toEqual(b);
  });
});

describe('climate norms', () => {
  test('mean and sd per local month and hour from archive hours', () => {
    const times: number[] = [];
    const temps: number[] = [];
    const hums: number[] = [];
    // three November days at 14:00 local with humidity 60/70/80
    ['2026-11-02', '2026-11-03', '2026-11-04'].forEach((date, i) => {
      times.push(S(at(date, 14)));
      temps.push(10 + i);
      hums.push(60 + i * 10);
    });
    const climate = parseArchive(
      { hourly: { time: times, temperature_2m: temps, relative_humidity_2m: hums } },
      PLACE,
      [2015, 2024],
      NOW,
    );
    expect(climate.humidity.mean[10]?.[14]).toBe(70);
    expect(climate.humidity.sd[10]?.[14]).toBe(10);
    expect(climate.temp.mean[10]?.[14]).toBe(11);
    expect(climate.humidity.mean[0]?.[0]).toBe(0);
  });

  test('refresh writes the cache and load reads it without the network; the window is ten full years', async () => {
    const store = new MemoryStore();
    let requested = '';
    const fetcher: Fetcher = async (url) => {
      requested = url;
      return reply({
        hourly: {
          time: [S(at('2026-11-02', 14))],
          temperature_2m: [10],
          relative_humidity_2m: [70],
        },
      });
    };
    expect(await loadClimate(store, PLACE)).toBeNull();
    await refreshClimate(store, PLACE, { timeoutMs: 100, now: NOW, fetchImpl: fetcher });
    expect(requested).toContain('start_date=2016-01-01');
    expect(requested).toContain('end_date=2025-12-31');
    expect((await loadClimate(store, PLACE))?.years).toEqual([2016, 2025]);
    expect(archiveYears(NOW)).toEqual([2016, 2025]);
  });
});
