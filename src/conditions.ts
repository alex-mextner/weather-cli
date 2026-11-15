/**
 * @file  Sky/precipitation vocabulary: provider code mappings and classification helpers.
 *
 * Accessed via: providers (WMO codes, Home Assistant condition strings) and every decision
 * that asks "is it wet / snowing / thundering".
 *
 * Assumptions: Home Assistant's `windy`, `windy-variant` and `exceptional` carry no sky
 * information, so they map to `partly-cloudy`, the neutral choice.
 */

import type { Condition, Moment, PrecipKind } from './types';

const WMO: Record<number, Condition> = {
  0: 'clear',
  1: 'clear',
  2: 'partly-cloudy',
  3: 'cloudy',
  45: 'fog',
  48: 'fog',
  51: 'drizzle',
  53: 'drizzle',
  55: 'drizzle',
  56: 'sleet',
  57: 'sleet',
  61: 'rain',
  63: 'rain',
  65: 'heavy-rain',
  66: 'sleet',
  67: 'sleet',
  71: 'snow',
  73: 'snow',
  75: 'heavy-snow',
  77: 'snow',
  80: 'rain',
  81: 'rain',
  82: 'heavy-rain',
  85: 'snow',
  86: 'heavy-snow',
  95: 'thunderstorm',
  96: 'thunderstorm',
  99: 'thunderstorm',
};

export function conditionFromWmo(code: number): Condition {
  return WMO[code] ?? 'cloudy';
}

const HOME_ASSISTANT: Record<string, Condition> = {
  'clear-night': 'clear',
  sunny: 'clear',
  partlycloudy: 'partly-cloudy',
  cloudy: 'cloudy',
  fog: 'fog',
  hail: 'hail',
  lightning: 'thunderstorm',
  'lightning-rainy': 'thunderstorm',
  pouring: 'heavy-rain',
  rainy: 'rain',
  snowy: 'snow',
  'snowy-rainy': 'sleet',
  windy: 'partly-cloudy',
  'windy-variant': 'partly-cloudy',
  exceptional: 'partly-cloudy',
};

export function conditionFromHomeAssistant(state: string): Condition | null {
  return HOME_ASSISTANT[state] ?? null;
}

export function precipKindOf(condition: Condition): PrecipKind {
  switch (condition) {
    case 'drizzle':
    case 'rain':
    case 'heavy-rain':
    case 'thunderstorm':
    case 'hail':
      return 'rain';
    case 'snow':
    case 'heavy-snow':
      return 'snow';
    case 'sleet':
      return 'mixed';
    default:
      return 'none';
  }
}

/** Minimum hourly amount that counts as "it is raining" (mm). */
export const WET_MM = 0.2;
/** Below this probability a forecast hour is not called wet unless the amount is clear. */
const WET_PROBABILITY = 40;

/**
 * Is precipitation falling in this moment? A current observation with a precipitating
 * condition counts even without an amount (Home Assistant gives none); a forecast hour needs an
 * amount and a not-tiny probability.
 */
export function isWet(moment: Moment): boolean {
  const kind = precipKindOf(moment.condition);
  const mm = moment.precip;
  if (mm === undefined) return kind !== 'none';
  if (mm < WET_MM) return false;
  if (moment.precipProb !== undefined && moment.precipProb < WET_PROBABILITY && mm < 1)
    return false;
  return true;
}

/** What falls when it is wet. */
export type WetKind = Exclude<PrecipKind, 'none'>;

/** Wet in kind: what falls (rain/snow/mixed), judged by the moment's condition, else by temperature. */
export function wetKind(moment: Moment, temp = moment.temp): WetKind {
  const fromCondition = precipKindOf(moment.condition);
  if (fromCondition !== 'none') return fromCondition;
  // The sky code says "cloudy" but the amount says wet: decide by temperature.
  if (temp <= 0) return 'snow';
  if (temp <= 2) return 'mixed';
  return 'rain';
}

const ALL_CONDITIONS: readonly Condition[] = [
  'clear',
  'partly-cloudy',
  'cloudy',
  'fog',
  'drizzle',
  'rain',
  'heavy-rain',
  'thunderstorm',
  'snow',
  'heavy-snow',
  'sleet',
  'hail',
];

export function conditionFromString(value: string | undefined): Condition | null {
  return ALL_CONDITIONS.find((condition) => condition === value) ?? null;
}
