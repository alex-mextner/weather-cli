/**
 * @file  "Warm/cold for the month" — current air against the same month and hour of the archive.
 *
 * Accessed via: `say()` (now mode), low priority, mostly seen in `explain`.
 *
 * Assumptions: silent without cached climate norms; both the z-score and the absolute gap must
 * pass, so a month with a tiny spread does not make 2° look dramatic.
 */

import { monthGenitive } from '../ru';
import type { Thresholds } from '../thresholds';
import { localParts } from '../time';
import type { Climate, Decision, Moment } from '../types';

export function describeTempAnomaly(
  current: Moment,
  climate: Climate | null,
  tz: string,
  thresholds: Thresholds,
): Decision {
  const silent = (why: string): Decision => ({
    id: 'temp-anomaly',
    fired: false,
    phrase: null,
    weight: 30,
    why,
  });
  if (climate === null) return silent('no climate norms cached');
  const local = localParts(current.time, tz);
  const mean = climate.temp.mean[local.month - 1]?.[local.hour];
  const rawSd = climate.temp.sd[local.month - 1]?.[local.hour];
  if (mean === undefined || rawSd === undefined) return silent('norm missing for this month/hour');
  const sd = Math.max(rawSd, 1);
  const gap = current.temp - mean;
  const z = gap / sd;
  const why = `air ${current.temp.toFixed(1)}° vs norm ${mean.toFixed(1)}±${sd.toFixed(1)}° → z=${z.toFixed(2)}, gap ${gap.toFixed(1)}° (needs |z| ≥ ${thresholds.anomaly.sd}, |gap| ≥ ${thresholds.anomaly.gap}°)`;
  const month = monthGenitive(local.month);
  if (z >= thresholds.anomaly.sd && gap >= thresholds.anomaly.gap) {
    return {
      id: 'temp-anomaly',
      fired: true,
      phrase: `для ${month} необычно тепло`,
      weight: 30,
      why,
    };
  }
  if (z <= -thresholds.anomaly.sd && -gap >= thresholds.anomaly.gap) {
    return {
      id: 'temp-anomaly',
      fired: true,
      phrase: `для ${month} необычно холодно`,
      weight: 30,
      why,
    };
  }
  return silent(why);
}
