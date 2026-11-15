/**
 * @file  Russian wording helpers: plural agreement, signed temperatures, weekday and month forms.
 *
 * Accessed via: every decision function that builds a phrase.
 *
 * Assumptions: all numbers are already whole (callers pass `spoken(x)`); output never contains
 * a decimal separator, a percent sign or a speed unit — the property test in tests/ enforces it
 * on whole reports.
 */

import { type DayPart, dayPartOf, weekdayOf } from './time';

export function plural(n: number, one: string, few: string, many: string): string {
  const abs = Math.abs(n);
  const lastTwo = abs % 100;
  const last = abs % 10;
  if (lastTwo >= 11 && lastTwo <= 14) return many;
  if (last === 1) return one;
  if (last >= 2 && last <= 4) return few;
  return many;
}

/** «минус 3», «0», «7»: a temperature without the unit word. */
export function signed(n: number): string {
  return n < 0 ? `минус ${-n}` : String(n);
}

/** «минус 3 градуса», «1 градус», «ноль градусов». */
export function degrees(n: number): string {
  const word = plural(n, 'градус', 'градуса', 'градусов');
  return n === 0 ? `ноль ${word}` : `${signed(n)} ${word}`;
}

const MONTH_GENITIVE = [
  'января',
  'февраля',
  'марта',
  'апреля',
  'мая',
  'июня',
  'июля',
  'августа',
  'сентября',
  'октября',
  'ноября',
  'декабря',
];

/** «ноября» for month 11. */
export function monthGenitive(month: number): string {
  return MONTH_GENITIVE[month - 1] ?? '';
}

interface WeekdayForms {
  on: string;
  from: string;
  to: string;
}

const WEEKDAYS: WeekdayForms[] = [
  { on: 'в воскресенье', from: 'с воскресенья', to: 'к воскресенью' },
  { on: 'в понедельник', from: 'с понедельника', to: 'к понедельнику' },
  { on: 'во вторник', from: 'со вторника', to: 'ко вторнику' },
  { on: 'в среду', from: 'со среды', to: 'к среде' },
  { on: 'в четверг', from: 'с четверга', to: 'к четвергу' },
  { on: 'в пятницу', from: 'с пятницы', to: 'к пятнице' },
  { on: 'в субботу', from: 'с субботы', to: 'к субботе' },
];

export type DayCase = 'on' | 'from' | 'to';

/**
 * A day named relative to today: offset 1 → «завтра», 2 → «послезавтра», further → weekday
 * in the requested case. `todayDate` is the local YYYY-MM-DD of today.
 */
export function dayName(todayDate: string, offset: number, grammaticalCase: DayCase): string {
  if (offset === 0) return grammaticalCase === 'on' ? 'сегодня' : 'с сегодняшнего дня';
  if (offset === 1)
    return grammaticalCase === 'on'
      ? 'завтра'
      : grammaticalCase === 'from'
        ? 'с завтрашнего дня'
        : 'к завтрашнему дню';
  if (offset === 2)
    return grammaticalCase === 'on'
      ? 'послезавтра'
      : grammaticalCase === 'from'
        ? 'с послезавтра'
        : 'к послезавтрашнему дню';
  const date = new Date(Date.parse(`${todayDate}T00:00:00Z`) + offset * 86_400_000)
    .toISOString()
    .slice(0, 10);
  const forms = WEEKDAYS[weekdayOf(date)];
  return forms ? forms[grammaticalCase] : '';
}

const DAY_PART_ON: Record<DayPart, string> = {
  night: 'ночью',
  morning: 'утром',
  day: 'днём',
  evening: 'вечером',
};

/** «вечером» for hour 19. */
export function dayPartOn(hour: number): string {
  return DAY_PART_ON[dayPartOf(hour)];
}

export function capitalize(text: string): string {
  return text.length === 0 ? text : text.charAt(0).toUpperCase() + text.slice(1);
}

/** A sentence: capitalised, ends with a full stop unless it already ends with a mark. */
export function sentence(text: string): string {
  const trimmed = text.trim();
  if (trimmed.length === 0) return '';
  const capitalised = capitalize(trimmed);
  return /[.!?…]$/.test(capitalised) ? capitalised : `${capitalised}.`;
}

/** Genitive plural form used after «от»/«до»: «1 градуса», «минус 2 градусов». */
export function degreesGenitive(n: number): string {
  const abs = Math.abs(n);
  const word = abs % 10 === 1 && abs % 100 !== 11 ? 'градуса' : 'градусов';
  return `${signed(n)} ${word}`;
}

/** After «до»: «до минус 2 градусов», «до 1 градуса», «до нуля». */
export function degreesUpTo(n: number): string {
  return n === 0 ? 'до нуля' : `до ${degreesGenitive(n)}`;
}
