/**
 * @file  "What changes within the hour" — the person is about to step outside.
 *
 * Accessed via: `say()` (now mode), `umbrella` and `dress` answers.
 *
 * Assumptions: hourly steps describe the hour STARTING at `time` (providers normalise this).
 * The window is (now − 30 min, now + windowMinutes]: the hour already running counts only if it
 * began recently, so a forecast for "14:00–15:00" is not read as news at 14:55.
 * Only the strongest change is spoken; the others stay visible in `why` for `explain`.
 */

import { isWet, type WetKind, wetKind } from '../conditions';
import { degrees } from '../ru';
import type { Thresholds } from '../thresholds';
import type { Bundle, Decision, Moment } from '../types';
import { spoken } from '../units';
import { windLevel, windLevelRank } from './wind';

const MINUTE = 60_000;

interface Event {
  kind: 'start' | 'thunder' | 'end' | 'temp' | 'wind';
  phrase: string;
  weight: number;
  note: string;
}

const PRECIP_NOUN: Record<WetKind, string> = {
  rain: 'дождь',
  snow: 'снег',
  mixed: 'мокрый снег',
};

function startPhrase(minutesAway: number, noun: string, heavy: boolean): string {
  const what = heavy && noun === 'дождь' ? 'ливень' : noun;
  if (minutesAway <= 20) return `вот-вот начнётся ${what}`;
  if (minutesAway <= 45) return `скоро начнётся ${what}`;
  return `через час начнётся ${what}`;
}

export function windowOf(bundle: Bundle, now: number, thresholds: Thresholds): Moment[] {
  const end = now + thresholds.lookahead.windowMinutes * MINUTE;
  return bundle.hourly.filter((m) => m.time > now - 30 * MINUTE && m.time <= end);
}

export function describeLookahead(bundle: Bundle, now: number, thresholds: Thresholds): Decision {
  const window = windowOf(bundle, now, thresholds);
  if (window.length === 0) {
    return {
      id: 'lookahead',
      fired: false,
      phrase: null,
      weight: 95,
      why: 'no forecast hour inside the window',
    };
  }
  const current = bundle.current;
  const wetNow = isWet(current);
  const events: Event[] = [];
  const ahead = window.filter((m) => m.time > now);

  if (!wetNow) {
    const first = window.find((m) => isWet(m));
    if (first) {
      const minutes = Math.max(0, Math.round((first.time - now) / MINUTE));
      const noun = PRECIP_NOUN[wetKind(first)];
      if (first.condition === 'thunderstorm') {
        events.push({
          kind: 'thunder',
          phrase: minutes <= 20 ? 'вот-вот начнётся гроза' : 'в ближайший час возможна гроза',
          weight: 97,
          note: `thunderstorm hour at +${minutes}min`,
        });
      } else {
        events.push({
          kind: 'start',
          phrase: startPhrase(minutes, noun, first.condition === 'heavy-rain'),
          weight: 95,
          note: `wet hour at +${minutes}min (${first.condition}, ${first.precip ?? '?'} mm)`,
        });
      }
    }
  } else if (ahead.length > 0 && ahead.every((m) => !isWet(m))) {
    events.push({
      kind: 'end',
      phrase: `${PRECIP_NOUN[wetKind(current)]} скоро закончится`,
      weight: 90,
      note: 'wet now, every hour of the window dry',
    });
  }

  const last = ahead.at(-1) ?? window.at(-1);
  if (last) {
    const delta = spoken(last.temp) - spoken(current.temp);
    if (Math.abs(delta) >= thresholds.lookahead.tempJump) {
      events.push({
        kind: 'temp',
        phrase: `через час ${delta < 0 ? 'похолодает' : 'потеплеет'}: будет ${degrees(spoken(last.temp))}`,
        weight: 85,
        note: `air ${spoken(current.temp)}° → ${spoken(last.temp)}° (needs ≥ ${thresholds.lookahead.tempJump}°)`,
      });
    }
    const now1 = windLevel(current.wind, current.gust, thresholds);
    const next1 = windLevel(last.wind, last.gust, thresholds);
    if (
      windLevelRank(next1) > windLevelRank(now1) &&
      windLevelRank(next1) >= windLevelRank('strong')
    ) {
      events.push({
        kind: 'wind',
        phrase: 'через час усилится ветер',
        weight: 80,
        note: `wind ${now1} → ${next1}`,
      });
    }
  }

  if (events.length === 0) {
    return {
      id: 'lookahead',
      fired: false,
      phrase: null,
      weight: 95,
      why: `${window.length} hour(s) in window, nothing material changes (wet now: ${wetNow})`,
    };
  }
  events.sort((a, b) => b.weight - a.weight);
  const top = events[0] as Event;
  return {
    id: 'lookahead',
    fired: true,
    phrase: top.phrase,
    weight: top.weight,
    why: events.map((e) => `${e.kind}: ${e.note}`).join('; '),
  };
}
