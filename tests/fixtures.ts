/**
 * @file  Builders for deterministic weather bundles used across the test suite.
 *
 * Accessed via: every decision/compose test. Times are wall-clock in Europe/Belgrade so a test
 * reads like a real moment («21:00 on 9 November»).
 */

import { DEFAULT_THRESHOLDS } from '../src/thresholds';
import { addDays, localDate, zonedTimeToMs } from '../src/time';
import type { Bundle, Climate, ClimateSeries, Day, Moment, Place } from '../src/types';

export const TZ = 'Europe/Belgrade';
export const PLACE: Place = { lat: 44.8, lon: 20.48, tz: TZ };
export const T = DEFAULT_THRESHOLDS;
export const HOUR = 3_600_000;

export function at(date: string, hour: number, minute = 0): number {
  return zonedTimeToMs(TZ, date, hour, minute);
}

export function moment(time: number, over: Partial<Moment> = {}): Moment {
  return { time, temp: 10, condition: 'cloudy', ...over };
}

/** Forecast hours from one hour before `now` (floored to the hour) for 48 hours. */
export function hours(
  now: number,
  shape: (index: number, start: number) => Partial<Moment> = () => ({}),
): Moment[] {
  const first = Math.floor(now / HOUR) * HOUR - HOUR;
  return Array.from({ length: 49 }, (_, i) => {
    const start = first + i * HOUR;
    return moment(start, { precip: 0, ...shape(i, start) });
  });
}

export function day(date: string, over: Partial<Day> = {}): Day {
  return { date, min: 5, max: 12, condition: 'cloudy', precip: 0, snow: 0, ...over };
}

/** Eight days from the local date of `now`; `shape(offset)` overrides each. */
export function days(now: number, shape: (offset: number) => Partial<Day> = () => ({})): Day[] {
  const today = localDate(now, TZ);
  return Array.from({ length: 8 }, (_, offset) => day(addDays(today, offset), shape(offset)));
}

export interface BundleSpec {
  now: number;
  current?: Partial<Moment>;
  hour?: (index: number, start: number) => Partial<Moment>;
  day?: ((offset: number) => Partial<Day>) | undefined;
}

export function bundle(spec: BundleSpec): Bundle {
  return {
    place: PLACE,
    fetchedAt: spec.now,
    current: moment(spec.now, { precip: 0, ...spec.current }),
    // Forecast hours hold the current air temperature unless a test shapes them otherwise.
    hourly: hours(spec.now, (index, start) => ({
      temp: spec.current?.temp ?? 10,
      ...spec.hour?.(index, start),
    })),
    daily: days(spec.now, spec.day),
    sources: ['open-meteo'],
  };
}

function flat(value: number): number[][] {
  return Array.from({ length: 12 }, () => Array.from({ length: 24 }, () => value));
}

export function series(mean: number, sd: number): ClimateSeries {
  return { mean: flat(mean), sd: flat(sd) };
}

/** Norms with the same mean/sd for every month and hour. */
export function climate(
  humidityMean: number,
  humiditySd: number,
  tempMean = 10,
  tempSd = 4,
): Climate {
  return {
    place: PLACE,
    years: [2015, 2024],
    humidity: series(humidityMean, humiditySd),
    temp: series(tempMean, tempSd),
    builtAt: 0,
  };
}
