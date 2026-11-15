/**
 * @file  `say()`: run the decision functions for a mode and assemble a short spoken report.
 *
 * Accessed via: the CLI (`weather say`, `weather explain`) and any embedding host (the voice
 * assistant calls `say()` with a fetched bundle).
 *
 * Assumptions: a voice answer is 2-3 short sentences. The headline (temperature, feels-like,
 * sky) always speaks; extra sentences compete for the remaining budget by `weight`, and the
 * ones that lose are reported in `dropped` so `explain` can show what was left out.
 */

import { isWet } from './conditions';
import { describeAdvice, describeDress, describeUmbrella } from './decisions/advice';
import { describeTempAnomaly } from './decisions/anomaly';
import {
  describeInsight,
  describeTomorrow,
  describeWeekSummary,
  weekInsights,
} from './decisions/daily';
import { describeHumidity } from './decisions/humidity';
import { describeLookahead } from './decisions/lookahead';
import { describeFeels, describeSky, describeTemperature } from './decisions/temperature';
import { describeWind } from './decisions/wind';
import { sentence } from './ru';
import { DEFAULT_THRESHOLDS, type Thresholds } from './thresholds';
import { windowOf } from './decisions/lookahead';
import type { Bundle, Climate, Decision, DecisionId, Mode, Report } from './types';

export interface SayInput {
  bundle: Bundle;
  now: number;
  mode?: Mode;
  climate?: Climate | null;
  thresholds?: Thresholds;
  /** Sentence budget including the headline. Default 3 (week: 4). */
  maxSentences?: number;
}

/** Data older than this is reported as stale. */
const STALE_MS = 3 * 3_600_000;

function finish(
  mode: Mode,
  input: SayInput,
  sentences: string[],
  decisions: Decision[],
  dropped: DecisionId[],
): Report {
  return {
    mode,
    text: sentences.join(' '),
    sentences,
    decisions,
    dropped,
    asOf: input.bundle.fetchedAt,
    stale: input.now - input.bundle.fetchedAt > STALE_MS,
    sources: input.bundle.sources,
  };
}

function sayNow(input: SayInput, thresholds: Thresholds): Report {
  const { bundle, now } = input;
  const current = bundle.current;
  const climate = input.climate ?? null;
  const tz = bundle.place.tz;

  const temperature = describeTemperature(current);
  const feels = describeFeels(current, thresholds);
  const sky = describeSky(current);
  const lookahead = describeLookahead(bundle, now, thresholds);
  const wetSoon = windowOf(bundle, now, thresholds).some((m) => isWet(m));
  const wind = describeWind(
    { wet: isWet(current) || wetSoon, wind: current.wind, gust: current.gust },
    thresholds,
  );
  const humidity = describeHumidity(
    {
      humidity: current.humidity,
      climate,
      time: current.time,
      tz,
      wetOrFog: isWet(current) || current.condition === 'fog',
    },
    thresholds,
  );
  const tomorrow = describeTomorrow(bundle, now, thresholds, false);
  const insight = describeInsight(bundle, now, thresholds);
  const anomaly = describeTempAnomaly(current, climate, tz, thresholds);
  const advice = describeAdvice(bundle, now, thresholds);

  const headline = [
    `на улице ${temperature.phrase}`,
    ...(feels.fired && feels.phrase ? [feels.phrase] : []),
    sky.phrase,
  ].join(', ');

  const budget = Math.max(1, input.maxSentences ?? 3) - 1;
  const candidates = [lookahead, wind, humidity, tomorrow, insight, anomaly, advice]
    .filter((d) => d.fired)
    .sort((a, b) => b.weight - a.weight);
  const chosen = candidates.slice(0, budget);
  const dropped = candidates.slice(budget).map((d) => d.id);

  return finish(
    'now',
    input,
    [sentence(headline), ...chosen.map((d) => sentence(d.phrase ?? ''))],
    [temperature, feels, sky, lookahead, wind, humidity, tomorrow, insight, anomaly, advice],
    dropped,
  );
}

function sayTomorrow(input: SayInput, thresholds: Thresholds): Report {
  const tomorrow = describeTomorrow(input.bundle, input.now, thresholds, true);
  const insights = weekInsights(input.bundle, input.now, thresholds).filter((i) => i.offset >= 2);
  const top = insights[0];
  const decisions: Decision[] = [tomorrow];
  const sentences = tomorrow.fired
    ? [sentence(tomorrow.phrase ?? '')]
    : ['Прогноза на завтра нет.'];
  if (top) {
    decisions.push({
      id: 'insight',
      fired: true,
      phrase: top.phrase,
      weight: top.weight,
      why: `${top.kind}@+${top.offset}d`,
    });
    sentences.push(sentence(top.phrase));
  }
  return finish('tomorrow', input, sentences, decisions, []);
}

function sayWeek(input: SayInput, thresholds: Thresholds): Report {
  const summary = describeWeekSummary(input.bundle, input.now, thresholds);
  const insights = weekInsights(input.bundle, input.now, thresholds);
  const budget = Math.max(1, input.maxSentences ?? 4) - 1;
  const chosen = insights.slice(0, budget);
  const decisions: Decision[] = [summary];
  const sentences = summary.fired ? [sentence(summary.phrase ?? '')] : ['Прогноза на неделю нет.'];
  for (const i of chosen) {
    decisions.push({
      id: 'insight',
      fired: true,
      phrase: i.phrase,
      weight: i.weight,
      why: `${i.kind}@+${i.offset}d`,
    });
    sentences.push(sentence(i.phrase));
  }
  if (summary.fired && insights.length === 0) sentences.push('Резких перемен не будет.');
  const dropped = insights.slice(budget).map((): DecisionId => 'insight');
  return finish('week', input, sentences, decisions, dropped);
}

export function say(input: SayInput): Report {
  const thresholds = input.thresholds ?? DEFAULT_THRESHOLDS;
  const mode = input.mode ?? 'now';
  switch (mode) {
    case 'now':
      return sayNow(input, thresholds);
    case 'tomorrow':
      return sayTomorrow(input, thresholds);
    case 'week':
      return sayWeek(input, thresholds);
    case 'umbrella': {
      const umbrella = describeUmbrella(input.bundle, input.now, thresholds);
      return finish(mode, input, [sentence(umbrella.phrase ?? '')], [umbrella], []);
    }
    case 'dress': {
      const dress = describeDress(input.bundle, input.now, thresholds);
      return finish(mode, input, [sentence(dress.phrase ?? '')], [dress], []);
    }
  }
}
