import { describe, expect, test } from 'bun:test';

import { describeAdvice, describeDress, describeUmbrella } from '../src/decisions/advice';
import { describeTempAnomaly } from '../src/decisions/anomaly';
import {
  describeInsight,
  describeTomorrow,
  describeWeekSummary,
  weekInsights,
} from '../src/decisions/daily';
import { describeHumidity } from '../src/decisions/humidity';
import { describeLookahead } from '../src/decisions/lookahead';
import {
  describeFeels,
  describeSky,
  describeTemperature,
  feelsLike,
} from '../src/decisions/temperature';
import { describeWind, windLevel } from '../src/decisions/wind';
import { withThresholds } from '../src/thresholds';
import { T, TZ, at, bundle, climate, moment } from './fixtures';

const NOW = at('2026-11-09', 14, 10);

describe('temperature', () => {
  test('rounds to whole degrees, no decimals', () => {
    expect(describeTemperature(moment(NOW, { temp: 6.9 })).phrase).toBe('7 градусов');
    expect(describeTemperature(moment(NOW, { temp: -0.4 })).phrase).toBe('ноль градусов');
    expect(describeTemperature(moment(NOW, { temp: -2.6 })).phrase).toBe('минус 3 градуса');
    expect(describeTemperature(moment(NOW, { temp: 1.2 })).phrase).toBe('1 градус');
  });
});

describe('feels-like boundary (spoken whole degrees, ≥ 6 either way)', () => {
  test.each([
    // air, feels, fires
    [7, 2, false], // gap 5
    [7, 1, true], // gap 6
    [7, 0, true],
    [10, 15, false], // +5
    [10, 16, true], // +6
    [-2, -8, true],
    [-2, -7, false],
    [6.5, 1.5, false], // spoken 7 vs 2 → gap 5 (rounding is what the listener hears)
    [5.6, 0, true], // spoken 6 vs 0 → gap 6
  ])('air %f feels %f → fires %p', (air, feels, fires) => {
    const decision = describeFeels(moment(NOW, { temp: air, feels }), T);
    expect(decision.fired).toBe(fires);
    expect(decision.phrase !== null).toBe(fires);
  });

  test('wording depends on the cause', () => {
    expect(describeFeels(moment(NOW, { temp: 5, feels: -3, wind: 10 }), T).phrase).toBe(
      'дует — ощущается как минус 3',
    );
    expect(describeFeels(moment(NOW, { temp: 5, feels: -3, wind: 7 }), T).phrase).toBe(
      'на ветру ощущается как минус 3',
    );
    expect(describeFeels(moment(NOW, { temp: 5, feels: -3, wind: 1 }), T).phrase).toBe(
      'ощущается как минус 3',
    );
    expect(
      describeFeels(moment(NOW, { temp: 28, feels: 35, humidity: 75, wind: 1 }), T).phrase,
    ).toBe('из-за влажности ощущается как 35');
  });

  test('computes apparent temperature when the provider gives none', () => {
    const cold = moment(NOW, { temp: 5, humidity: 80, wind: 12 });
    expect(feelsLike(cold)).not.toBeNull();
    expect(describeFeels(cold, T).fired).toBe(true);
    expect(feelsLike(moment(NOW, { temp: 5 }))).toBeNull();
    expect(describeFeels(moment(NOW, { temp: 5 }), T).fired).toBe(false);
  });

  test('the threshold is configurable', () => {
    const t = withThresholds({ feelsGap: 3 });
    expect(describeFeels(moment(NOW, { temp: 7, feels: 4 }), t).fired).toBe(true);
  });
});

describe('sky', () => {
  test.each([
    ['clear', 0, 'ясно'],
    ['cloudy', 0, 'пасмурно'],
    ['rain', 0.5, 'небольшой дождь'],
    ['rain', 2, 'идёт дождь'],
    ['rain', 5, 'сильный дождь'],
    ['snow', 0.3, 'идёт снег'],
    ['thunderstorm', 3, 'гроза'],
  ] as const)('%s %f mm → %s', (condition, precip, phrase) => {
    expect(describeSky(moment(NOW, { condition, precip })).phrase).toBe(phrase);
  });
});

describe('humidity vs the month norm', () => {
  const input = (humidity: number, wetOrFog = false) => ({
    humidity,
    climate: climate(75, 10),
    time: NOW,
    tz: TZ,
    wetOrFog,
  });

  test.each([
    [75, null],
    [68, null], // z -0.7
    [66, null], // z -0.9
    [65, 'суше обычного для ноября'], // z -1.0, gap 10: the boundary is inclusive
    [64, 'суше обычного для ноября'], // z -1.1, gap 11
    [50, 'очень сухо для ноября'], // z -2.5
    [84, null], // z +0.9
    [85, 'влажнее обычного для ноября'], // z +1.0, gap 10: the boundary is inclusive
    [86, 'влажнее обычного для ноября'], // z +1.1, gap 11
    [95, 'очень сыро для ноября'], // z +2.0
  ])('humidity %i → %p', (humidity, phrase) => {
    const d = describeHumidity(input(humidity), T);
    expect(d.phrase).toBe(phrase);
    expect(d.fired).toBe(phrase !== null);
  });

  test('damp air is not announced while it rains', () => {
    expect(describeHumidity(input(95, true), T).fired).toBe(false);
  });

  test('a wide norm needs a wider gap, a narrow one does not shout on small gaps', () => {
    // sd 20: 60 is only z -0.75 from 75 → silent. sd 2 floors at 3: gap 6 < 10 → silent.
    expect(describeHumidity({ ...input(60), climate: climate(75, 20) }, T).fired).toBe(false);
    expect(describeHumidity({ ...input(69), climate: climate(75, 2) }, T).fired).toBe(false);
  });

  test('silent without norms or without a reading', () => {
    expect(describeHumidity({ ...input(20), climate: null }, T).fired).toBe(false);
    expect(describeHumidity({ ...input(20), humidity: undefined }, T).fired).toBe(false);
  });

  test('the norm is the same month and hour: a humid night is normal, a humid noon is not', () => {
    const grid = climate(75, 8);
    for (const row of grid.humidity.mean) {
      row[2] = 92; // nights are damp
      row[14] = 55; // afternoons are dry
    }
    const night = at('2026-11-09', 2, 0);
    const noon = at('2026-11-09', 14, 0);
    expect(
      describeHumidity({ humidity: 92, climate: grid, time: night, tz: TZ, wetOrFog: false }, T)
        .fired,
    ).toBe(false);
    expect(
      describeHumidity({ humidity: 92, climate: grid, time: noon, tz: TZ, wetOrFog: false }, T)
        .fired,
    ).toBe(true);
  });

  test('never says a percentage', () => {
    for (const h of [10, 30, 95, 99]) {
      expect(describeHumidity(input(h), T).phrase ?? '').not.toMatch(/\d|%|процент/);
    }
  });
});

describe('temperature against the month norm', () => {
  test('warm and cold anomalies, silent inside the norm', () => {
    const c = climate(70, 10, 8, 3);
    expect(describeTempAnomaly(moment(NOW, { temp: 17 }), c, TZ, T).phrase).toBe(
      'для ноября необычно тепло',
    );
    expect(describeTempAnomaly(moment(NOW, { temp: -2 }), c, TZ, T).phrase).toBe(
      'для ноября необычно холодно',
    );
    expect(describeTempAnomaly(moment(NOW, { temp: 11 }), c, TZ, T).fired).toBe(false);
    expect(describeTempAnomaly(moment(NOW, { temp: 17 }), null, TZ, T).fired).toBe(false);
  });
});

describe('wind categories (m/s)', () => {
  test.each([
    [3, 5, 'none'],
    [5.9, 9.9, 'none'],
    [6, 0, 'breezy'],
    [2, 10, 'breezy'], // gusts alone
    [8.9, 13.9, 'breezy'],
    [9, 0, 'strong'],
    [4, 14, 'strong'],
    [12.9, 18.9, 'strong'],
    [13, 0, 'storm'],
    [5, 19, 'storm'],
  ] as const)('wind %f gust %f → %s', (wind, gust, level) => {
    expect(windLevel(wind, gust, T)).toBe(level);
  });

  test('silent when insignificant, never prints a number', () => {
    const d = describeWind({ wind: 3, gust: 5, wet: true }, T);
    expect(d.fired).toBe(false);
    expect(d.phrase).toBeNull();
  });

  test('umbrella-meaningful wording', () => {
    expect(describeWind({ wind: 7, wet: false }, T).phrase).toBe('ветрено');
    expect(describeWind({ wind: 7, wet: true }, T).phrase).toBe(
      'ветрено, зонт придётся держать крепко',
    );
    expect(describeWind({ wind: 10, wet: true }, T).phrase).toBe(
      'сильный ветер, зонт может вывернуть',
    );
    expect(describeWind({ wind: 15, wet: true }, T).phrase).toBe('штормовой ветер, зонт унесёт');
    expect(describeWind({ wind: 3, gust: 11, wet: false }, T).phrase).toBe('порывистый ветер');
  });
});

describe('lookahead: the next hour', () => {
  test('rain starting within the hour', () => {
    const b = bundle({
      now: NOW,
      hour: (_, start) =>
        start === at('2026-11-09', 15) ? { condition: 'rain', precip: 1.2, precipProb: 80 } : {},
    });
    expect(describeLookahead(b, NOW, T).phrase).toBe('через час начнётся дождь'); // +50 min
  });

  test.each([
    [at('2026-11-09', 14, 50), 'вот-вот начнётся дождь'],
    [at('2026-11-09', 14, 30), 'скоро начнётся дождь'],
    [at('2026-11-09', 14, 5), 'через час начнётся дождь'],
  ])('rain at 15:00 asked at %i', (now, phrase) => {
    const b = bundle({
      now,
      hour: (_, start) =>
        start === at('2026-11-09', 15) ? { condition: 'rain', precip: 1.2 } : {},
    });
    expect(describeLookahead(b, now, T).phrase).toBe(phrase);
  });

  test('rain ending within the hour', () => {
    const b = bundle({
      now: NOW,
      current: { condition: 'rain', precip: 1 },
      hour: (_, start) => (start <= NOW ? { condition: 'rain', precip: 1 } : {}),
    });
    expect(describeLookahead(b, NOW, T).phrase).toBe('дождь скоро закончится');
  });

  test('steady rain: nothing to report', () => {
    const b = bundle({
      now: NOW,
      current: { condition: 'rain', precip: 1 },
      hour: () => ({ condition: 'rain', precip: 1 }),
    });
    expect(describeLookahead(b, NOW, T).fired).toBe(false);
  });

  test('snow and thunder and heavy rain have their own words', () => {
    const start = at('2026-11-09', 15);
    const snow = bundle({
      now: NOW,
      current: { temp: -1 },
      hour: (_, s) => (s === start ? { condition: 'snow', precip: 1, temp: -1 } : {}),
    });
    expect(describeLookahead(snow, NOW, T).phrase).toContain('снег');
    const storm = bundle({
      now: NOW,
      hour: (_, s) => (s === start ? { condition: 'thunderstorm', precip: 3 } : {}),
    });
    expect(describeLookahead(storm, NOW, T).phrase).toBe('в ближайший час возможна гроза');
    const heavy = bundle({
      now: NOW,
      hour: (_, s) => (s === start ? { condition: 'heavy-rain', precip: 6 } : {}),
    });
    expect(describeLookahead(heavy, NOW, T).phrase).toContain('ливень');
  });

  test.each([
    [10, 12, false], // +2
    [10, 13, true], // +3 warms
    [10, 7, true], // −3 cools
    [10, 8, false],
  ])('temperature %i → %i within the hour fires %p', (now, next, fires) => {
    const b = bundle({
      now: NOW,
      current: { temp: now },
      hour: (_, s) => (s === at('2026-11-09', 15) ? { temp: next } : { temp: now }),
    });
    const d = describeLookahead(b, NOW, T);
    expect(d.fired).toBe(fires);
    if (fires) expect(d.phrase).toMatch(next > now ? /потеплеет/ : /похолодает/);
  });

  test('wind picking up to strong', () => {
    const b = bundle({
      now: NOW,
      current: { wind: 3 },
      hour: (_, s) => (s === at('2026-11-09', 15) ? { wind: 10 } : { wind: 3 }),
    });
    expect(describeLookahead(b, NOW, T).phrase).toBe('через час усилится ветер');
  });

  test('rain two hours away is not news', () => {
    const b = bundle({
      now: NOW,
      hour: (_, s) => (s >= at('2026-11-09', 16) ? { condition: 'rain', precip: 2 } : {}),
    });
    expect(describeLookahead(b, NOW, T).fired).toBe(false);
  });
});

describe('tomorrow', () => {
  const tomorrow = (offset: number) =>
    offset === 1 ? { max: 14, min: 6, condition: 'rain' as const, precip: 4 } : { max: 9, min: 4 };

  test.each([
    [at('2026-11-09', 20, 59), false],
    [at('2026-11-09', 21, 0), true],
    [at('2026-11-09', 23, 59), true],
    [at('2026-11-09', 12, 0), false],
  ])('asked at %i fires %p', (now, fires) => {
    expect(describeTomorrow(bundle({ now, day: tomorrow }), now, T, false).fired).toBe(fires);
  });

  test('forced always answers, with shift against today', () => {
    const b = bundle({ now: NOW, day: tomorrow });
    expect(describeTomorrow(b, NOW, T, true).phrase).toBe(
      'завтра днём 14, ночью 6 градусов, дождь, на 5 градусов теплее, чем сегодня',
    );
  });

  test('late-evening threshold is configurable', () => {
    const now = at('2026-11-09', 19, 0);
    expect(
      describeTomorrow(bundle({ now, day: tomorrow }), now, withThresholds({ lateHour: 18 }), false)
        .fired,
    ).toBe(true);
  });

  test('DST: on the eve of the spring change tomorrow is the 29th, not the 30th', () => {
    const now = at('2026-03-28', 23, 30);
    const b = bundle({
      now,
      day: (offset) => (offset === 1 ? { max: 20, min: 9 } : { max: 10, min: 3 }),
    });
    expect(b.daily[1]?.date).toBe('2026-03-29');
    const d = describeTomorrow(b, now, T, false);
    expect(d.fired).toBe(true);
    expect(d.phrase).toContain('днём 20');
  });

  test('wind and sub-zero values read correctly', () => {
    const b = bundle({
      now: NOW,
      day: (o) =>
        o === 1
          ? { max: -1, min: -6, condition: 'snow', precip: 3, snow: 4, windMax: 10 }
          : { max: 5, min: 0 },
    });
    expect(describeTomorrow(b, NOW, T, true).phrase).toBe(
      'завтра днём минус 1, ночью минус 6 градусов, снег, сильный ветер, на 6 градусов холоднее, чем сегодня',
    );
  });
});

describe('week insights', () => {
  const kinds = (shape: Parameters<typeof bundle>[0]['day']) =>
    weekInsights(bundle({ now: NOW, day: shape }), NOW, T).map((i) => `${i.kind}@${i.offset}`);

  test('quiet week: nothing to say', () => {
    expect(kinds(() => ({}))).toEqual([]);
    expect(describeInsight(bundle({ now: NOW }), NOW, T).fired).toBe(false);
  });

  test('warming: needs ≥ 5° above the level of today and tomorrow', () => {
    expect(kinds((o) => ({ max: o >= 4 ? 16 : 11 }))).toEqual(['warming@4']);
    expect(kinds((o) => ({ max: o >= 4 ? 15 : 11 }))).toEqual([]); // +4: not enough
    const b = bundle({ now: NOW, day: (o) => ({ max: o >= 4 ? 17 : 11 }) });
    expect(weekInsights(b, NOW, T)[0]?.phrase).toBe('в пятницу потеплеет до 17 градусов');
  });

  test('cooling with the weekday spoken in the right case', () => {
    const b = bundle({ now: NOW, day: (o) => ({ max: o >= 3 ? 4 : 12 }) });
    expect(weekInsights(b, NOW, T)[0]?.phrase).toBe('с четверга похолодает, днём всего 4 градуса');
  });

  test('snow onset, rain onset, rain end', () => {
    expect(
      weekInsights(
        bundle({
          now: NOW,
          day: (o) =>
            o === 3
              ? { condition: 'snow', precip: 4, snow: 5, max: 1, min: -2 }
              : { max: 1, min: 1 },
        }),
        NOW,
        T,
      )[0]?.phrase,
    ).toBe('в четверг пойдёт снег');
    expect(kinds((o) => (o >= 2 && o <= 3 ? { condition: 'rain', precip: 5 } : {}))).toContain(
      'rain-onset@2',
    );
    const b = bundle({ now: NOW, day: (o) => (o <= 1 ? { condition: 'rain', precip: 6 } : {}) });
    const end = weekInsights(b, NOW, T).find((i) => i.kind === 'rain-end');
    expect(end?.phrase).toBe('дожди закончатся к послезавтрашнему дню');
  });

  test('rain then snow then dry: the spell is «осадки», not «дожди»', () => {
    const b = bundle({
      now: NOW,
      day: (o) =>
        o === 0
          ? { condition: 'rain', precip: 5 }
          : o === 1
            ? { condition: 'snow', precip: 5, snow: 4 }
            : {},
    });
    expect(weekInsights(b, NOW, T).find((i) => i.kind === 'rain-end')?.phrase).toBe(
      'осадки закончатся к послезавтрашнему дню',
    );
  });

  test('one rainy day followed by dry days: «дождь закончится уже завтра»', () => {
    const b = bundle({ now: NOW, day: (o) => (o === 0 ? { condition: 'rain', precip: 6 } : {}) });
    expect(weekInsights(b, NOW, T).find((i) => i.kind === 'rain-end')?.phrase).toBe(
      'дождь закончится уже завтра',
    );
  });

  test('frost arriving after mild nights, hard frost, heat', () => {
    expect(
      weekInsights(
        bundle({ now: NOW, day: (o) => (o === 2 ? { min: -2 } : { min: 4 }) }),
        NOW,
        T,
      )[0]?.phrase,
    ).toBe('послезавтра ночью заморозки до минус 2 градусов');
    expect(
      weekInsights(
        bundle({ now: NOW, day: (o) => (o === 3 ? { min: -12 } : { min: 4 }) }),
        NOW,
        T,
      )[0]?.phrase,
    ).toContain('сильный мороз');
    expect(
      weekInsights(
        bundle({ now: NOW, day: (o) => (o === 5 ? { max: 36 } : { max: 22 }) }),
        NOW,
        T,
      ).find((i) => i.kind === 'heat')?.phrase,
    ).toBe('в субботу сильная жара до 36 градусов');
  });

  test('already freezing today is not news', () => {
    expect(kinds(() => ({ min: -3 }))).not.toContain('frost@1');
  });

  test('week summary has a range and the wet-day count', () => {
    const b = bundle({
      now: NOW,
      day: (o) => ({
        min: 2 + o,
        max: 8 + o,
        ...(o === 2 || o === 3 ? { precip: 3, condition: 'rain' as const } : {}),
      }),
    });
    expect(describeWeekSummary(b, NOW, T).phrase).toBe(
      'на неделе от 2 до 15 градусов, дождь в 2 из 8 дней',
    );
  });
});

describe('advice', () => {
  const wetFrom = (hour: number) => (_: number, start: number) =>
    start >= at('2026-11-09', hour) ? { condition: 'rain' as const, precip: 1.5 } : {};

  test('umbrella: wet now, wet later, dry, snow, storm wind', () => {
    const now = bundle({
      now: NOW,
      current: { condition: 'rain', precip: 1 },
      hour: (i, s) => (s < at('2026-11-09', 17) ? { condition: 'rain', precip: 1 } : {}),
    });
    expect(describeUmbrella(now, NOW, T).phrase).toBe('Да, сейчас идёт дождь, закончится вечером.');
    expect(describeUmbrella(bundle({ now: NOW, hour: wetFrom(18) }), NOW, T).phrase).toBe(
      'Да, вечером будет дождь.',
    );
    expect(describeUmbrella(bundle({ now: NOW, hour: wetFrom(15) }), NOW, T).phrase).toBe(
      'Да, скоро начнётся дождь.',
    );
    expect(describeUmbrella(bundle({ now: NOW }), NOW, T).phrase).toBe('Нет, дождя не ожидается.');
    const windy = bundle({
      now: NOW,
      current: { condition: 'rain', precip: 2, wind: 11 },
      hour: () => ({ condition: 'rain', precip: 2 }),
    });
    expect(describeUmbrella(windy, NOW, T).phrase).toContain('зонт унесёт');
    const snow = bundle({
      now: NOW,
      current: { temp: -2 },
      hour: (_, s) =>
        s >= at('2026-11-09', 18) ? { condition: 'snow', precip: 1, temp: -2 } : { temp: -2 },
    });
    expect(describeUmbrella(snow, NOW, T).phrase).toContain('Зонт не нужен');
  });

  test('late evening with a wet tomorrow adds a note', () => {
    const now = at('2026-11-09', 22, 0);
    const b = bundle({ now, day: (o) => (o === 1 ? { precip: 5, condition: 'rain' } : {}) });
    expect(describeUmbrella(b, now, T).phrase).toBe(
      'Нет, дождя не ожидается. Завтра зонт пригодится.',
    );
  });

  test('now-mode hint: only when wet, not when the wind already covers the umbrella', () => {
    expect(describeAdvice(bundle({ now: NOW }), NOW, T).fired).toBe(false);
    expect(
      describeAdvice(bundle({ now: NOW, current: { condition: 'rain', precip: 1 } }), NOW, T)
        .phrase,
    ).toBe('возьми зонт');
    expect(
      describeAdvice(
        bundle({ now: NOW, current: { condition: 'rain', precip: 1, wind: 11 } }),
        NOW,
        T,
      ).fired,
    ).toBe(false);
  });

  test.each([
    [-20, 'Очень холодно'],
    [-8, 'Мороз'],
    [0, 'Холодно'],
    [7, 'Прохладно'],
    [14, 'Свежо'],
    [20, 'Тепло: хватит'],
    [25, 'Тепло: футболка'],
    [31, 'Жарко'],
    [37, 'Очень жарко'],
  ])('dress at %i° → %s…', (temp, start) => {
    expect(
      describeDress(bundle({ now: NOW, current: { temp, feels: temp } }), NOW, T).phrase,
    ).toStartWith(start);
  });

  test('dress uses the felt temperature, warns about an evening drop and rain', () => {
    const b = bundle({
      now: NOW,
      current: { temp: 12, feels: 6 },
      hour: (_, s) => ({
        temp: s >= at('2026-11-09', 19) ? 4 : 12,
        ...(s >= at('2026-11-09', 15) ? { condition: 'rain' as const, precip: 1 } : {}),
      }),
    });
    const phrase = describeDress(b, NOW, T).phrase ?? '';
    expect(phrase).toStartWith('Прохладно');
    expect(phrase).toContain('Вечером станет 4 градуса, захвати что-то тёплое.');
    expect(phrase).toContain('Скоро начнётся дождь.');
  });
});
