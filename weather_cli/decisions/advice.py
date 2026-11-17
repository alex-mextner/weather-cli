"""Practical answers built on the same data: umbrella, what to wear, and the optional one-line
hint appended to a "now" report.

Accessed via: `say()` modes `umbrella` and `dress`; `describe_advice` in the "now" mode.

Assumptions: the outlook looks 12 hours ahead (a person asking "do I need an umbrella"
means today). A strong wind turns "take an umbrella" into "it will be carried away".
Clothing is chosen from the felt temperature, rounded, never from the raw air value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from weather_cli.conditions import PrecipNow, WetKind, current_precipitation, is_wet, wet_kind
from weather_cli.decisions.daily import days_from
from weather_cli.decisions.temperature import feels_like
from weather_cli.decisions.wind import WindLevel, wind_level, wind_level_rank
from weather_cli.horizon import hours_ahead
from weather_cli.local_time import local_parts
from weather_cli.providers.json import js_number
from weather_cli.ru import capitalize, day_part_on, degrees, signed
from weather_cli.thresholds import Thresholds
from weather_cli.types import Bundle, Decision, Moment
from weather_cli.units import js_round, spoken

_MINUTE = 60_000


@dataclass(frozen=True, slots=True)
class _Outlook:
    precipitation: PrecipNow
    wet_now: bool
    # What falls now (if wet now) or at the first wet hour.
    kind: WetKind | None
    # Minutes until the first wet hour (0 when wet now); None when dry through the horizon.
    starts_in_minutes: int | None
    start_hour: int | None
    # Wet now and dry again later within the horizon: minutes until it clears.
    ends_in_minutes: int | None
    end_hour: int | None
    # The wind that would meet the umbrella (now, or at the first wet hour).
    wind: WindLevel
    # No forecast hour covers the horizon (the request failed, answered empty, or the bundle is
    # so old its hours have passed): "dry" and "won't stop" would be claims from missing data.
    no_forecast: bool = False


def _clearing_hour(hours: list[Moment], now: float) -> Moment | None:
    """The first hour after which every remaining hour of the horizon is dry."""
    return next(
        (
            m
            for m in hours
            if m.time > now and all(not is_wet(h) for h in hours if h.time >= m.time)
        ),
        None,
    )


def _starts_in(wet_now: bool, first_wet: Moment | None, now: float) -> int | None:
    if wet_now:
        return 0
    if first_wet is None:
        return None
    return max(0, js_round((first_wet.time - now) / _MINUTE))


def _outlook(bundle: Bundle, now: float, thresholds: Thresholds) -> _Outlook:
    tz = bundle.place.tz
    hours = hours_ahead(bundle.hourly, now)
    precipitation = current_precipitation(bundle)
    wet_now = precipitation.wet
    first_wet = next((m for m in hours if is_wet(m)), None)
    clears = _clearing_hour(hours, now) if wet_now else None
    if wet_now:
        meets, kind = bundle.current, precipitation.kind or wet_kind(bundle.current)
    elif first_wet is not None:
        meets, kind = first_wet, wet_kind(first_wet)
    else:
        meets, kind = bundle.current, None
    return _Outlook(
        precipitation=precipitation,
        wet_now=wet_now,
        kind=kind,
        starts_in_minutes=_starts_in(wet_now, first_wet, now),
        start_hour=None if first_wet is None else local_parts(first_wet.time, tz).hour,
        ends_in_minutes=None if clears is None else js_round((clears.time - now) / _MINUTE),
        end_hour=None if clears is None else local_parts(clears.time, tz).hour,
        wind=wind_level(meets.wind, meets.gust, thresholds),
        no_forecast=not hours,
    )


def _windy(level: WindLevel) -> bool:
    return wind_level_rank(level) >= wind_level_rank("strong")


def _note(o: _Outlook) -> str:
    note = o.precipitation.note()
    return f"; {note}" if note else ""


def _minutes(value: int | None) -> str:
    return "—" if value is None else js_number(value)


_NOUN: dict[WetKind, str] = {"rain": "дождь", "snow": "снег", "mixed": "мокрый снег"}


@dataclass(frozen=True, slots=True)
class UmbrellaAdvice:
    """The umbrella answer as data, so callers never have to read the wording back."""

    # Anything precipitating now or within the horizon; False only for "no rain expected".
    needed: bool
    phrase: str
    # The same statement without the leading «Да, », for composing into longer answers.
    statement: str
    why: str


def _until_phrase(o: _Outlook) -> str:
    if o.no_forecast:
        return "когда закончится, неизвестно"
    if o.ends_in_minutes is None:
        return "и скоро не закончится"
    if o.ends_in_minutes <= 90:
        return "но скоро закончится"
    return f"закончится {day_part_on(o.end_hour or 0)}"


def _wet_now_statement(o: _Outlook) -> tuple[str, bool]:
    """(statement, affirmative): affirmative statements are spoken with a leading «Да, »."""
    noun = _NOUN[o.kind or "rain"]
    if o.kind == "snow":
        # `_until_phrase` already opens with «но» for a near end; never speak it twice.
        tail = "но надолго" if o.ends_in_minutes is None else _until_phrase(o)
        if o.no_forecast:
            tail = f"а {_until_phrase(o)}"
        elif not tail.startswith("но "):
            tail = f"но {tail}"
        return f"Идёт снег, зонт не обязателен, {tail}.", False
    if _windy(o.wind):
        return f"Идёт {noun}, а ветер сильный: зонт унесёт, лучше куртка с капюшоном.", False
    return f"сейчас идёт {noun}, {_until_phrase(o)}.", True


def _when_phrase(starts_in: int, start_hour: int | None) -> str:
    if starts_in <= 20:
        return "вот-вот"
    return "скоро" if starts_in <= 90 else day_part_on(start_hour or 0)


def _upcoming_statement(o: _Outlook, starts_in: int) -> tuple[str, bool]:
    when = _when_phrase(starts_in, o.start_hour)
    if o.kind in ("snow", "mixed"):
        return f"Зонт не нужен, но {when} пойдёт {_NOUN[o.kind]}.", False
    if _windy(o.wind):
        return (
            f"{day_part_on(o.start_hour or 0)} будет дождь, но ветер сильный: зонт унесёт.",
            False,
        )
    return f"{when} {'начнётся' if starts_in <= 90 else 'будет'} дождь.", True


def _dry_phrase(bundle: Bundle, now: float, thresholds: Thresholds) -> str:
    phrase = "Нет, дождя не ожидается."
    tomorrow = next((e.day for e in days_from(bundle, now) if e.offset == 1), None)
    late = local_parts(now, bundle.place.tz).hour >= thresholds.late_hour
    if late and tomorrow is not None and tomorrow.precip >= thresholds.week.wet_day_mm:
        phrase += " Завтра зонт пригодится."
    return phrase


def umbrella_advice(bundle: Bundle, now: float, thresholds: Thresholds) -> UmbrellaAdvice:
    o = _outlook(bundle, now, thresholds)
    why = (
        f"wet now {'true' if o.wet_now else 'false'}, "
        f"first wet in {_minutes(o.starts_in_minutes)} min, "
        f"clears in {_minutes(o.ends_in_minutes)} min, wind {o.wind}"
    ) + _note(o)
    if not o.wet_now and o.starts_in_minutes is None:
        if o.no_forecast:
            unknown = "Сейчас без дождя, но прогноза на ближайшие часы нет."
            return UmbrellaAdvice(needed=False, phrase=unknown, statement=unknown, why=why)
        dry = _dry_phrase(bundle, now, thresholds)
        return UmbrellaAdvice(needed=False, phrase=dry, statement=dry, why=why)
    if o.wet_now:
        statement, affirmative = _wet_now_statement(o)
    else:
        statement, affirmative = _upcoming_statement(o, o.starts_in_minutes or 0)
    phrase = f"Да, {statement}" if affirmative else statement
    return UmbrellaAdvice(needed=True, phrase=phrase, statement=statement, why=why)


def describe_umbrella(bundle: Bundle, now: float, thresholds: Thresholds) -> Decision:
    advice = umbrella_advice(bundle, now, thresholds)
    return Decision(id="umbrella", fired=True, phrase=advice.phrase, weight=100, why=advice.why)


def describe_advice(bundle: Bundle, now: float, thresholds: Thresholds) -> Decision:
    """The "now" report's optional umbrella hint.

    Only when it is wet or about to be, and the wind is not the story.
    """
    o = _outlook(bundle, now, thresholds)
    soon = (
        o.starts_in_minutes is not None
        and o.starts_in_minutes <= thresholds.lookahead.window_minutes
    )
    why = (
        f"wet now {'true' if o.wet_now else 'false'}, "
        f"starts in {_minutes(o.starts_in_minutes)} min, wind {o.wind}"
    ) + _note(o)
    if not o.wet_now and not soon:
        return Decision(id="advice", fired=False, phrase=None, weight=45, why=why)
    if _windy(o.wind):
        covered = f"{why}; wind phrase already covers the umbrella"
        return Decision(id="advice", fired=False, phrase=None, weight=45, why=covered)
    phrase = "обувь лучше непромокаемую" if o.kind == "snow" else "возьми зонт"
    return Decision(id="advice", fired=True, phrase=phrase, weight=45, why=why)


@dataclass(frozen=True, slots=True)
class _Outfit:
    up_to: float
    label: str
    wear: str


_OUTFITS = (
    _Outfit(-15, "Очень холодно", "зимний пуховик, шапка, шарф и перчатки"),
    _Outfit(-5, "Мороз", "тёплая зимняя куртка, шапка и перчатки"),
    _Outfit(3, "Холодно", "зимняя куртка и шапка"),
    _Outfit(10, "Прохладно", "тёплая куртка и шарф"),
    _Outfit(16, "Свежо", "лёгкая куртка или толстовка"),
    _Outfit(22, "Тепло", "хватит кофты или лёгкой рубашки"),
    _Outfit(28, "Тепло", "футболка и лёгкие брюки"),
    _Outfit(33, "Жарко", "лёгкая одежда, не забудь воду и головной убор"),
    _Outfit(
        math.inf,
        "Очень жарко",
        "самая лёгкая одежда, вода и головной убор, лучше переждать жару в тени",
    ),
)


def _outfit_for(felt: float) -> _Outfit:
    return next((o for o in _OUTFITS if felt <= o.up_to), _OUTFITS[-1])


def _drop_warning(bundle: Bundle, now: float) -> tuple[str, str] | None:
    """A big drop within the next hours changes what to take along: (sentence, note)."""
    ahead = [m for m in bundle.hourly if now < m.time <= now + 8 * 60 * _MINUTE]
    if not ahead:
        return None
    coldest = min(ahead, key=lambda m: m.temp)
    if spoken(coldest.temp) > spoken(bundle.current.temp) - 5:
        return None
    when = day_part_on(local_parts(coldest.time, bundle.place.tz).hour)
    low = spoken(coldest.temp)
    sentence = f"{capitalize(when)} станет {degrees(low)}, захвати что-то тёплое."
    return sentence, f"drop to {js_number(low)}° {when}"


def describe_dress(bundle: Bundle, now: float, thresholds: Thresholds) -> Decision:
    felt_raw = feels_like(bundle.current)
    felt = spoken(bundle.current.temp if felt_raw is None else felt_raw)
    outfit = _outfit_for(felt)
    sentences = [f"{outfit.label}: {outfit.wear}."]
    notes = [f"felt {js_number(felt)}° → {outfit.label}"]

    drop = _drop_warning(bundle, now)
    if drop is not None:
        sentences.append(drop[0])
        notes.append(drop[1])

    umbrella = umbrella_advice(bundle, now, thresholds)
    if umbrella.needed:
        sentences.append(capitalize(umbrella.statement))
        notes.append(f"umbrella: {umbrella.why}")
    return Decision(
        id="dress",
        fired=True,
        phrase=" ".join(sentences),
        weight=100,
        why=f"{'; '.join(notes)} (temp {signed(felt)})",
    )
