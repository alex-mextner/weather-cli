/**
 * @file  Public API of weather-cli: decision functions, providers, cache, climate.
 *
 * Accessed via: embedding hosts (`import { getWeather, say } from 'weather-cli'`). The CLI lives
 * under src/cli and is not part of the library surface.
 */

export { type Store, MemoryStore, bundleKey, climateKey } from './cache';
export { FileStore } from './cache-fs';
export { CLIMATE_MAX_AGE_MS, climateIsStale, loadClimate, refreshClimate } from './climate';
export { type SayInput, say } from './compose';
export { describeAdvice, describeDress, describeUmbrella } from './decisions/advice';
export { describeTempAnomaly } from './decisions/anomaly';
export {
  type Insight,
  type InsightKind,
  describeInsight,
  describeTomorrow,
  describeWeekSummary,
  weekInsights,
} from './decisions/daily';
export { describeHumidity } from './decisions/humidity';
export { describeLookahead } from './decisions/lookahead';
export {
  describeFeels,
  describeSky,
  describeTemperature,
  feelsLike,
} from './decisions/temperature';
export { type WindLevel, describeWind, windLevel } from './decisions/wind';
export { type HaConnection, fetchHaPlace } from './providers/home-assistant';
export { mergeBundles } from './providers/merge';
export { type WeatherOptions, type WeatherOrigin, type WeatherResult, getWeather } from './service';
export { DEFAULT_THRESHOLDS, type Thresholds, withThresholds } from './thresholds';
export type {
  Bundle,
  Climate,
  Condition,
  Day,
  Decision,
  DecisionId,
  Mode,
  Moment,
  Place,
  Report,
  SourceName,
} from './types';
