/**
 * @file  Wind as an umbrella question: silent when it does not matter, a category when it does.
 *
 * Accessed via: `say()` (now mode), the lookahead decision (wind rising) and the umbrella answer.
 *
 * Assumptions: speeds in m/s (provider boundary converts km/h). Sustained wind and gusts are
 * judged separately and the higher category wins, so a calm hour with 12 m/s gusts is not "calm".
 * The raw number is never spoken.
 */

import type { Thresholds } from '../thresholds';
import type { Decision } from '../types';

export type WindLevel = 'none' | 'breezy' | 'strong' | 'storm';

const ORDER: WindLevel[] = ['none', 'breezy', 'strong', 'storm'];

export function windLevelRank(level: WindLevel): number {
  return ORDER.indexOf(level);
}

export function windLevel(
  wind: number | undefined,
  gust: number | undefined,
  thresholds: Thresholds,
): WindLevel {
  const t = thresholds.wind;
  const sustained = wind ?? 0;
  const gusts = gust ?? 0;
  if (sustained >= t.storm || gusts >= t.gustStorm) return 'storm';
  if (sustained >= t.strong || gusts >= t.gustStrong) return 'strong';
  if (sustained >= t.breezy || gusts >= t.gustBreezy) return 'breezy';
  return 'none';
}

export interface WindInput {
  wind?: number | undefined;
  gust?: number | undefined;
  /** Precipitation falls (or is about to): wind then decides whether an umbrella survives. */
  wet: boolean;
}

export function describeWind(input: WindInput, thresholds: Thresholds): Decision {
  const level = windLevel(input.wind, input.gust, thresholds);
  const sustained = (input.wind ?? 0).toFixed(1);
  const gusts = (input.gust ?? 0).toFixed(1);
  const why = `wind ${sustained} m/s, gust ${gusts} m/s → ${level}${input.wet ? ', wet' : ''}`;
  const gustDriven = windLevel(input.wind, undefined, thresholds) !== level && level !== 'none';
  switch (level) {
    case 'none':
      return { id: 'wind', fired: false, phrase: null, weight: 80, why };
    case 'breezy':
      return {
        id: 'wind',
        fired: true,
        phrase: input.wet
          ? 'ветрено, зонт придётся держать крепко'
          : gustDriven
            ? 'порывистый ветер'
            : 'ветрено',
        weight: input.wet ? 80 : 35,
        why,
      };
    case 'strong':
      return {
        id: 'wind',
        fired: true,
        phrase: input.wet
          ? 'сильный ветер, зонт может вывернуть'
          : gustDriven
            ? 'сильные порывы ветра'
            : 'сильный ветер',
        weight: input.wet ? 88 : 60,
        why,
      };
    case 'storm':
      return {
        id: 'wind',
        fired: true,
        phrase: input.wet ? 'штормовой ветер, зонт унесёт' : 'штормовой ветер, на улице опасно',
        weight: 92,
        why,
      };
  }
}
