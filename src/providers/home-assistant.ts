/**
 * @file  Home Assistant provider: a `weather.*` entity plus `weather.get_forecasts`.
 *
 * Accessed via: `getWeather()` (service.ts) as the primary source when configured. Works through
 * the HA REST API directly or through a caching proxy with the same API (e.g. ha-cli).
 *
 * Assumptions: forecast hours start at their `datetime` (HA convention, same as this library).
 * HA gives no apparent temperature or gusts for most integrations; `feels` stays unset and the
 * temperature decision computes it from temperature/humidity/wind. Units come from the entity's
 * own `*_unit` attributes and are converted at this boundary.
 */

import { conditionFromHomeAssistant } from '../conditions';
import { localDate } from '../time';
import type { Bundle, Condition, Day, Moment, Place } from '../types';
import { fahrenheitToCelsius, kmhToMs, knotsToMs, mphToMs } from '../units';
import {
  asArray,
  asNumber,
  asString,
  isRecord,
  type JsonRecord,
  type JsonValue,
  ProviderError,
  requestJson,
  type RequestOptions,
} from './json';

export interface HaConnection {
  /** Base URL without trailing slash, e.g. http://homeassistant.local:8123 */
  url: string;
  token: string;
  /** `weather.*` entity id; discovered from /api/states when omitted. */
  entityId?: string;
}

interface Units {
  temperature: 'C' | 'F';
  wind: (value: number) => number;
  precip: (value: number) => number;
}

function windConverter(unit: string | undefined): (value: number) => number {
  switch (unit) {
    case 'm/s':
      return (v) => v;
    case 'mph':
      return mphToMs;
    case 'kn':
      return knotsToMs;
    case 'ft/s':
      return (v) => v * 0.3048;
    default:
      return kmhToMs;
  }
}

function unitsOf(attributes: JsonRecord): Units {
  const temperature = asString(attributes.temperature_unit) === '°F' ? 'F' : 'C';
  const precipUnit = asString(attributes.precipitation_unit);
  return {
    temperature,
    wind: windConverter(asString(attributes.wind_speed_unit)),
    precip: precipUnit === 'in' ? (v) => v * 25.4 : (v) => v,
  };
}

const toC = (units: Units, value: number): number =>
  units.temperature === 'F' ? fahrenheitToCelsius(value) : value;

function conditionOf(value: JsonValue | undefined): Condition {
  return conditionFromHomeAssistant(asString(value) ?? '') ?? 'cloudy';
}

function forecastMoment(entry: JsonRecord, units: Units): Moment | null {
  const time = Date.parse(asString(entry.datetime) ?? '');
  const temp = asNumber(entry.temperature);
  if (Number.isNaN(time) || temp === undefined) return null;
  const moment: Moment = { time, temp: toC(units, temp), condition: conditionOf(entry.condition) };
  const humidity = asNumber(entry.humidity);
  const wind = asNumber(entry.wind_speed);
  const gust = asNumber(entry.wind_gust_speed);
  const precip = asNumber(entry.precipitation);
  const prob = asNumber(entry.precipitation_probability);
  if (humidity !== undefined) moment.humidity = humidity;
  if (wind !== undefined) moment.wind = units.wind(wind);
  if (gust !== undefined) moment.gust = units.wind(gust);
  if (precip !== undefined) moment.precip = units.precip(precip);
  if (prob !== undefined) moment.precipProb = prob;
  return moment;
}

function forecastDay(entry: JsonRecord, units: Units, place: Place): Day | null {
  const time = Date.parse(asString(entry.datetime) ?? '');
  const max = asNumber(entry.temperature);
  if (Number.isNaN(time) || max === undefined) return null;
  const min = asNumber(entry.templow) ?? max;
  const day: Day = {
    date: localDate(time, place.tz),
    min: toC(units, min),
    max: toC(units, max),
    condition: conditionOf(entry.condition),
    precip: units.precip(asNumber(entry.precipitation) ?? 0),
    snow: 0,
  };
  const prob = asNumber(entry.precipitation_probability);
  const wind = asNumber(entry.wind_speed);
  const gust = asNumber(entry.wind_gust_speed);
  if (prob !== undefined) day.precipProb = prob;
  if (wind !== undefined) day.windMax = units.wind(wind);
  if (gust !== undefined) day.gustMax = units.wind(gust);
  return day;
}

function forecastList(response: JsonValue, entityId: string): JsonRecord[] {
  if (!isRecord(response) || !isRecord(response.service_response)) return [];
  const forEntity = response.service_response[entityId];
  if (!isRecord(forEntity)) return [];
  return asArray(forEntity.forecast).filter(isRecord);
}

export function parseHomeAssistant(
  state: JsonValue,
  hourlyResponse: JsonValue | null,
  dailyResponse: JsonValue | null,
  place: Place,
  fetchedAt: number,
): Bundle {
  if (!isRecord(state)) throw new ProviderError('home-assistant', 'unexpected state payload');
  const entityId = asString(state.entity_id) ?? '';
  const attributes = state.attributes;
  const condition = conditionFromHomeAssistant(asString(state.state) ?? '');
  if (!isRecord(attributes) || condition === null) {
    throw new ProviderError('home-assistant', `weather entity ${entityId} is unavailable`);
  }
  const units = unitsOf(attributes);
  const temp = asNumber(attributes.temperature);
  if (temp === undefined) throw new ProviderError('home-assistant', 'entity has no temperature');
  const updated = Date.parse(asString(state.last_updated) ?? '');
  const current: Moment = {
    time: Number.isNaN(updated) ? fetchedAt : updated,
    temp: toC(units, temp),
    condition,
  };
  const humidity = asNumber(attributes.humidity);
  const wind = asNumber(attributes.wind_speed);
  const gust = asNumber(attributes.wind_gust_speed);
  const feels = asNumber(attributes.apparent_temperature);
  if (humidity !== undefined) current.humidity = humidity;
  if (wind !== undefined) current.wind = units.wind(wind);
  if (gust !== undefined) current.gust = units.wind(gust);
  if (feels !== undefined) current.feels = toC(units, feels);

  const hourly = (hourlyResponse ? forecastList(hourlyResponse, entityId) : [])
    .map((entry) => forecastMoment(entry, units))
    .filter((m): m is Moment => m !== null);
  const daily = (dailyResponse ? forecastList(dailyResponse, entityId) : [])
    .map((entry) => forecastDay(entry, units, place))
    .filter((d): d is Day => d !== null);
  return { place, fetchedAt, current, hourly, daily, sources: ['home-assistant'] };
}

function authHeaders(connection: HaConnection): Record<string, string> {
  return { authorization: `Bearer ${connection.token}` };
}

/** Home coordinates and zone from HA's own configuration. */
export async function fetchHaPlace(
  connection: HaConnection,
  options: RequestOptions,
): Promise<Place> {
  const config = await requestJson('home-assistant', `${connection.url}/api/config`, {
    ...options,
    headers: authHeaders(connection),
  });
  if (isRecord(config)) {
    const lat = asNumber(config.latitude);
    const lon = asNumber(config.longitude);
    const tz = asString(config.time_zone);
    if (lat !== undefined && lon !== undefined && tz !== undefined) return { lat, lon, tz };
  }
  throw new ProviderError('home-assistant', '/api/config has no coordinates');
}

async function discoverEntity(connection: HaConnection, options: RequestOptions): Promise<string> {
  const states = await requestJson('home-assistant', `${connection.url}/api/states`, {
    ...options,
    headers: authHeaders(connection),
  });
  for (const state of asArray(states)) {
    const id = isRecord(state) ? asString(state.entity_id) : undefined;
    if (id?.startsWith('weather.')) return id;
  }
  throw new ProviderError('home-assistant', 'no weather.* entity found');
}

export async function fetchHomeAssistant(
  connection: HaConnection,
  place: Place,
  options: RequestOptions,
  fetchedAt: number = Date.now(),
): Promise<Bundle> {
  const headers = authHeaders(connection);
  const entityId = connection.entityId ?? (await discoverEntity(connection, options));
  const forecast = (type: 'hourly' | 'daily'): Promise<JsonValue | null> =>
    requestJson(
      'home-assistant',
      `${connection.url}/api/services/weather/get_forecasts?return_response`,
      { ...options, headers, body: { entity_id: entityId, type } },
    ).catch(() => null);
  const [state, hourly, daily] = await Promise.all([
    requestJson('home-assistant', `${connection.url}/api/states/${entityId}`, {
      ...options,
      headers,
    }),
    forecast('hourly'),
    forecast('daily'),
  ]);
  return parseHomeAssistant(state, hourly, daily, place, fetchedAt);
}
