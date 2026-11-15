/**
 * @file  Practical answers built on the same data: umbrella, what to wear, and the optional
 * one-line hint appended to a "now" report.
 *
 * Accessed via: `say()` modes `umbrella` and `dress`; `describeAdvice` in the "now" mode.
 *
 * Assumptions: the outlook looks 12 hours ahead (a person asking "do I need an umbrella"
 * means today). A strong wind turns "take an umbrella" into "it will be carried away".
 * Clothing is chosen from the felt temperature, rounded, never from the raw air value.
 */

import { isWet, type WetKind, wetKind } from '../conditions';
import { capitalize, dayPartOn, degrees, signed } from '../ru';
import type { Thresholds } from '../thresholds';
import { localParts } from '../time';
import type { Bundle, Decision, Moment } from '../types';
import { spoken } from '../units';
import { daysFrom } from './daily';
import { feelsLike } from './temperature';
import { windLevel, type WindLevel, windLevelRank } from './wind';

const MINUTE = 60_000;
const HORIZON_HOURS = 12;

interface Outlook {
  wetNow: boolean;
  /** What falls now (if wet now) or at the first wet hour. */
  kind: WetKind | null;
  /** Minutes until the first wet hour (0 when wet now); null when dry through the horizon. */
  startsInMinutes: number | null;
  startHour: number | null;
  /** Wet now and dry again later within the horizon: minutes until it clears. */
  endsInMinutes: number | null;
  endHour: number | null;
  /** The wind that would meet the umbrella (now, or at the first wet hour). */
  wind: WindLevel;
}

function outlook(bundle: Bundle, now: number, thresholds: Thresholds): Outlook {
  const tz = bundle.place.tz;
  const horizon = now + HORIZON_HOURS * 60 * MINUTE;
  const hours = bundle.hourly.filter((m) => m.time > now - 30 * MINUTE && m.time <= horizon);
  const wetNow = isWet(bundle.current);
  const firstWet = hours.find((m) => isWet(m));
  const clears = wetNow
    ? hours.find(
        (m) => m.time > now && hours.filter((h) => h.time >= m.time).every((h) => !isWet(h)),
      )
    : undefined;
  const meets: Moment = wetNow ? bundle.current : (firstWet ?? bundle.current);
  return {
    wetNow,
    kind: wetNow ? wetKind(bundle.current) : firstWet ? wetKind(firstWet) : null,
    startsInMinutes: wetNow
      ? 0
      : firstWet
        ? Math.max(0, Math.round((firstWet.time - now) / MINUTE))
        : null,
    startHour: firstWet ? localParts(firstWet.time, tz).hour : null,
    endsInMinutes: clears ? Math.round((clears.time - now) / MINUTE) : null,
    endHour: clears ? localParts(clears.time, tz).hour : null,
    wind: windLevel(meets.wind, meets.gust, thresholds),
  };
}

const windy = (level: WindLevel): boolean => windLevelRank(level) >= windLevelRank('strong');

export function describeUmbrella(bundle: Bundle, now: number, thresholds: Thresholds): Decision {
  const o = outlook(bundle, now, thresholds);
  const id = 'umbrella';
  const base = { id, fired: true, weight: 100 } as const;
  const why = `wet now ${o.wetNow}, first wet in ${o.startsInMinutes ?? '—'} min, clears in ${o.endsInMinutes ?? '—'} min, wind ${o.wind}`;

  let phrase: string;
  if (o.wetNow) {
    const snow = o.kind === 'snow';
    const noun = snow ? 'снег' : o.kind === 'mixed' ? 'мокрый снег' : 'дождь';
    const until =
      o.endsInMinutes === null
        ? 'и скоро не закончится'
        : o.endsInMinutes <= 90
          ? 'но скоро закончится'
          : `закончится ${dayPartOn(o.endHour ?? 0)}`;
    if (windy(o.wind) && !snow) {
      phrase = `Идёт ${noun}, а ветер сильный: зонт унесёт, лучше куртка с капюшоном.`;
    } else if (snow) {
      phrase = `Идёт снег, зонт не обязателен, но ${until === 'и скоро не закончится' ? 'надолго' : until}.`;
    } else {
      phrase = `Да, сейчас идёт ${noun === 'дождь' ? 'дождь' : noun}, ${until}.`;
    }
  } else if (o.startsInMinutes !== null) {
    const when =
      o.startsInMinutes <= 20
        ? 'вот-вот'
        : o.startsInMinutes <= 90
          ? 'скоро'
          : dayPartOn(o.startHour ?? 0);
    if (o.kind === 'snow' || o.kind === 'mixed') {
      phrase = `Зонт не нужен, но ${when} пойдёт ${o.kind === 'snow' ? 'снег' : 'мокрый снег'}.`;
    } else if (windy(o.wind)) {
      phrase = `${dayPartOn(o.startHour ?? 0)} будет дождь, но ветер сильный: зонт унесёт.`;
    } else {
      phrase = `Да, ${when} ${o.startsInMinutes <= 90 ? 'начнётся' : 'будет'} дождь.`;
    }
  } else {
    phrase = 'Нет, дождя не ожидается.';
    const next = daysFrom(bundle, now).find((d) => d.offset === 1)?.day;
    const late = localParts(now, bundle.place.tz).hour >= thresholds.lateHour;
    if (late && next && next.precip >= thresholds.week.wetDayMm) {
      phrase += ' Завтра зонт пригодится.';
    }
    return { ...base, phrase, why };
  }
  return { ...base, phrase, why };
}

/** The "now" report's optional umbrella hint: only when it is wet or about to be, and the wind is not the story. */
export function describeAdvice(bundle: Bundle, now: number, thresholds: Thresholds): Decision {
  const o = outlook(bundle, now, thresholds);
  const soon =
    o.startsInMinutes !== null && o.startsInMinutes <= thresholds.lookahead.windowMinutes;
  const why = `wet now ${o.wetNow}, starts in ${o.startsInMinutes ?? '—'} min, wind ${o.wind}`;
  if (!o.wetNow && !soon) {
    return { id: 'advice', fired: false, phrase: null, weight: 45, why };
  }
  if (windy(o.wind)) {
    return {
      id: 'advice',
      fired: false,
      phrase: null,
      weight: 45,
      why: `${why}; wind phrase already covers the umbrella`,
    };
  }
  if (o.kind === 'snow') {
    return { id: 'advice', fired: true, phrase: 'обувь лучше непромокаемую', weight: 45, why };
  }
  return { id: 'advice', fired: true, phrase: 'возьми зонт', weight: 45, why };
}

interface Outfit {
  upTo: number;
  label: string;
  wear: string;
}

const OUTFITS: Outfit[] = [
  { upTo: -15, label: 'Очень холодно', wear: 'зимний пуховик, шапка, шарф и перчатки' },
  { upTo: -5, label: 'Мороз', wear: 'тёплая зимняя куртка, шапка и перчатки' },
  { upTo: 3, label: 'Холодно', wear: 'зимняя куртка и шапка' },
  { upTo: 10, label: 'Прохладно', wear: 'тёплая куртка и шарф' },
  { upTo: 16, label: 'Свежо', wear: 'лёгкая куртка или толстовка' },
  { upTo: 22, label: 'Тепло', wear: 'хватит кофты или лёгкой рубашки' },
  { upTo: 28, label: 'Тепло', wear: 'футболка и лёгкие брюки' },
  { upTo: 33, label: 'Жарко', wear: 'лёгкая одежда, не забудь воду и головной убор' },
  {
    upTo: Infinity,
    label: 'Очень жарко',
    wear: 'самая лёгкая одежда, вода и головной убор, лучше переждать жару в тени',
  },
];

export function describeDress(bundle: Bundle, now: number, thresholds: Thresholds): Decision {
  const felt = spoken(feelsLike(bundle.current) ?? bundle.current.temp);
  const outfit = OUTFITS.find((o) => felt <= o.upTo) ?? (OUTFITS.at(-1) as Outfit);
  const sentences = [`${outfit.label}: ${outfit.wear}.`];
  const notes: string[] = [`felt ${felt}° → ${outfit.label}`];

  // A big drop within the next hours changes what to take along.
  const ahead = bundle.hourly.filter((m) => m.time > now && m.time <= now + 8 * 60 * MINUTE);
  const coldest = ahead.reduce<Moment | null>(
    (low, m) => (low === null || m.temp < low.temp ? m : low),
    null,
  );
  if (coldest && spoken(coldest.temp) <= spoken(bundle.current.temp) - 5) {
    const when = dayPartOn(localParts(coldest.time, bundle.place.tz).hour);
    sentences.push(
      `${capitalize(when)} станет ${degrees(spoken(coldest.temp))}, захвати что-то тёплое.`,
    );
    notes.push(`drop to ${spoken(coldest.temp)}° ${when}`);
  }

  const umbrella = describeUmbrella(bundle, now, thresholds);
  const needsUmbrella = !umbrella.phrase?.startsWith('Нет');
  if (needsUmbrella && umbrella.phrase) {
    sentences.push(capitalize(umbrella.phrase.replace(/^Да, /, '')));
    notes.push(`umbrella: ${umbrella.why}`);
  }
  return {
    id: 'dress',
    fired: true,
    phrase: sentences.join(' '),
    weight: 100,
    why: `${notes.join('; ')} (temp ${signed(felt)})`,
  };
}
