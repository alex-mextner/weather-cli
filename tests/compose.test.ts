import { describe, expect, test } from 'bun:test';

import { say } from '../src/compose';
import type { Condition, Mode } from '../src/types';
import { T, TZ, at, bundle, climate } from './fixtures';

const NOW = at('2026-11-09', 14, 10);

describe('say: now', () => {
  test('quiet day is one plain sentence', () => {
    const r = say({
      bundle: bundle({
        now: NOW,
        current: { temp: 6.9, condition: 'cloudy', humidity: 70, wind: 2 },
      }),
      now: NOW,
    });
    expect(r.text).toBe('На улице 7 градусов, пасмурно.');
    expect(r.dropped).toEqual([]);
  });

  test('feels-like joins the headline only when it differs by ≥ 6', () => {
    const base = { temp: 7, condition: 'cloudy' as const, humidity: 70, wind: 2 };
    expect(
      say({ bundle: bundle({ now: NOW, current: { ...base, feels: 2 } }), now: NOW }).text,
    ).toBe('На улице 7 градусов, пасмурно.');
    expect(
      say({ bundle: bundle({ now: NOW, current: { ...base, feels: 1 } }), now: NOW }).text,
    ).toBe('На улице 7 градусов, ощущается как 1, пасмурно.');
  });

  test('rain with a rising wind, rain starting later, and tomorrow in the evening compete for the budget', () => {
    const now = at('2026-11-09', 21, 30);
    const b = bundle({
      now,
      current: { temp: 8, condition: 'cloudy', wind: 3 },
      hour: (_, s) => (s >= at('2026-11-09', 22) ? { condition: 'rain', precip: 2 } : {}),
      day: (o) => (o === 1 ? { max: 12, min: 6, condition: 'rain', precip: 8 } : {}),
    });
    const r = say({ bundle: b, now });
    expect(r.sentences).toHaveLength(3);
    expect(r.sentences[1]).toBe('Скоро начнётся дождь.');
    expect(r.decisions.find((d) => d.id === 'tomorrow')?.fired).toBe(true);
  });

  test('tomorrow is attached from 21:00 local, not before', () => {
    const mk = (now: number) =>
      say({
        bundle: bundle({
          now,
          current: { temp: 8, wind: 1 },
          day: (o) => (o === 1 ? { max: 12, min: 6 } : {}),
        }),
        now,
      });
    expect(mk(at('2026-11-09', 20, 59)).text).not.toContain('Завтра');
    expect(mk(at('2026-11-09', 21, 0)).text).toContain('Завтра днём 12, ночью 6 градусов');
  });

  test('humidity speaks only against a loaded norm', () => {
    const b = bundle({ now: NOW, current: { temp: 12, humidity: 40, wind: 1 } });
    expect(say({ bundle: b, now: NOW }).text).toBe('На улице 12 градусов, пасмурно.');
    expect(say({ bundle: b, now: NOW, climate: climate(75, 10) }).text).toBe(
      'На улице 12 градусов, пасмурно. Очень сухо для ноября.',
    );
  });

  test('a week insight is added when nothing more urgent competes', () => {
    const b = bundle({
      now: NOW,
      current: { temp: 9, wind: 1 },
      day: (o) => ({ max: o >= 4 ? 18 : 11 }),
    });
    expect(say({ bundle: b, now: NOW }).text).toContain('В пятницу потеплеет до 18 градусов.');
  });

  test('sentences over the budget are reported, not silently lost', () => {
    const b = bundle({
      now: NOW,
      current: { temp: 9, wind: 10, humidity: 30, condition: 'rain', precip: 1 },
      hour: (_, s) => (s > NOW ? { condition: 'rain', precip: 0 } : {}),
      day: (o) => ({ max: o >= 4 ? 18 : 11 }),
    });
    const r = say({ bundle: b, now: NOW, climate: climate(75, 10), maxSentences: 2 });
    expect(r.sentences).toHaveLength(2);
    expect(r.dropped.length).toBeGreaterThan(0);
  });

  test('data older than three hours is flagged stale', () => {
    const b = { ...bundle({ now: NOW }), fetchedAt: NOW - 4 * 3_600_000 };
    expect(say({ bundle: b, now: NOW }).stale).toBe(true);
  });
});

describe('say: other modes', () => {
  test('tomorrow, week, umbrella, dress', () => {
    const b = bundle({
      now: NOW,
      day: (o) =>
        o === 1 ? { max: 14, min: 6, condition: 'rain', precip: 4 } : { max: 9, min: 4 },
    });
    expect(say({ bundle: b, now: NOW, mode: 'tomorrow' }).text).toStartWith(
      'Завтра днём 14, ночью 6 градусов, дождь',
    );
    expect(say({ bundle: b, now: NOW, mode: 'week' }).text).toStartWith(
      'На неделе от 4 до 14 градусов',
    );
    expect(say({ bundle: b, now: NOW, mode: 'umbrella' }).mode).toBe('umbrella');
    expect(say({ bundle: b, now: NOW, mode: 'dress' }).text).toStartWith('Прохладно');
  });

  test('a quiet week says so', () => {
    expect(say({ bundle: bundle({ now: NOW }), now: NOW, mode: 'week' }).text).toEndWith(
      'Резких перемен не будет.',
    );
  });
});

// A tiny seeded generator keeps the property test reproducible.
function rng(seed: number): () => number {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const CONDITIONS: Condition[] = [
  'clear',
  'partly-cloudy',
  'cloudy',
  'fog',
  'drizzle',
  'rain',
  'heavy-rain',
  'thunderstorm',
  'snow',
  'heavy-snow',
  'sleet',
  'hail',
];
const MODES: Mode[] = ['now', 'tomorrow', 'week', 'umbrella', 'dress'];

describe('property: spoken output is human', () => {
  test('never a decimal, speed unit, percentage, minus sign or placeholder — 1500 random worlds', () => {
    const random = rng(20261109);
    const between = (lo: number, hi: number): number => lo + random() * (hi - lo);
    const pick = <V>(list: V[]): V => list[Math.floor(random() * list.length)] as V;
    for (let n = 0; n < 1500; n += 1) {
      const month = 1 + Math.floor(random() * 12);
      const date = `2026-${String(month).padStart(2, '0')}-${String(1 + Math.floor(random() * 27)).padStart(2, '0')}`;
      const now = at(date, Math.floor(random() * 24), Math.floor(random() * 60));
      const base = between(-25, 38);
      const b = bundle({
        now,
        current: {
          temp: base,
          feels: base + between(-14, 14),
          humidity: between(5, 100),
          wind: between(0, 20),
          gust: between(0, 30),
          precip: random() < 0.4 ? between(0, 8) : 0,
          condition: pick(CONDITIONS),
        },
        hour: () => ({
          temp: base + between(-6, 6),
          wind: between(0, 20),
          gust: between(0, 30),
          precip: random() < 0.3 ? between(0, 8) : 0,
          precipProb: between(0, 100),
          condition: pick(CONDITIONS),
        }),
        day: () => {
          const max = base + between(-10, 10);
          return {
            max,
            min: max - between(2, 14),
            precip: random() < 0.4 ? between(0, 20) : 0,
            snow: random() < 0.2 ? between(0, 15) : 0,
            condition: pick(CONDITIONS),
            windMax: between(0, 18),
            gustMax: between(0, 28),
          };
        },
      });
      const norms =
        random() < 0.7
          ? climate(between(40, 90), between(4, 20), between(-5, 25), between(1, 6))
          : null;
      for (const mode of MODES) {
        const r = say({ bundle: b, now, mode, climate: norms, thresholds: T });
        expect(r.text.length).toBeGreaterThan(0);
        expect(r.text).not.toMatch(/\d[.,]\d/);
        expect(r.text).not.toMatch(/км\/ч|km\/h|м\/с|m\/s|kph|mph/i);
        expect(r.text).not.toMatch(/%|процент/i);
        expect(r.text).not.toMatch(/-\d|минус 0|NaN|undefined|null|Infinity/);
        expect(r.text).toMatch(/[.!?]$/);
        if (mode === 'now') expect(r.sentences.length).toBeLessThanOrEqual(3);
      }
    }
  });

  test('the timezone is respected: the same instant is evening in Belgrade and afternoon in New York', () => {
    const now = at('2026-11-09', 22, 0);
    const b = bundle({ now, day: (o) => (o === 1 ? { max: 12, min: 6 } : {}) });
    expect(say({ bundle: b, now }).text).toContain('Завтра');
    const ny = { ...b, place: { ...b.place, tz: 'America/New_York' } };
    expect(say({ bundle: ny, now }).text).not.toContain('Завтра');
    expect(TZ).toBe('Europe/Belgrade');
  });
});
