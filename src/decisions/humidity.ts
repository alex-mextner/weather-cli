/**
 * @file  Humidity as a sensation relative to the month's norm — never as a percentage.
 *
 * Accessed via: `say()` (now mode) and `explain`.
 *
 * Assumptions: the norm is the mean/sd of the same local month AND hour of day over the
 * archive years (night air is humid every night: comparing it to a daily mean would call every
 * evening damp). With no climate loaded the decision stays silent — it never invents a norm.
 * Damp air is not called out while it rains or fogs (redundant).
 */

import { monthGenitive } from '../ru';
import type { Thresholds } from '../thresholds';
import type { Climate, Decision } from '../types';
import { localParts } from '../time';

export interface HumidityInput {
  humidity: number | undefined;
  climate: Climate | null;
  time: number;
  tz: string;
  /** It is raining/snowing/foggy: "damp" would only repeat the obvious. */
  wetOrFog: boolean;
}

export function describeHumidity(input: HumidityInput, thresholds: Thresholds): Decision {
  const silent = (why: string): Decision => ({
    id: 'humidity',
    fired: false,
    phrase: null,
    weight: 40,
    why,
  });
  if (input.humidity === undefined) return silent('no humidity reading');
  if (input.climate === null)
    return silent('no climate norms cached (run `weather climate update`)');
  const local = localParts(input.time, input.tz);
  const mean = input.climate.humidity.mean[local.month - 1]?.[local.hour];
  const rawSd = input.climate.humidity.sd[local.month - 1]?.[local.hour];
  if (mean === undefined || rawSd === undefined) return silent('norm missing for this month/hour');
  // A degenerate sd would turn rounding noise into a "notable" deviation.
  const sd = Math.max(rawSd, 3);
  const z = (input.humidity - mean) / sd;
  const gap = input.humidity - mean;
  const why = `humidity ${Math.round(input.humidity)}% vs norm ${Math.round(mean)}±${Math.round(sd)}% (month ${local.month}, ${local.hour}h) → z=${z.toFixed(2)}, gap ${Math.round(gap)} (needs |z| ≥ ${thresholds.humidity.sd}, |gap| ≥ ${thresholds.humidity.gap})`;
  const month = monthGenitive(local.month);
  const t = thresholds.humidity;
  if (z <= -t.sd && -gap >= t.gap) {
    return {
      id: 'humidity',
      fired: true,
      phrase: z <= -t.strongSd ? `очень сухо для ${month}` : `суше обычного для ${month}`,
      weight: 40,
      why,
    };
  }
  if (z >= t.sd && gap >= t.gap) {
    if (input.wetOrFog) return silent(`${why}; damp but it rains/fogs`);
    return {
      id: 'humidity',
      fired: true,
      phrase: z >= t.strongSd ? `очень сыро для ${month}` : `влажнее обычного для ${month}`,
      weight: 40,
      why,
    };
  }
  return silent(why);
}
