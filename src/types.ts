/**
 * @file  Domain types shared by providers, decision functions and the CLI.
 *
 * Units are fixed here and never converted downstream: °C, m/s, mm, %, hPa, epoch milliseconds.
 * Providers convert at the boundary (km/h, ISO strings, WMO codes); decision functions
 * therefore never need to guess a unit.
 */

export type Condition =
  | 'clear'
  | 'partly-cloudy'
  | 'cloudy'
  | 'fog'
  | 'drizzle'
  | 'rain'
  | 'heavy-rain'
  | 'thunderstorm'
  | 'snow'
  | 'heavy-snow'
  | 'sleet'
  | 'hail';

export type PrecipKind = 'none' | 'rain' | 'snow' | 'mixed';

export type SourceName = 'home-assistant' | 'open-meteo';

export interface Place {
  lat: number;
  lon: number;
  /** IANA zone, e.g. Europe/Belgrade. All "local" reasoning (month, evening, tomorrow) uses it. */
  tz: string;
}

/** One instant: the current observation or one forecast hour. */
export interface Moment {
  /** Epoch ms. For forecast hours: the START of the hour the values describe. */
  time: number;
  temp: number;
  /** Apparent temperature. Filled by the provider or computed from temp/humidity/wind. */
  feels?: number;
  humidity?: number;
  /** Sustained wind, m/s. */
  wind?: number;
  /** Gust, m/s. */
  gust?: number;
  /** Precipitation during the hour starting at `time`, mm. */
  precip?: number;
  /** Probability of precipitation, 0..100. */
  precipProb?: number;
  condition: Condition;
}

/** One local calendar day. */
export interface Day {
  /** Local date, YYYY-MM-DD. */
  date: string;
  min: number;
  max: number;
  condition: Condition;
  /** Precipitation sum, mm. */
  precip: number;
  /** Snowfall sum, cm. */
  snow: number;
  precipProb?: number;
  windMax?: number;
  gustMax?: number;
}

export interface Bundle {
  place: Place;
  /** Epoch ms the data was fetched (or last refreshed from a live provider). */
  fetchedAt: number;
  current: Moment;
  /** Forecast hours, ascending, starting at or just before `current.time`. */
  hourly: Moment[];
  /** Forecast days, ascending, first one is today (local). */
  daily: Day[];
  sources: SourceName[];
}

/** Monthly norms by local hour: index [month-1][hour]. */
export interface ClimateSeries {
  mean: number[][];
  sd: number[][];
}

export interface Climate {
  place: Place;
  /** Inclusive range of years the norms were computed from. */
  years: [number, number];
  humidity: ClimateSeries;
  temp: ClimateSeries;
  builtAt: number;
}

export type Mode = 'now' | 'tomorrow' | 'week' | 'umbrella' | 'dress';

export type DecisionId =
  | 'temperature'
  | 'feels'
  | 'sky'
  | 'humidity'
  | 'wind'
  | 'lookahead'
  | 'tomorrow'
  | 'insight'
  | 'temp-anomaly'
  | 'advice'
  | 'week-summary'
  | 'umbrella'
  | 'dress';

/**
 * The outcome of one decision function. `why` is always filled (also when nothing fired) so
 * `weather explain` can show the arithmetic behind silence.
 */
export interface Decision {
  id: DecisionId;
  fired: boolean;
  phrase: string | null;
  /** Higher wins the limited sentence budget. */
  weight: number;
  why: string;
}

export interface Report {
  mode: Mode;
  text: string;
  sentences: string[];
  decisions: Decision[];
  /** Phrases of fired decisions that did not fit the sentence budget. */
  dropped: DecisionId[];
  asOf: number;
  stale: boolean;
  sources: SourceName[];
}
