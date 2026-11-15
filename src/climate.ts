/**
 * @file  Climate norms: per local month and hour mean/sd of humidity and temperature, built
 * from Open-Meteo's ERA5 archive and cached.
 *
 * Accessed via: `loadClimate()` on the hot path (cache read only, never the network) and
 * `refreshClimate()` from `weather climate update` or a background task of the host.
 *
 * Assumptions: the archive request is ~2.7 MB and takes seconds, so it NEVER runs inside a voice
 * answer; the decision functions stay silent about humidity/anomaly until norms exist. The
 * window is the ten complete calendar years before the current one.
 */

import { type Store, climateKey, readClimate, writeClimate } from './cache';
import { localParts } from './time';
import {
  asArray,
  asNumber,
  type Fetcher,
  isRecord,
  type JsonValue,
  ProviderError,
  requestJson,
} from './providers/json';
import type { Climate, ClimateSeries, Place } from './types';

const ARCHIVE = 'https://archive-api.open-meteo.com/v1/archive';
/** Norms older than this are rebuilt. */
export const CLIMATE_MAX_AGE_MS = 180 * 86_400_000;
const YEARS = 10;

class Accumulator {
  private readonly count: number[][] = grid(0);
  private readonly mean: number[][] = grid(0);
  private readonly m2: number[][] = grid(0);

  /** Welford's online mean/variance per [month][hour]. */
  add(month: number, hour: number, value: number): void {
    const row = month - 1;
    const n = (this.count[row]?.[hour] ?? 0) + 1;
    const previousMean = this.mean[row]?.[hour] ?? 0;
    const delta = value - previousMean;
    const nextMean = previousMean + delta / n;
    set(this.count, row, hour, n);
    set(this.mean, row, hour, nextMean);
    set(this.m2, row, hour, (this.m2[row]?.[hour] ?? 0) + delta * (value - nextMean));
  }

  series(): ClimateSeries {
    return {
      mean: this.mean.map((row) => row.map((v) => round(v))),
      sd: this.m2.map((row, r) =>
        row.map((sum, h) => {
          const n = this.count[r]?.[h] ?? 0;
          return n > 1 ? round(Math.sqrt(sum / (n - 1))) : 0;
        }),
      ),
    };
  }
}

function grid(fill: number): number[][] {
  return Array.from({ length: 12 }, () => Array.from({ length: 24 }, () => fill));
}

function set(target: number[][], row: number, col: number, value: number): void {
  const line = target[row];
  if (line) line[col] = value;
}

const round = (value: number): number => Math.round(value * 100) / 100;

export function archiveUrl(place: Place, years: [number, number]): string {
  const query = new URLSearchParams({
    latitude: String(place.lat),
    longitude: String(place.lon),
    start_date: `${years[0]}-01-01`,
    end_date: `${years[1]}-12-31`,
    hourly: 'temperature_2m,relative_humidity_2m',
    timezone: place.tz,
    timeformat: 'unixtime',
  });
  return `${ARCHIVE}?${query.toString()}`;
}

export function parseArchive(
  json: JsonValue,
  place: Place,
  years: [number, number],
  builtAt: number,
): Climate {
  if (!isRecord(json) || !isRecord(json.hourly))
    throw new ProviderError('open-meteo-archive', 'unexpected payload');
  const times = asArray(json.hourly.time).map((v) => asNumber(v));
  const temps = asArray(json.hourly.temperature_2m).map((v) => asNumber(v));
  const humidities = asArray(json.hourly.relative_humidity_2m).map((v) => asNumber(v));
  if (times.length === 0) throw new ProviderError('open-meteo-archive', 'empty archive');
  const temp = new Accumulator();
  const humidity = new Accumulator();
  times.forEach((time, i) => {
    if (time === undefined) return;
    const local = localParts(time * 1000, place.tz);
    const t = temps[i];
    const h = humidities[i];
    if (t !== undefined) temp.add(local.month, local.hour, t);
    if (h !== undefined) humidity.add(local.month, local.hour, h);
  });
  return { place, years, humidity: humidity.series(), temp: temp.series(), builtAt };
}

/** The ten complete years before the one containing `now`. */
export function archiveYears(now: number): [number, number] {
  const year = new Date(now).getUTCFullYear();
  return [year - YEARS, year - 1];
}

export async function refreshClimate(
  store: Store,
  place: Place,
  options: { timeoutMs: number; now?: number; fetchImpl?: Fetcher },
): Promise<Climate> {
  const now = options.now ?? Date.now();
  const years = archiveYears(now);
  const json = await requestJson('open-meteo-archive', archiveUrl(place, years), {
    timeoutMs: options.timeoutMs,
    ...(options.fetchImpl ? { fetchImpl: options.fetchImpl } : {}),
  });
  const climate = parseArchive(json, place, years, now);
  await writeClimate(store, climateKey(place), climate);
  return climate;
}

/** Cache read only: the hot path never waits for the network. */
export async function loadClimate(store: Store, place: Place): Promise<Climate | null> {
  return readClimate(store, climateKey(place));
}

export function climateIsStale(climate: Climate | null, now: number): boolean {
  return climate === null || now - climate.builtAt > CLIMATE_MAX_AGE_MS;
}
