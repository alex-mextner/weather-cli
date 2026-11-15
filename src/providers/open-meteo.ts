/**
 * @file  Open-Meteo forecast provider (free, no key): current, 48 forecast hours, 8 days.
 *
 * Accessed via: `getWeather()` (service.ts) as the secondary/enrichment source and the only
 * source that brings apparent temperature, gusts and precipitation probability.
 *
 * Assumptions: requested with `timeformat=unixtime` and `wind_speed_unit=ms`, so no string
 * date parsing and no unit conversion. Open-Meteo labels hourly precipitation with the END of
 * the hour; this provider re-labels it to the hour's START (the library-wide convention), which
 * is why the last returned hour is dropped (its successor is unknown).
 */

import { conditionFromWmo, WET_MM } from '../conditions';
import { localDate } from '../time';
import type { Bundle, Day, Moment, Place } from '../types';
import {
  asArray,
  asNumber,
  isRecord,
  type JsonValue,
  ProviderError,
  requestJson,
  type RequestOptions,
} from './json';

const BASE = 'https://api.open-meteo.com/v1/forecast';

const CURRENT_FIELDS = [
  'temperature_2m',
  'relative_humidity_2m',
  'apparent_temperature',
  'precipitation',
  'weather_code',
  'wind_speed_10m',
  'wind_gusts_10m',
];
const HOURLY_FIELDS = [...CURRENT_FIELDS, 'precipitation_probability'];
const DAILY_FIELDS = [
  'weather_code',
  'temperature_2m_max',
  'temperature_2m_min',
  'precipitation_sum',
  'snowfall_sum',
  'precipitation_probability_max',
  'wind_speed_10m_max',
  'wind_gusts_10m_max',
];

export function openMeteoUrl(place: Place): string {
  const query = new URLSearchParams({
    latitude: String(place.lat),
    longitude: String(place.lon),
    current: CURRENT_FIELDS.join(','),
    hourly: HOURLY_FIELDS.join(','),
    daily: DAILY_FIELDS.join(','),
    timezone: place.tz,
    timeformat: 'unixtime',
    wind_speed_unit: 'ms',
    past_hours: '2',
    forecast_hours: '48',
    forecast_days: '8',
  });
  return `${BASE}?${query.toString()}`;
}

function column(block: JsonValue | undefined, name: string): (number | undefined)[] {
  return isRecord(block) ? asArray(block[name]).map((v) => asNumber(v)) : [];
}

function momentFields(
  moment: Moment,
  values: {
    feels: number | undefined;
    humidity: number | undefined;
    wind: number | undefined;
    gust: number | undefined;
    precip: number | undefined;
    prob: number | undefined;
  },
): Moment {
  if (values.feels !== undefined) moment.feels = values.feels;
  if (values.humidity !== undefined) moment.humidity = values.humidity;
  if (values.wind !== undefined) moment.wind = values.wind;
  if (values.gust !== undefined) moment.gust = values.gust;
  if (values.precip !== undefined) moment.precip = values.precip;
  if (values.prob !== undefined) moment.precipProb = values.prob;
  return moment;
}

export function parseOpenMeteo(json: JsonValue, place: Place, fetchedAt: number): Bundle {
  if (!isRecord(json)) throw new ProviderError('open-meteo', 'unexpected payload');
  const current = json.current;
  const hourly = json.hourly;
  const daily = json.daily;
  if (!isRecord(current)) throw new ProviderError('open-meteo', 'no current block');

  const currentTemp = asNumber(current.temperature_2m);
  const currentTime = asNumber(current.time);
  if (currentTemp === undefined || currentTime === undefined) {
    throw new ProviderError('open-meteo', 'current block has no temperature');
  }
  const now: Moment = momentFields(
    {
      time: currentTime * 1000,
      temp: currentTemp,
      condition: conditionFromWmo(asNumber(current.weather_code) ?? 3),
    },
    {
      feels: asNumber(current.apparent_temperature),
      humidity: asNumber(current.relative_humidity_2m),
      wind: asNumber(current.wind_speed_10m),
      gust: asNumber(current.wind_gusts_10m),
      precip: asNumber(current.precipitation),
      prob: undefined,
    },
  );

  const times = isRecord(hourly) ? asArray(hourly.time).map((v) => asNumber(v)) : [];
  const temp = column(hourly, 'temperature_2m');
  const feels = column(hourly, 'apparent_temperature');
  const humidity = column(hourly, 'relative_humidity_2m');
  const wind = column(hourly, 'wind_speed_10m');
  const gust = column(hourly, 'wind_gusts_10m');
  const precip = column(hourly, 'precipitation');
  const prob = column(hourly, 'precipitation_probability');
  const codes = column(hourly, 'weather_code');

  const hours: Moment[] = [];
  for (let i = 0; i < times.length - 1; i += 1) {
    const start = times[i];
    const t = temp[i];
    if (start === undefined || t === undefined) continue;
    // Precipitation and its sky code at the hour's END describe the hour that starts here.
    const amount = precip[i + 1];
    const wetCode = amount !== undefined && amount >= WET_MM ? codes[i + 1] : undefined;
    hours.push(
      momentFields(
        { time: start * 1000, temp: t, condition: conditionFromWmo(wetCode ?? codes[i] ?? 3) },
        {
          feels: feels[i],
          humidity: humidity[i],
          wind: wind[i],
          gust: gust[i],
          precip: amount,
          prob: prob[i + 1],
        },
      ),
    );
  }

  const dayTimes = isRecord(daily) ? asArray(daily.time).map((v) => asNumber(v)) : [];
  const dayCodes = column(daily, 'weather_code');
  const dayMax = column(daily, 'temperature_2m_max');
  const dayMin = column(daily, 'temperature_2m_min');
  const dayPrecip = column(daily, 'precipitation_sum');
  const daySnow = column(daily, 'snowfall_sum');
  const dayProb = column(daily, 'precipitation_probability_max');
  const dayWind = column(daily, 'wind_speed_10m_max');
  const dayGust = column(daily, 'wind_gusts_10m_max');
  const days: Day[] = [];
  dayTimes.forEach((time, i) => {
    const max = dayMax[i];
    const min = dayMin[i];
    if (time === undefined || max === undefined || min === undefined) return;
    const day: Day = {
      date: localDate(time * 1000, place.tz),
      min,
      max,
      condition: conditionFromWmo(dayCodes[i] ?? 3),
      precip: dayPrecip[i] ?? 0,
      snow: daySnow[i] ?? 0,
    };
    const p = dayProb[i];
    const w = dayWind[i];
    const g = dayGust[i];
    if (p !== undefined) day.precipProb = p;
    if (w !== undefined) day.windMax = w;
    if (g !== undefined) day.gustMax = g;
    days.push(day);
  });

  if (days.length === 0) throw new ProviderError('open-meteo', 'no daily forecast');
  return { place, fetchedAt, current: now, hourly: hours, daily: days, sources: ['open-meteo'] };
}

export async function fetchOpenMeteo(
  place: Place,
  options: RequestOptions,
  fetchedAt: number = Date.now(),
): Promise<Bundle> {
  const json = await requestJson('open-meteo', openMeteoUrl(place), options);
  return parseOpenMeteo(json, place, fetchedAt);
}
