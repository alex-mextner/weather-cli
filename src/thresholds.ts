/**
 * @file  Every tunable number of the decision functions, in one place.
 *
 * Accessed via: each decision takes `Thresholds`; `say()` and the CLI pass `DEFAULT_THRESHOLDS`
 * (overridable with `withThresholds`).
 *
 * Assumptions: speeds are m/s; temperature gaps are compared on WHOLE spoken degrees, so what
 * the listener hears is what the rule saw («7, ощущается как 1» is a 6° gap).
 */

export interface Thresholds {
  /** Feels-like is spoken only if it differs from the air temperature by at least this many degrees. */
  feelsGap: number;
  humidity: {
    /** z-score (against the month/hour norm) beyond which air is "notably" dry or damp. */
    sd: number;
    /** …and at least this many humidity points away from the norm. */
    gap: number;
    /** z-score for the stronger wording. */
    strongSd: number;
  };
  wind: {
    breezy: number;
    strong: number;
    storm: number;
    gustBreezy: number;
    gustStrong: number;
    gustStorm: number;
  };
  lookahead: {
    /** Minutes ahead that count as "about an hour". */
    windowMinutes: number;
    /** Air temperature change within the window that is worth saying, degrees. */
    tempJump: number;
  };
  /** From this local hour on, a "now" answer also carries tomorrow. */
  lateHour: number;
  tomorrow: {
    /** Tomorrow's daytime high differs from today's by at least this much → said. */
    shift: number;
  };
  week: {
    /** Daytime high moves this far from the next two days' level → warming/cooling. */
    shift: number;
    frost: number;
    hardFrost: number;
    heat: number;
    strongHeat: number;
    /** Daily precipitation sum (mm) that makes a "wet" day. */
    wetDayMm: number;
    /** Snowfall (cm) that makes a "snowy" day. */
    snowDayCm: number;
    /** Look at this many days after today. */
    horizonDays: number;
  };
  anomaly: {
    sd: number;
    gap: number;
  };
}

export const DEFAULT_THRESHOLDS: Thresholds = {
  feelsGap: 6,
  humidity: { sd: 1.0, gap: 10, strongSd: 1.8 },
  wind: { breezy: 6, strong: 9, storm: 13, gustBreezy: 10, gustStrong: 14, gustStorm: 19 },
  lookahead: { windowMinutes: 60, tempJump: 3 },
  lateHour: 21,
  tomorrow: { shift: 5 },
  week: {
    shift: 5,
    frost: 0,
    hardFrost: -10,
    heat: 30,
    strongHeat: 35,
    wetDayMm: 1,
    snowDayCm: 0.5,
    horizonDays: 7,
  },
  anomaly: { sd: 1.5, gap: 5 },
};

export type ThresholdOverrides = {
  [K in keyof Thresholds]?: Thresholds[K] extends number ? number : Partial<Thresholds[K]>;
};

export function withThresholds(overrides: ThresholdOverrides): Thresholds {
  const base = DEFAULT_THRESHOLDS;
  return {
    feelsGap: overrides.feelsGap ?? base.feelsGap,
    humidity: { ...base.humidity, ...overrides.humidity },
    wind: { ...base.wind, ...overrides.wind },
    lookahead: { ...base.lookahead, ...overrides.lookahead },
    lateHour: overrides.lateHour ?? base.lateHour,
    tomorrow: { ...base.tomorrow, ...overrides.tomorrow },
    week: { ...base.week, ...overrides.week },
    anomaly: { ...base.anomaly, ...overrides.anomaly },
  };
}
