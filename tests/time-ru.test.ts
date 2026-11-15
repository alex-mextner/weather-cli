import { describe, expect, test } from 'bun:test';

import { dayName, degrees, degreesUpTo, plural, sentence, signed } from '../src/ru';
import {
  addDays,
  dayPartOf,
  daysBetween,
  localDate,
  localParts,
  weekdayOf,
  zonedTimeToMs,
} from '../src/time';
import { spoken } from '../src/units';
import { TZ, at } from './fixtures';

describe('time', () => {
  test('zonedTimeToMs round-trips local wall-clock time, winter and summer', () => {
    for (const [date, hour] of [
      ['2026-01-15', 9],
      ['2026-07-15', 23],
      ['2026-11-09', 0],
    ] as const) {
      const p = localParts(zonedTimeToMs(TZ, date, hour, 30), TZ);
      expect([localDate(zonedTimeToMs(TZ, date, hour, 30), TZ), p.hour, p.minute]).toEqual([
        date,
        hour,
        30,
      ]);
    }
  });

  test('tomorrow is a calendar step: the eve of the spring DST change', () => {
    // 2026-03-29 02:00 CET jumps to 03:00 CEST: the 29th is 23 hours long.
    const now = at('2026-03-28', 23, 30);
    expect(addDays(localDate(now, TZ), 1)).toBe('2026-03-29');
    // The naive "+24h" lands on the 30th: exactly the bug this guards against.
    expect(localDate(now + 24 * 3_600_000, TZ)).toBe('2026-03-30');
  });

  test('tomorrow is a calendar step: the eve of the autumn DST change (25-hour day)', () => {
    const now = at('2026-10-24', 23, 30);
    expect(addDays(localDate(now, TZ), 1)).toBe('2026-10-25');
    expect(daysBetween('2026-10-24', '2026-10-26')).toBe(2);
  });

  test('weekday and month arithmetic', () => {
    expect(weekdayOf('2026-11-09')).toBe(1);
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
  });

  test.each([
    [5, 'night'],
    [6, 'morning'],
    [10, 'morning'],
    [11, 'day'],
    [16, 'day'],
    [17, 'evening'],
    [21, 'evening'],
    [22, 'night'],
    [0, 'night'],
  ] as const)('hour %i is %s', (hour, part) => {
    expect(dayPartOf(hour)).toBe(part);
  });
});

describe('russian wording', () => {
  test.each([
    [0, 'ноль градусов'],
    [1, '1 градус'],
    [2, '2 градуса'],
    [5, '5 градусов'],
    [11, '11 градусов'],
    [21, '21 градус'],
    [22, '22 градуса'],
    [-1, 'минус 1 градус'],
    [-3, 'минус 3 градуса'],
    [-12, 'минус 12 градусов'],
  ])('degrees(%i) = %s', (n, expected) => {
    expect(degrees(n)).toBe(expected);
  });

  test.each([
    [0, 'до нуля'],
    [1, 'до 1 градуса'],
    [2, 'до 2 градусов'],
    [-2, 'до минус 2 градусов'],
    [11, 'до 11 градусов'],
    [21, 'до 21 градуса'],
  ])('degreesUpTo(%i) = %s', (n, expected) => {
    expect(degreesUpTo(n)).toBe(expected);
  });

  test('signed and plural', () => {
    expect(signed(-5)).toBe('минус 5');
    expect(signed(0)).toBe('0');
    expect(plural(3, 'день', 'дня', 'дней')).toBe('дня');
    expect(plural(12, 'день', 'дня', 'дней')).toBe('дней');
  });

  test('day names relative to Monday 9 Nov 2026', () => {
    expect(dayName('2026-11-09', 1, 'on')).toBe('завтра');
    expect(dayName('2026-11-09', 2, 'on')).toBe('послезавтра');
    expect(dayName('2026-11-09', 3, 'on')).toBe('в четверг');
    expect(dayName('2026-11-09', 8, 'from')).toBe('со вторника');
    expect(dayName('2026-11-09', 4, 'to')).toBe('к пятнице');
  });

  test('sentence capitalises and terminates once', () => {
    expect(sentence('дождь скоро закончится')).toBe('Дождь скоро закончится.');
    expect(sentence('Уже готово.')).toBe('Уже готово.');
    expect(sentence('  ')).toBe('');
  });
});

describe('spoken rounding', () => {
  test.each([
    [6.9, 7],
    [6.4, 6],
    [6.5, 7],
    [-6.5, -7],
    [-0.4, 0],
    [0.4, 0],
    [-1.9, -2],
  ])('spoken(%f) = %i and is never -0', (input, expected) => {
    expect(Object.is(spoken(input), expected)).toBe(true);
  });
});
