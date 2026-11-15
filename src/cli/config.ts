/**
 * @file  CLI configuration: where the place, Home Assistant and the cache directory come from.
 *
 * Accessed via: src/cli/main.ts only (the library takes explicit options).
 *
 * Assumptions: order is flags → environment → config file → Home Assistant's own /api/config.
 * The Home Assistant token is read from the environment only; it is never written to the
 * config file or the cache.
 */

import { readFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';

import { asNumber, asString, isRecord, type JsonValue } from '../providers/json';
import { fetchHaPlace, type HaConnection } from '../providers/home-assistant';
import type { Place } from '../types';

export interface FileConfig {
  lat?: number;
  lon?: number;
  tz?: string;
  haUrl?: string;
  haEntity?: string;
}

export function configDir(env: Record<string, string | undefined>): string {
  return join(env.XDG_CONFIG_HOME ?? join(homedir(), '.config'), 'weather-cli');
}

export function cacheDir(env: Record<string, string | undefined>): string {
  return (
    env.WEATHER_CACHE_DIR ?? join(env.XDG_CACHE_HOME ?? join(homedir(), '.cache'), 'weather-cli')
  );
}

export async function readFileConfig(env: Record<string, string | undefined>): Promise<FileConfig> {
  try {
    // SAFETY: JSON.parse yields exactly the JsonValue union; fields are narrowed below.
    const json: JsonValue = JSON.parse(await readFile(join(configDir(env), 'config.json'), 'utf8'));
    if (!isRecord(json)) return {};
    const config: FileConfig = {};
    const lat = asNumber(json.lat);
    const lon = asNumber(json.lon);
    const tz = asString(json.tz);
    const haUrl = asString(json.haUrl);
    const haEntity = asString(json.haEntity);
    if (lat !== undefined) config.lat = lat;
    if (lon !== undefined) config.lon = lon;
    if (tz !== undefined) config.tz = tz;
    if (haUrl !== undefined) config.haUrl = haUrl;
    if (haEntity !== undefined) config.haEntity = haEntity;
    return config;
  } catch {
    return {};
  }
}

export interface PlaceFlags {
  lat?: string | undefined;
  lon?: string | undefined;
  tz?: string | undefined;
}

export function haConnection(
  env: Record<string, string | undefined>,
  file: FileConfig,
): HaConnection | undefined {
  const url = env.WEATHER_HA_URL ?? env.HA_URL ?? file.haUrl;
  const token = env.WEATHER_HA_TOKEN ?? env.HA_TOKEN;
  if (!url || !token) return undefined;
  const entityId = env.WEATHER_HA_ENTITY ?? file.haEntity;
  return { url: url.replace(/\/+$/, ''), token, ...(entityId ? { entityId } : {}) };
}

export async function resolvePlace(
  flags: PlaceFlags,
  env: Record<string, string | undefined>,
  file: FileConfig,
  ha: HaConnection | undefined,
  timeoutMs: number,
): Promise<Place> {
  const lat = flags.lat ?? env.WEATHER_LAT;
  const lon = flags.lon ?? env.WEATHER_LON;
  const tz = flags.tz ?? env.WEATHER_TZ ?? file.tz;
  const latNumber = lat !== undefined ? Number(lat) : file.lat;
  const lonNumber = lon !== undefined ? Number(lon) : file.lon;
  if (
    latNumber !== undefined &&
    lonNumber !== undefined &&
    Number.isFinite(latNumber) &&
    Number.isFinite(lonNumber)
  ) {
    return {
      lat: latNumber,
      lon: lonNumber,
      tz: tz ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
    };
  }
  if (ha) return fetchHaPlace(ha, { timeoutMs });
  throw new Error(
    'no location: pass --lat/--lon, set WEATHER_LAT/WEATHER_LON, write ~/.config/weather-cli/config.json, or configure Home Assistant (WEATHER_HA_URL + WEATHER_HA_TOKEN)',
  );
}
