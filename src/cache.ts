/**
 * @file  Key/value store seam for cached bundles and climate norms, plus the memory store.
 *
 * Accessed via: `getWeather()` and the climate helpers; the CLI and the voice host pass a
 * file-backed store (cache-fs.ts), tests pass the memory one.
 *
 * Assumptions: values are JSON text. A read that fails or returns garbage is a cache miss —
 * the cache is an optimisation and an offline fallback, never a source of truth. Decoders
 * rebuild every object field by field instead of asserting the parsed shape.
 */

import { conditionFromString } from './conditions';
import {
  asArray,
  asNumber,
  asString,
  isRecord,
  type JsonRecord,
  type JsonValue,
} from './providers/json';
import type { Bundle, Climate, ClimateSeries, Day, Moment, Place, SourceName } from './types';

export interface Store {
  read(key: string): Promise<string | null>;
  write(key: string, value: string): Promise<void>;
}

export class MemoryStore implements Store {
  readonly entries = new Map<string, string>();

  async read(key: string): Promise<string | null> {
    return this.entries.get(key) ?? null;
  }

  async write(key: string, value: string): Promise<void> {
    this.entries.set(key, value);
  }
}

const SCHEMA = 1;

export function bundleKey(place: Place): string {
  return `bundle-${place.lat.toFixed(2)}_${place.lon.toFixed(2)}.json`;
}

export function climateKey(place: Place): string {
  return `climate-${place.lat.toFixed(2)}_${place.lon.toFixed(2)}.json`;
}

function parse(text: string): JsonValue | null {
  try {
    // SAFETY: JSON.parse yields exactly the JsonValue union; decoders narrow it before use.
    const value: JsonValue = JSON.parse(text);
    return value;
  } catch {
    return null;
  }
}

function decodeMoment(value: JsonValue | undefined): Moment | null {
  if (!isRecord(value)) return null;
  const time = asNumber(value.time);
  const temp = asNumber(value.temp);
  const condition = conditionFromString(asString(value.condition));
  if (time === undefined || temp === undefined || condition === null) return null;
  const moment: Moment = { time, temp, condition };
  const feels = asNumber(value.feels);
  const humidity = asNumber(value.humidity);
  const wind = asNumber(value.wind);
  const gust = asNumber(value.gust);
  const precip = asNumber(value.precip);
  const prob = asNumber(value.precipProb);
  if (feels !== undefined) moment.feels = feels;
  if (humidity !== undefined) moment.humidity = humidity;
  if (wind !== undefined) moment.wind = wind;
  if (gust !== undefined) moment.gust = gust;
  if (precip !== undefined) moment.precip = precip;
  if (prob !== undefined) moment.precipProb = prob;
  return moment;
}

function decodeDay(value: JsonValue | undefined): Day | null {
  if (!isRecord(value)) return null;
  const date = asString(value.date);
  const min = asNumber(value.min);
  const max = asNumber(value.max);
  const condition = conditionFromString(asString(value.condition));
  if (date === undefined || min === undefined || max === undefined || condition === null)
    return null;
  const day: Day = {
    date,
    min,
    max,
    condition,
    precip: asNumber(value.precip) ?? 0,
    snow: asNumber(value.snow) ?? 0,
  };
  const prob = asNumber(value.precipProb);
  const wind = asNumber(value.windMax);
  const gust = asNumber(value.gustMax);
  if (prob !== undefined) day.precipProb = prob;
  if (wind !== undefined) day.windMax = wind;
  if (gust !== undefined) day.gustMax = gust;
  return day;
}

function decodePlace(value: JsonValue | undefined): Place | null {
  if (!isRecord(value)) return null;
  const lat = asNumber(value.lat);
  const lon = asNumber(value.lon);
  const tz = asString(value.tz);
  return lat === undefined || lon === undefined || tz === undefined ? null : { lat, lon, tz };
}

function decodeSources(value: JsonValue | undefined): SourceName[] {
  const names: SourceName[] = [];
  for (const entry of asArray(value)) {
    if (entry === 'home-assistant' || entry === 'open-meteo') names.push(entry);
  }
  return names;
}

function decodeList<T>(
  value: JsonValue | undefined,
  decode: (v: JsonValue) => T | null,
): T[] | null {
  const out: T[] = [];
  for (const entry of asArray(value)) {
    const decoded = decode(entry);
    if (decoded === null) return null;
    out.push(decoded);
  }
  return out;
}

function decodeBundle(value: JsonValue | undefined): Bundle | null {
  if (!isRecord(value)) return null;
  const place = decodePlace(value.place);
  const fetchedAt = asNumber(value.fetchedAt);
  const current = decodeMoment(value.current);
  const hourly = decodeList(value.hourly, decodeMoment);
  const daily = decodeList(value.daily, decodeDay);
  if (
    place === null ||
    fetchedAt === undefined ||
    current === null ||
    hourly === null ||
    daily === null
  ) {
    return null;
  }
  return { place, fetchedAt, current, hourly, daily, sources: decodeSources(value.sources) };
}

function unwrap(text: string | null): JsonRecord | null {
  if (text === null) return null;
  const json = parse(text);
  return isRecord(json) && json.schema === SCHEMA ? json : null;
}

export async function readBundle(store: Store, key: string): Promise<Bundle | null> {
  const text = await store.read(key).catch(() => null);
  return decodeBundle(unwrap(text)?.bundle);
}

export async function writeBundle(store: Store, key: string, bundle: Bundle): Promise<void> {
  await store.write(key, JSON.stringify({ schema: SCHEMA, bundle })).catch(() => undefined);
}

function decodeGrid(value: JsonValue | undefined): number[][] | null {
  const rows = decodeList(value, (row) => {
    const cells = asArray(row).flatMap((cell) => {
      const n = asNumber(cell);
      return n === undefined ? [] : [n];
    });
    return cells.length === 24 ? cells : null;
  });
  return rows !== null && rows.length === 12 ? rows : null;
}

function decodeSeries(value: JsonValue | undefined): ClimateSeries | null {
  if (!isRecord(value)) return null;
  const mean = decodeGrid(value.mean);
  const sd = decodeGrid(value.sd);
  return mean === null || sd === null ? null : { mean, sd };
}

export async function readClimate(store: Store, key: string): Promise<Climate | null> {
  const text = await store.read(key).catch(() => null);
  const climate = unwrap(text)?.climate;
  if (!isRecord(climate)) return null;
  const place = decodePlace(climate.place);
  const humidity = decodeSeries(climate.humidity);
  const temp = decodeSeries(climate.temp);
  const builtAt = asNumber(climate.builtAt);
  const years = asArray(climate.years).map((y) => asNumber(y));
  const first = years[0];
  const last = years[1];
  if (place === null || humidity === null || temp === null || builtAt === undefined) return null;
  if (first === undefined || last === undefined) return null;
  return { place, years: [first, last], humidity, temp, builtAt };
}

export async function writeClimate(store: Store, key: string, climate: Climate): Promise<void> {
  await store.write(key, JSON.stringify({ schema: SCHEMA, climate }));
}

/** A bundle from JSON text: either the cache wrapper or a bare bundle (CLI `--bundle` fixtures). */
export function decodeBundleText(text: string): Bundle | null {
  const json = parse(text);
  if (!isRecord(json)) return null;
  return decodeBundle(json.schema === SCHEMA ? json.bundle : json);
}
