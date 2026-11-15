/**
 * @file  Number conversions and the one physical formula the library owns.
 *
 * Accessed via: providers (unit boundary) and the temperature decision (rounding).
 *
 * Assumptions: spoken temperatures are whole degrees and `-0` never reaches a listener,
 * so every spoken value goes through `spoken()`.
 */

/** Whole degrees as spoken: rounded half away from zero, `-0` collapsed to `0`. */
export function spoken(value: number): number {
  const rounded = Math.sign(value) * Math.round(Math.abs(value));
  return rounded === 0 ? 0 : rounded;
}

export function kmhToMs(kmh: number): number {
  return kmh / 3.6;
}

export function mphToMs(mph: number): number {
  return mph * 0.44704;
}

export function knotsToMs(knots: number): number {
  return knots * 0.514444;
}

export function fahrenheitToCelsius(f: number): number {
  return ((f - 32) * 5) / 9;
}

/**
 * Apparent temperature (Steadman, as used by the Australian Bureau of Meteorology):
 * AT = T + 0.33·e − 0.70·ws − 4.00, e = rh/100 · 6.105 · exp(17.27·T / (237.7 + T)).
 * Used when a provider (Home Assistant) gives no apparent temperature of its own.
 * `wind` in m/s, `humidity` in %.
 */
export function apparentTemperature(temp: number, humidity: number, wind: number): number {
  const vapour = (humidity / 100) * 6.105 * Math.exp((17.27 * temp) / (237.7 + temp));
  return temp + 0.33 * vapour - 0.7 * wind - 4;
}
