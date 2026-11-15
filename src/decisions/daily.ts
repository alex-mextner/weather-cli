/**
 * @file  Day-level decisions: tomorrow's headline, week insights, week summary.
 *
 * Accessed via: `say()` — "tomorrow" mode, "week" mode, and the late-evening tail of "now".
 *
 * Assumptions: `bundle.daily` is in local calendar days (provider boundary); offsets are
 * calendar days from today's local date, computed with `daysBetween`, never from `ms + 24h`.
 * An insight exists only when something changes (onset/end/threshold crossing): quiet weeks
 * produce no insight, not a filler sentence.
 */

import {
  type DayCase,
  dayName,
  degrees,
  degreesGenitive,
  degreesUpTo,
  plural,
  signed,
} from '../ru';
import type { Thresholds } from '../thresholds';
import { daysBetween, localDate, localParts } from '../time';
import type { Bundle, Day, Decision } from '../types';
import { spoken } from '../units';
import { windLevel, windLevelRank } from './wind';

export interface DayAt {
  offset: number;
  day: Day;
}

/** Forecast days from today on, with their calendar offset from today. */
export function daysFrom(bundle: Bundle, now: number): DayAt[] {
  const today = localDate(now, bundle.place.tz);
  return bundle.daily
    .map((day) => ({ offset: daysBetween(today, day.date), day }))
    .filter((entry) => entry.offset >= 0)
    .sort((a, b) => a.offset - b.offset);
}

const isSnowy = (day: Day, t: Thresholds): boolean =>
  day.snow >= t.week.snowDayCm ||
  ((day.condition === 'snow' || day.condition === 'heavy-snow') && day.precip >= 0.5);

const isWetDay = (day: Day, t: Thresholds): boolean =>
  day.precip >= t.week.wetDayMm || isSnowy(day, t);

/** The sky of a whole day in words; `mm` refines rain intensity. */
export function dayWeather(day: Day, thresholds: Thresholds): string {
  switch (day.condition) {
    case 'clear':
      return 'ясно';
    case 'partly-cloudy':
      return isWetDay(day, thresholds)
        ? 'переменная облачность, возможны осадки'
        : 'переменная облачность';
    case 'cloudy':
      return isWetDay(day, thresholds) ? 'пасмурно, возможен дождь' : 'пасмурно';
    case 'fog':
      return 'туман';
    case 'drizzle':
      return 'морось';
    case 'rain':
      return day.precip >= 10 ? 'сильный дождь' : 'дождь';
    case 'heavy-rain':
      return 'сильный дождь';
    case 'thunderstorm':
      return 'гроза';
    case 'snow':
      return 'снег';
    case 'heavy-snow':
      return 'сильный снегопад';
    case 'sleet':
      return 'мокрый снег';
    case 'hail':
      return 'град';
  }
}

export function describeTomorrow(
  bundle: Bundle,
  now: number,
  thresholds: Thresholds,
  forced: boolean,
): Decision {
  const id = 'tomorrow';
  const local = localParts(now, bundle.place.tz);
  const late = local.hour >= thresholds.lateHour;
  if (!forced && !late) {
    return {
      id,
      fired: false,
      phrase: null,
      weight: 85,
      why: `local hour ${local.hour} < ${thresholds.lateHour}: tomorrow not requested`,
    };
  }
  const days = daysFrom(bundle, now);
  const today = days.find((d) => d.offset === 0)?.day;
  const tomorrow = days.find((d) => d.offset === 1)?.day;
  if (!tomorrow) {
    return { id, fired: false, phrase: null, weight: 85, why: 'no forecast day for tomorrow' };
  }
  const high = spoken(tomorrow.max);
  const low = spoken(tomorrow.min);
  const parts = [
    `завтра днём ${signed(high)}, ночью ${degrees(low)}`,
    dayWeather(tomorrow, thresholds),
  ];
  const level = windLevel(tomorrow.windMax, tomorrow.gustMax, thresholds);
  if (windLevelRank(level) >= windLevelRank('strong')) parts.push('сильный ветер');
  if (today) {
    const shift = high - spoken(today.max);
    if (Math.abs(shift) >= thresholds.tomorrow.shift) {
      parts.push(
        `на ${Math.abs(shift)} ${plural(Math.abs(shift), 'градус', 'градуса', 'градусов')} ${shift > 0 ? 'теплее' : 'холоднее'}, чем сегодня`,
      );
    }
  }
  return {
    id,
    fired: true,
    phrase: parts.join(', '),
    weight: forced ? 100 : 85,
    why: `${forced ? 'requested' : `local hour ${local.hour} ≥ ${thresholds.lateHour}`}; tomorrow ${tomorrow.date} high ${high}° low ${low}°${
      today ? `, today high ${spoken(today.max)}°` : ''
    }`,
  };
}

export type InsightKind =
  | 'snow-onset'
  | 'rain-onset'
  | 'rain-end'
  | 'warming'
  | 'cooling'
  | 'frost'
  | 'hard-frost'
  | 'heat';

export interface Insight {
  kind: InsightKind;
  /** Calendar offset of the day the change happens on. */
  offset: number;
  phrase: string;
  weight: number;
}

function insight(kind: InsightKind, offset: number, base: number, phrase: string): Insight {
  return { kind, offset, phrase, weight: base - offset * 3 };
}

/** Everything notable that changes during the coming days, strongest first. */
export function weekInsights(bundle: Bundle, now: number, thresholds: Thresholds): Insight[] {
  const t = thresholds.week;
  const today = localDate(now, bundle.place.tz);
  const days = daysFrom(bundle, now).filter((d) => d.offset <= t.horizonDays);
  const day0 = days.find((d) => d.offset === 0);
  const future = days.filter((d) => d.offset >= 1);
  const out: Insight[] = [];
  const on = (offset: number, grammaticalCase: DayCase = 'on'): string =>
    dayName(today, offset, grammaticalCase);

  // Snow arriving where it was not snowing.
  const snowStart = future.find((d) => isSnowy(d.day, thresholds));
  if (snowStart && !(day0 && isSnowy(day0.day, thresholds))) {
    out.push(insight('snow-onset', snowStart.offset, 82, `${on(snowStart.offset)} пойдёт снег`));
  }

  // Rain arriving after dry days.
  const rainOnset = future.find((d) => isWetDay(d.day, thresholds) && !isSnowy(d.day, thresholds));
  const wetBefore = days.some(
    (d) => rainOnset && d.offset < rainOnset.offset && isWetDay(d.day, thresholds),
  );
  if (rainOnset && !wetBefore) {
    const streak = future.filter(
      (d) =>
        d.offset >= rainOnset.offset &&
        d.offset <= rainOnset.offset + 1 &&
        isWetDay(d.day, thresholds),
    ).length;
    out.push(
      insight(
        'rain-onset',
        rainOnset.offset,
        72,
        streak >= 2
          ? `${on(rainOnset.offset)} начнутся дожди`
          : `${on(rainOnset.offset)} пойдёт дождь`,
      ),
    );
  }

  // Rain ending after wet days.
  if (day0 && isWetDay(day0.day, thresholds)) {
    const firstDry = future.find((d) => !isWetDay(d.day, thresholds));
    if (firstDry) {
      const after = future.filter(
        (d) => d.offset > firstDry.offset && d.offset <= firstDry.offset + 1,
      );
      if (after.every((d) => !isWetDay(d.day, thresholds))) {
        const spell = days.filter((d) => d.offset < firstDry.offset);
        const snowDays = spell.filter((d) => isSnowy(d.day, thresholds)).length;
        const kind = snowDays === 0 ? 'rain' : snowDays === spell.length ? 'snow' : 'mixed';
        const single = firstDry.offset === 1;
        const noun = {
          rain: single ? 'дождь' : 'дожди',
          snow: single ? 'снег' : 'снегопады',
          mixed: 'осадки',
        }[kind];
        out.push(
          insight(
            'rain-end',
            firstDry.offset,
            66,
            single
              ? `${noun} ${kind === 'mixed' ? 'закончатся' : 'закончится'} уже завтра`
              : `${noun} закончатся ${on(firstDry.offset, 'to')}`,
          ),
        );
      }
    }
  }

  // Warming / cooling against the level of today and tomorrow.
  const reference = [day0, future[0]].filter((d): d is DayAt => d !== undefined);
  if (reference.length > 0) {
    const level = reference.reduce((sum, d) => sum + d.day.max, 0) / reference.length;
    const shifted = future.filter(
      (d) => d.offset >= 2 && Math.abs(spoken(d.day.max) - spoken(level)) >= t.shift,
    );
    const first = shifted[0];
    if (first) {
      const warmer = spoken(first.day.max) > spoken(level);
      out.push(
        insight(
          warmer ? 'warming' : 'cooling',
          first.offset,
          62,
          warmer
            ? `${on(first.offset)} потеплеет ${degreesUpTo(spoken(first.day.max))}`
            : `${on(first.offset, 'from')} похолодает, днём всего ${degrees(spoken(first.day.max))}`,
        ),
      );
    }
  }

  // Frost arriving after nights above freezing.
  const frostDay = future.find((d) => d.day.min <= t.frost);
  if (frostDay && !(day0 && day0.day.min <= t.frost)) {
    const hard = spoken(frostDay.day.min) <= t.hardFrost;
    out.push(
      insight(
        hard ? 'hard-frost' : 'frost',
        frostDay.offset,
        hard ? 80 : 76,
        hard
          ? `${on(frostDay.offset)} ночью ударит сильный мороз ${degreesUpTo(spoken(frostDay.day.min))}`
          : `${on(frostDay.offset)} ночью заморозки ${degreesUpTo(spoken(frostDay.day.min))}`,
      ),
    );
  }

  // Heat arriving after cooler days.
  const heatDay = future.find((d) => d.day.max >= t.heat);
  if (heatDay && !(day0 && day0.day.max >= t.heat)) {
    const strong = spoken(heatDay.day.max) >= t.strongHeat;
    out.push(
      insight(
        'heat',
        heatDay.offset,
        74,
        `${on(heatDay.offset)} ${strong ? 'сильная жара' : 'жара'} ${degreesUpTo(spoken(heatDay.day.max))}`,
      ),
    );
  }

  return out.sort((a, b) => b.weight - a.weight);
}

export function describeInsight(bundle: Bundle, now: number, thresholds: Thresholds): Decision {
  const all = weekInsights(bundle, now, thresholds);
  const top = all[0];
  if (!top) {
    return {
      id: 'insight',
      fired: false,
      phrase: null,
      weight: 50,
      why: 'no onset, end or threshold crossing in the coming days',
    };
  }
  return {
    id: 'insight',
    fired: true,
    phrase: top.phrase,
    weight: Math.min(top.weight, 75),
    why: all.map((i) => `${i.kind}@+${i.offset}d (${i.weight})`).join(', '),
  };
}

/** One sentence for the "week" mode when it needs a base: range and wet days. */
export function describeWeekSummary(bundle: Bundle, now: number, thresholds: Thresholds): Decision {
  const days = daysFrom(bundle, now).filter((d) => d.offset <= thresholds.week.horizonDays);
  if (days.length === 0) {
    return { id: 'week-summary', fired: false, phrase: null, weight: 100, why: 'no forecast days' };
  }
  const low = spoken(Math.min(...days.map((d) => d.day.min)));
  const high = spoken(Math.max(...days.map((d) => d.day.max)));
  const wet = days.filter((d) => isWetDay(d.day, thresholds)).length;
  const snow = days.some((d) => isSnowy(d.day, thresholds));
  const precip =
    wet === 0 ? 'без осадков' : `${snow ? 'осадки' : 'дождь'} в ${wet} из ${days.length} дней`;
  return {
    id: 'week-summary',
    fired: true,
    phrase: `на неделе от ${signed(low)} до ${degreesGenitive(high)}, ${precip}`,
    weight: 100,
    why: `${days.length} days, lows ${low}°, highs ${high}°, ${wet} wet`,
  };
}
