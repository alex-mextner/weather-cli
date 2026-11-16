"""Day-level decisions: tomorrow's headline, week insights, week summary.

Accessed via: `say()` — "tomorrow" mode, "week" mode, and the late-evening tail of "now".

Assumptions: `bundle.daily` is in local calendar days (provider boundary); offsets are
calendar days from today's local date, computed with `days_between`, never from `ms + 24h`.
An insight exists only when something changes (onset/end/threshold crossing): quiet weeks
produce no insight, not a filler sentence.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
from typing import Literal

from weather_cli.decisions.wind import wind_level, wind_level_rank
from weather_cli.local_time import days_between, local_date, local_parts
from weather_cli.providers.json import js_number
from weather_cli.ru import (
    DayCase,
    day_name,
    degrees,
    degrees_genitive,
    degrees_up_to,
    plural,
    signed,
)
from weather_cli.thresholds import Thresholds
from weather_cli.types import Bundle, Condition, Day, Decision
from weather_cli.units import spoken


@dataclass(frozen=True, slots=True)
class DayAt:
    offset: int
    day: Day


def days_from(bundle: Bundle, now: float) -> list[DayAt]:
    """Forecast days from today on, with their calendar offset from today."""
    today = local_date(now, bundle.place.tz)
    entries = [DayAt(offset=days_between(today, day.date), day=day) for day in bundle.daily]
    return sorted((entry for entry in entries if entry.offset >= 0), key=lambda e: e.offset)


def _is_snowy(day: Day, t: Thresholds) -> bool:
    return day.snow >= t.week.snow_day_cm or (
        day.condition in ("snow", "heavy-snow") and day.precip >= 0.5
    )


def _is_wet_day(day: Day, t: Thresholds) -> bool:
    return day.precip >= t.week.wet_day_mm or _is_snowy(day, t)


_DAY_WEATHER: dict[Condition, str] = {
    "clear": "ясно",
    "fog": "туман",
    "drizzle": "морось",
    "heavy-rain": "сильный дождь",
    "thunderstorm": "гроза",
    "snow": "снег",
    "heavy-snow": "сильный снегопад",
    "sleet": "мокрый снег",
    "hail": "град",
}


def day_weather(day: Day, thresholds: Thresholds) -> str:
    """The sky of a whole day in words; precipitation refines rain intensity."""
    wet = _is_wet_day(day, thresholds)
    if day.condition == "partly-cloudy":
        return "переменная облачность, возможны осадки" if wet else "переменная облачность"
    if day.condition == "cloudy":
        return "пасмурно, возможен дождь" if wet else "пасмурно"
    if day.condition == "rain":
        return "сильный дождь" if day.precip >= 10 else "дождь"
    return _DAY_WEATHER[day.condition]


def _find_offset(days: list[DayAt], offset: int) -> Day | None:
    return next((entry.day for entry in days if entry.offset == offset), None)


def _shift_part(high: float, today: Day, thresholds: Thresholds) -> str | None:
    shift = high - spoken(today.max)
    if abs(shift) < thresholds.tomorrow.shift:
        return None
    word = plural(abs(shift), "градус", "градуса", "градусов")
    return f"на {js_number(abs(shift))} {word} {'теплее' if shift > 0 else 'холоднее'}, чем сегодня"


def _tomorrow_parts(tomorrow: Day, today: Day | None, thresholds: Thresholds) -> list[str]:
    high = spoken(tomorrow.max)
    low = spoken(tomorrow.min)
    parts = [
        f"завтра днём {signed(high)}, ночью {degrees(low)}",
        day_weather(tomorrow, thresholds),
    ]
    level = wind_level(tomorrow.wind_max, tomorrow.gust_max, thresholds)
    if wind_level_rank(level) >= wind_level_rank("strong"):
        parts.append("сильный ветер")
    if today is not None:
        shift = _shift_part(high, today, thresholds)
        if shift is not None:
            parts.append(shift)
    return parts


def describe_tomorrow(bundle: Bundle, now: float, thresholds: Thresholds, forced: bool) -> Decision:
    local = local_parts(now, bundle.place.tz)
    late = local.hour >= thresholds.late_hour
    if not forced and not late:
        return Decision(
            id="tomorrow",
            fired=False,
            phrase=None,
            weight=85,
            why=(
                f"local hour {local.hour} < {js_number(thresholds.late_hour)}: "
                "tomorrow not requested"
            ),
        )
    days = days_from(bundle, now)
    today = _find_offset(days, 0)
    tomorrow = _find_offset(days, 1)
    if tomorrow is None:
        return Decision(
            id="tomorrow", fired=False, phrase=None, weight=85, why="no forecast day for tomorrow"
        )
    reason = (
        "requested" if forced else f"local hour {local.hour} ≥ {js_number(thresholds.late_hour)}"
    )
    today_note = f", today high {js_number(spoken(today.max))}°" if today is not None else ""
    return Decision(
        id="tomorrow",
        fired=True,
        phrase=", ".join(_tomorrow_parts(tomorrow, today, thresholds)),
        weight=100 if forced else 85,
        why=(
            f"{reason}; tomorrow {tomorrow.date} high {js_number(spoken(tomorrow.max))}° "
            f"low {js_number(spoken(tomorrow.min))}°{today_note}"
        ),
    )


InsightKind = Literal[
    "snow-onset", "rain-onset", "rain-end", "warming", "cooling", "frost", "hard-frost", "heat"
]


@dataclass(frozen=True, slots=True)
class Insight:
    kind: InsightKind
    # Calendar offset of the day the change happens on.
    offset: int
    phrase: str
    weight: int


def _insight(kind: InsightKind, offset: int, base: int, phrase: str) -> Insight:
    return Insight(kind=kind, offset=offset, phrase=phrase, weight=base - offset * 3)


@dataclass(frozen=True, slots=True)
class _Week:
    """The days a week insight looks at, split once so each rule reads naturally."""

    today: str
    thresholds: Thresholds
    days: list[DayAt]
    day0: DayAt | None
    future: list[DayAt]

    def on(self, offset: int, grammatical_case: DayCase = "on") -> str:
        return day_name(self.today, offset, grammatical_case)

    def snowy(self, entry: DayAt) -> bool:
        return _is_snowy(entry.day, self.thresholds)

    def wet(self, entry: DayAt) -> bool:
        return _is_wet_day(entry.day, self.thresholds)


def _week_of(bundle: Bundle, now: float, thresholds: Thresholds) -> _Week:
    today = local_date(now, bundle.place.tz)
    days = [d for d in days_from(bundle, now) if d.offset <= thresholds.week.horizon_days]
    return _Week(
        today=today,
        thresholds=thresholds,
        days=days,
        day0=next((d for d in days if d.offset == 0), None),
        future=[d for d in days if d.offset >= 1],
    )


def _snow_onset(w: _Week) -> Insight | None:
    start = next((d for d in w.future if w.snowy(d)), None)
    if start is None or (w.day0 is not None and w.snowy(w.day0)):
        return None
    return _insight("snow-onset", start.offset, 82, f"{w.on(start.offset)} пойдёт снег")


def _rain_onset(w: _Week) -> Insight | None:
    onset = next((d for d in w.future if w.wet(d) and not w.snowy(d)), None)
    if onset is None or any(d.offset < onset.offset and w.wet(d) for d in w.days):
        return None
    streak = sum(1 for d in w.future if onset.offset <= d.offset <= onset.offset + 1 and w.wet(d))
    verb = "начнутся дожди" if streak >= 2 else "пойдёт дождь"
    return _insight("rain-onset", onset.offset, 72, f"{w.on(onset.offset)} {verb}")


def _spell_kind(w: _Week, first_dry: DayAt) -> Literal["rain", "snow", "mixed"]:
    spell = [d for d in w.days if d.offset < first_dry.offset]
    snow_days = sum(1 for d in spell if w.snowy(d))
    if snow_days == 0:
        return "rain"
    return "snow" if snow_days == len(spell) else "mixed"


def _rain_end_phrase(w: _Week, first_dry: DayAt) -> str:
    kind = _spell_kind(w, first_dry)
    single = first_dry.offset == 1
    if single:
        noun = {"rain": "дождь", "snow": "снег", "mixed": "осадки"}[kind]
        verb = "закончатся" if kind == "mixed" else "закончится"
        return f"{noun} {verb} уже завтра"
    noun = {"rain": "дожди", "snow": "снегопады", "mixed": "осадки"}[kind]
    return f"{noun} закончатся {w.on(first_dry.offset, 'to')}"


def _rain_end(w: _Week) -> Insight | None:
    if w.day0 is None or not w.wet(w.day0):
        return None
    first_dry = next((d for d in w.future if not w.wet(d)), None)
    if first_dry is None:
        return None
    after = [d for d in w.future if first_dry.offset < d.offset <= first_dry.offset + 1]
    if any(w.wet(d) for d in after):
        return None
    return _insight("rain-end", first_dry.offset, 66, _rain_end_phrase(w, first_dry))


def _temp_shift(w: _Week) -> Insight | None:
    reference = [d for d in (w.day0, w.future[0] if w.future else None) if d is not None]
    if not reference:
        return None
    level = reduce(lambda total, d: total + d.day.max, reference, 0) / len(reference)
    shift = w.thresholds.week.shift
    first = next(
        (d for d in w.future if d.offset >= 2 and abs(spoken(d.day.max) - spoken(level)) >= shift),
        None,
    )
    if first is None:
        return None
    high = spoken(first.day.max)
    if high > spoken(level):
        warming = f"{w.on(first.offset)} потеплеет {degrees_up_to(high)}"
        return _insight("warming", first.offset, 62, warming)
    cooling = f"{w.on(first.offset, 'from')} похолодает, днём всего {degrees(high)}"
    return _insight("cooling", first.offset, 62, cooling)


def _frost(w: _Week) -> Insight | None:
    limit = w.thresholds.week.frost
    day = next((d for d in w.future if d.day.min <= limit), None)
    if day is None or (w.day0 is not None and w.day0.day.min <= limit):
        return None
    low = spoken(day.day.min)
    if low <= w.thresholds.week.hard_frost:
        phrase = f"{w.on(day.offset)} ночью ударит сильный мороз {degrees_up_to(low)}"
        return _insight("hard-frost", day.offset, 80, phrase)
    phrase = f"{w.on(day.offset)} ночью заморозки {degrees_up_to(low)}"
    return _insight("frost", day.offset, 76, phrase)


def _heat(w: _Week) -> Insight | None:
    limit = w.thresholds.week.heat
    day = next((d for d in w.future if d.day.max >= limit), None)
    if day is None or (w.day0 is not None and w.day0.day.max >= limit):
        return None
    high = spoken(day.day.max)
    word = "сильная жара" if high >= w.thresholds.week.strong_heat else "жара"
    return _insight("heat", day.offset, 74, f"{w.on(day.offset)} {word} {degrees_up_to(high)}")


_RULES = (_snow_onset, _rain_onset, _rain_end, _temp_shift, _frost, _heat)


def week_insights(bundle: Bundle, now: float, thresholds: Thresholds) -> list[Insight]:
    """Everything notable that changes during the coming days, strongest first."""
    week = _week_of(bundle, now, thresholds)
    found = [rule(week) for rule in _RULES]
    return sorted((i for i in found if i is not None), key=lambda i: -i.weight)


def describe_insight(bundle: Bundle, now: float, thresholds: Thresholds) -> Decision:
    insights = week_insights(bundle, now, thresholds)
    if not insights:
        return Decision(
            id="insight",
            fired=False,
            phrase=None,
            weight=50,
            why="no onset, end or threshold crossing in the coming days",
        )
    top = insights[0]
    return Decision(
        id="insight",
        fired=True,
        phrase=top.phrase,
        weight=min(top.weight, 75),
        why=", ".join(f"{i.kind}@+{i.offset}d ({i.weight})" for i in insights),
    )


def describe_week_summary(bundle: Bundle, now: float, thresholds: Thresholds) -> Decision:
    """One sentence for the "week" mode when it needs a base: range and wet days."""
    days = [d for d in days_from(bundle, now) if d.offset <= thresholds.week.horizon_days]
    if not days:
        return Decision(
            id="week-summary", fired=False, phrase=None, weight=100, why="no forecast days"
        )
    low = spoken(min(d.day.min for d in days))
    high = spoken(max(d.day.max for d in days))
    wet = sum(1 for d in days if _is_wet_day(d.day, thresholds))
    snow = any(_is_snowy(d.day, thresholds) for d in days)
    kind = "осадки" if snow else "дождь"
    precip = "без осадков" if wet == 0 else f"{kind} в {wet} из {len(days)} дней"
    return Decision(
        id="week-summary",
        fired=True,
        phrase=f"на неделе от {signed(low)} до {degrees_genitive(high)}, {precip}",
        weight=100,
        why=f"{len(days)} days, lows {js_number(low)}°, highs {js_number(high)}°, {wet} wet",
    )
