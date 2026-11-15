/**
 * @file  Merge two provider bundles: the primary leads, the secondary fills what it lacks.
 *
 * Accessed via: `getWeather()` after both providers answered (or one failed).
 *
 * Assumptions: the primary (Home Assistant when configured) owns current values, hours and
 * days it can supply; the secondary only adds optional fields (gusts, probability, snowfall)
 * and horizon beyond the primary's last day. Apparent temperature from the secondary is applied
 * as an OFFSET to the primary's air temperature, so a 1.5° disagreement between sources never
 * produces a fake "feels-like" gap.
 */

import type { Bundle, Day, Moment } from '../types';

/** Primary current data older than this is replaced by the secondary's. */
const CURRENT_MAX_AGE_MS = 90 * 60_000;
/** Hours/days needed before the primary's series is trusted over the secondary's. */
const MIN_HOURS = 6;
const MIN_DAYS = 3;

function withFeelsOffset(target: Moment, source: Moment): void {
  if (target.feels === undefined && source.feels !== undefined) {
    target.feels = target.temp + (source.feels - source.temp);
  }
}

function fillMoment(target: Moment, source: Moment | undefined): Moment {
  if (!source) return target;
  withFeelsOffset(target, source);
  if (target.gust === undefined && source.gust !== undefined) target.gust = source.gust;
  if (target.precipProb === undefined && source.precipProb !== undefined) {
    target.precipProb = source.precipProb;
  }
  if (target.humidity === undefined && source.humidity !== undefined) {
    target.humidity = source.humidity;
  }
  if (target.wind === undefined && source.wind !== undefined) target.wind = source.wind;
  if (target.precip === undefined && source.precip !== undefined) target.precip = source.precip;
  return target;
}

function fillDay(target: Day, source: Day | undefined): Day {
  if (!source) return target;
  if (target.snow === 0 && source.snow > 0) target.snow = source.snow;
  if (target.precipProb === undefined && source.precipProb !== undefined) {
    target.precipProb = source.precipProb;
  }
  if (target.gustMax === undefined && source.gustMax !== undefined) target.gustMax = source.gustMax;
  if (target.windMax === undefined && source.windMax !== undefined) target.windMax = source.windMax;
  return target;
}

export function mergeBundles(
  primary: Bundle | null,
  secondary: Bundle | null,
  now: number,
): Bundle {
  if (primary === null && secondary === null) throw new Error('nothing to merge');
  if (primary === null) return secondary as Bundle;
  if (secondary === null) return primary;

  const useCurrent = now - primary.current.time <= CURRENT_MAX_AGE_MS;
  const current = useCurrent
    ? fillMoment({ ...primary.current }, secondary.current)
    : { ...secondary.current };

  const secondaryHours = new Map(secondary.hourly.map((m) => [m.time, m]));
  const hourly =
    primary.hourly.length >= MIN_HOURS
      ? primary.hourly.map((m) => fillMoment({ ...m }, secondaryHours.get(m.time)))
      : secondary.hourly;

  const secondaryDays = new Map(secondary.daily.map((d) => [d.date, d]));
  let daily: Day[];
  if (primary.daily.length >= MIN_DAYS) {
    const lastDate = primary.daily.at(-1)?.date ?? '';
    daily = [
      ...primary.daily.map((d) => fillDay({ ...d }, secondaryDays.get(d.date))),
      ...secondary.daily.filter((d) => d.date > lastDate),
    ];
  } else {
    daily = secondary.daily;
  }

  const sources = [...new Set([...primary.sources, ...secondary.sources])];
  return {
    place: primary.place,
    fetchedAt: Math.max(primary.fetchedAt, secondary.fetchedAt),
    current,
    hourly,
    daily,
    sources,
  };
}
