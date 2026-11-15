/**
 * @file  Decisions about the current air: the spoken temperature, the feels-like clause, the sky.
 *
 * Accessed via: `say()` for the "now" mode (compose.ts) and the `explain` command.
 *
 * Assumptions: the feels-like gap is judged on WHOLE spoken degrees (7 vs 1 is a 6° gap), so
 * a listener never hears «7, ощущается как 1» for a rule that saw 5.6. Boundary: gap ≥ feelsGap fires.
 */

import { precipKindOf } from '../conditions';
import { degrees, signed } from '../ru';
import type { Thresholds } from '../thresholds';
import type { Decision, Moment } from '../types';
import { apparentTemperature, spoken } from '../units';

export function describeTemperature(current: Moment): Decision {
  const value = spoken(current.temp);
  return {
    id: 'temperature',
    fired: true,
    phrase: degrees(value),
    weight: 100,
    why: `air ${current.temp.toFixed(1)}° → spoken ${value}`,
  };
}

/** The provider's apparent temperature, else Steadman from temp/humidity/wind, else unknown. */
export function feelsLike(moment: Moment): number | null {
  if (moment.feels !== undefined) return moment.feels;
  if (moment.humidity !== undefined && moment.wind !== undefined) {
    return apparentTemperature(moment.temp, moment.humidity, moment.wind);
  }
  return null;
}

export function describeFeels(current: Moment, thresholds: Thresholds): Decision {
  const feels = feelsLike(current);
  if (feels === null) {
    return {
      id: 'feels',
      fired: false,
      phrase: null,
      weight: 70,
      why: 'no apparent temperature and not enough data to compute it',
    };
  }
  const air = spoken(current.temp);
  const felt = spoken(feels);
  const gap = felt - air;
  const why = `air ${air}°, feels ${felt}° → gap ${gap}° (needs ≥ ${thresholds.feelsGap}° either way)`;
  if (Math.abs(gap) < thresholds.feelsGap) {
    return { id: 'feels', fired: false, phrase: null, weight: 70, why };
  }
  const wind = current.wind ?? 0;
  let phrase: string;
  if (gap < 0 && wind >= thresholds.wind.strong) {
    phrase = `дует — ощущается как ${signed(felt)}`;
  } else if (gap < 0 && wind >= thresholds.wind.breezy) {
    phrase = `на ветру ощущается как ${signed(felt)}`;
  } else if (gap > 0 && (current.humidity ?? 0) >= 60 && air >= 22) {
    phrase = `из-за влажности ощущается как ${signed(felt)}`;
  } else {
    phrase = `ощущается как ${signed(felt)}`;
  }
  return { id: 'feels', fired: true, phrase, weight: 70, why };
}

export function describeSky(current: Moment): Decision {
  const mm = current.precip ?? 0;
  let phrase: string;
  switch (current.condition) {
    case 'clear':
      phrase = 'ясно';
      break;
    case 'partly-cloudy':
      phrase = 'облачно с прояснениями';
      break;
    case 'cloudy':
      phrase = 'пасмурно';
      break;
    case 'fog':
      phrase = 'туман';
      break;
    case 'drizzle':
      phrase = 'моросит';
      break;
    case 'rain':
      phrase = mm >= 4 ? 'сильный дождь' : mm > 0 && mm < 1 ? 'небольшой дождь' : 'идёт дождь';
      break;
    case 'heavy-rain':
      phrase = 'льёт сильный дождь';
      break;
    case 'thunderstorm':
      phrase = 'гроза';
      break;
    case 'snow':
      phrase = mm >= 2 ? 'идёт сильный снег' : 'идёт снег';
      break;
    case 'heavy-snow':
      phrase = 'сильный снегопад';
      break;
    case 'sleet':
      phrase = 'мокрый снег';
      break;
    case 'hail':
      phrase = 'град';
      break;
  }
  return {
    id: 'sky',
    fired: true,
    phrase,
    weight: 90,
    why: `condition ${current.condition} (${precipKindOf(current.condition)})`,
  };
}
