/**
 * Regenerates examples/*.json: synthetic but realistic bundles for the README commands.
 * Run: bun scripts/make-examples.ts
 */
import { writeFile } from 'node:fs/promises';

import { at, bundle } from '../tests/fixtures';

const examples = {
  // Wet, windy autumn evening: the wind decides the umbrella; tomorrow is added after 21:00.
  'rain-wind-evening': bundle({
    now: at('2026-11-09', 21, 40),
    current: {
      temp: 8.3,
      feels: 3.1,
      humidity: 93,
      wind: 10.5,
      gust: 15,
      condition: 'rain',
      precip: 1.2,
    },
    hour: (_, start) => ({
      condition: start < at('2026-11-09', 23) ? 'rain' : 'cloudy',
      precip: start < at('2026-11-09', 23) ? 1.2 : 0,
      wind: 10,
      gust: 15,
    }),
    day: (o) =>
      o === 1 ? { max: 11, min: 6, condition: 'cloudy', precip: 0.4 } : { max: 10, min: 5 },
  }),
  // Dry, calm and warm in the middle of the day: almost nothing to say.
  'calm-noon': bundle({
    now: at('2026-10-12', 12, 15),
    current: { temp: 17.6, humidity: 55, wind: 2, condition: 'clear' },
    day: () => ({ max: 18, min: 8, condition: 'clear' }),
  }),
  // Rain turns to snow during the week, then a cold snap.
  'snow-week': bundle({
    now: at('2026-12-01', 9, 0),
    current: { temp: 3.2, feels: 1, humidity: 85, wind: 3, condition: 'cloudy' },
    day: (o) =>
      o <= 1
        ? { max: 4, min: 1, condition: 'rain', precip: 5 }
        : o === 2
          ? { max: 1, min: -2, condition: 'snow', precip: 6, snow: 5 }
          : { max: -3, min: -9, condition: 'cloudy' },
  }),
  // A cold snap with a wind: what it feels like matters more than the air.
  'windy-cold-morning': bundle({
    now: at('2026-12-15', 8, 0),
    current: {
      temp: 2,
      feels: -6.5,
      humidity: 70,
      wind: 9.5,
      gust: 14,
      condition: 'partly-cloudy',
    },
    hour: () => ({ temp: 2, wind: 9.5, gust: 14 }),
    day: () => ({ max: 3, min: -1 }),
  }),
};

for (const [name, value] of Object.entries(examples)) {
  await writeFile(`examples/${name}.json`, `${JSON.stringify(value, null, 2)}\n`);
}
