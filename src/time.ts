/**
 * @file  Local-time arithmetic on top of Intl, no dependencies.
 *
 * Accessed via: every decision that talks about "tonight", "tomorrow", a month norm or a weekday.
 *
 * Assumptions: epoch milliseconds in, IANA zone names. "Tomorrow" is a calendar step on the
 * local date, never `ms + 24h` (a DST day is 23 or 25 hours long, and +24h from 23:30 on the
 * eve of the spring change lands two calendar days ahead).
 */

export interface LocalParts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  /** 0 = Sunday. */
  weekday: number;
}

const formatters = new Map<string, Intl.DateTimeFormat>();

function formatterFor(tz: string): Intl.DateTimeFormat {
  const cached = formatters.get(tz);
  if (cached) return cached;
  const created = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    hourCycle: 'h23',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: 'numeric',
    weekday: 'short',
  });
  formatters.set(tz, created);
  return created;
}

const WEEKDAY_INDEX: Record<string, number> = {
  Sun: 0,
  Mon: 1,
  Tue: 2,
  Wed: 3,
  Thu: 4,
  Fri: 5,
  Sat: 6,
};

export function localParts(ms: number, tz: string): LocalParts {
  const parts = formatterFor(tz).formatToParts(new Date(ms));
  const pick = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((part) => part.type === type)?.value ?? '0';
  return {
    year: Number(pick('year')),
    month: Number(pick('month')),
    day: Number(pick('day')),
    hour: Number(pick('hour')),
    minute: Number(pick('minute')),
    weekday: WEEKDAY_INDEX[pick('weekday')] ?? 0,
  };
}

const pad = (n: number): string => String(n).padStart(2, '0');

export function localDate(ms: number, tz: string): string {
  const p = localParts(ms, tz);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}`;
}

function dateToUtcMs(date: string): number {
  const [y, m, d] = date.split('-').map(Number);
  return Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1);
}

/** Calendar arithmetic on a YYYY-MM-DD string. */
export function addDays(date: string, days: number): string {
  const moved = new Date(dateToUtcMs(date) + days * 86_400_000);
  return `${moved.getUTCFullYear()}-${pad(moved.getUTCMonth() + 1)}-${pad(moved.getUTCDate())}`;
}

export function daysBetween(from: string, to: string): number {
  return Math.round((dateToUtcMs(to) - dateToUtcMs(from)) / 86_400_000);
}

/** 0 = Sunday. */
export function weekdayOf(date: string): number {
  return new Date(dateToUtcMs(date)).getUTCDay();
}

/**
 * Epoch ms of a wall-clock time in a zone. For a time skipped by a DST jump it resolves to the
 * instant after the jump; for a repeated time, to the first occurrence.
 */
export function zonedTimeToMs(tz: string, date: string, hour = 0, minute = 0): number {
  const wall = dateToUtcMs(date) + hour * 3_600_000 + minute * 60_000;
  let guess = wall;
  for (let i = 0; i < 3; i += 1) {
    const p = localParts(guess, tz);
    const shown = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute);
    const delta = wall - shown;
    if (delta === 0) break;
    guess += delta;
  }
  return guess;
}

export type DayPart = 'night' | 'morning' | 'day' | 'evening';

export function dayPartOf(hour: number): DayPart {
  if (hour < 6 || hour >= 22) return 'night';
  if (hour < 11) return 'morning';
  if (hour < 17) return 'day';
  return 'evening';
}
