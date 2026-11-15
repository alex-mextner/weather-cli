/**
 * @file  `getWeather()`: providers + cache + deadline in one call.
 *
 * Accessed via: the CLI and the voice host before `say()`.
 *
 * Assumptions: a voice turn has ~1.5 s of patience, so every provider shares one hard timeout
 * (default 1.2 s) and runs concurrently. A fresh cache hit (default 10 min) does no network at
 * all; when every provider fails the last cached bundle is returned (up to 12 h old) and
 * `source` says `stale`, so the caller can still answer instead of going silent.
 */

import { type Store, bundleKey, readBundle, writeBundle } from './cache';
import { type HaConnection, fetchHomeAssistant } from './providers/home-assistant';
import type { Fetcher } from './providers/json';
import { mergeBundles } from './providers/merge';
import { fetchOpenMeteo } from './providers/open-meteo';
import type { Bundle, Place } from './types';

export interface WeatherOptions {
  place: Place;
  /** Home Assistant is the primary source when given. */
  ha?: HaConnection;
  /** Open-Meteo is on by default; it brings feels-like, gusts and probabilities. */
  openMeteo?: boolean;
  store?: Store;
  /** Hard deadline per provider, ms. */
  timeoutMs?: number;
  /** A cached bundle younger than this is served without network, ms. */
  freshMs?: number;
  /** Offline fallback: the last cached bundle is usable up to this age, ms. */
  maxStaleMs?: number;
  now?: number;
  fetchImpl?: Fetcher;
}

export type WeatherOrigin = 'cache' | 'live' | 'stale';

export interface WeatherResult {
  bundle: Bundle;
  origin: WeatherOrigin;
  /** One line per provider that failed. */
  errors: string[];
}

const DEFAULT_TIMEOUT_MS = 1200;
const DEFAULT_FRESH_MS = 10 * 60_000;
const DEFAULT_MAX_STALE_MS = 12 * 3_600_000;

export async function getWeather(options: WeatherOptions): Promise<WeatherResult> {
  const now = options.now ?? Date.now();
  const key = bundleKey(options.place);
  const cached = options.store ? await readBundle(options.store, key) : null;
  const freshMs = options.freshMs ?? DEFAULT_FRESH_MS;
  if (cached && now - cached.fetchedAt < freshMs) {
    return { bundle: cached, origin: 'cache', errors: [] };
  }

  const request = {
    timeoutMs: options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    ...(options.fetchImpl ? { fetchImpl: options.fetchImpl } : {}),
  };
  const [home, meteo] = await Promise.allSettled([
    options.ha
      ? fetchHomeAssistant(options.ha, options.place, request, now)
      : Promise.reject(new Error('off')),
    options.openMeteo === false
      ? Promise.reject(new Error('off'))
      : fetchOpenMeteo(options.place, request, now),
  ]);

  const errors: string[] = [];
  const failure = (name: string, result: PromiseRejectedResult): void => {
    const reason = result.reason instanceof Error ? result.reason.message : String(result.reason);
    if (reason !== 'off') errors.push(`${name}: ${reason}`);
  };
  if (home.status === 'rejected') failure('home-assistant', home);
  if (meteo.status === 'rejected') failure('open-meteo', meteo);

  const primary = home.status === 'fulfilled' ? home.value : null;
  const secondary = meteo.status === 'fulfilled' ? meteo.value : null;
  if (primary === null && secondary === null) {
    const maxStale = options.maxStaleMs ?? DEFAULT_MAX_STALE_MS;
    if (cached && now - cached.fetchedAt <= maxStale)
      return { bundle: cached, origin: 'stale', errors };
    throw new Error(`no weather data: ${errors.join('; ') || 'no provider configured'}`);
  }

  const bundle = mergeBundles(primary, secondary, now);
  if (options.store) await writeBundle(options.store, key, bundle);
  return { bundle, origin: 'live', errors };
}
