"""What changes within the hour: the person is about to step outside.

Accessed via: `say()` (now mode), `umbrella` and `dress` answers.

Assumptions: hourly steps describe the hour STARTING at `time` (providers normalise this).
The window is (now − 30 min, now + window_minutes]: the hour already running counts only if it
began recently, so a forecast for "14:00–15:00" is not read as news at 14:55.
Only the strongest change is spoken; the others stay visible in `why` for `explain`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from weather_cli.conditions import WetKind, current_precipitation, is_wet, wet_kind
from weather_cli.decisions.wind import wind_level, wind_level_rank
from weather_cli.providers.json import js_number
from weather_cli.ru import degrees
from weather_cli.thresholds import Thresholds
from weather_cli.types import Bundle, Decision, Moment
from weather_cli.units import js_round, spoken

_MINUTE = 60_000


@dataclass(frozen=True, slots=True)
class _Event:
    kind: Literal["start", "thunder", "end", "temp", "wind"]
    phrase: str
    weight: int
    note: str


_PRECIP_NOUN: dict[WetKind, str] = {
    "rain": "дождь",
    "snow": "снег",
    "mixed": "мокрый снег",
}


def _start_phrase(minutes_away: int, noun: str, heavy: bool) -> str:
    what = "ливень" if heavy and noun == "дождь" else noun
    if minutes_away <= 20:
        return f"вот-вот начнётся {what}"
    if minutes_away <= 45:
        return f"скоро начнётся {what}"
    return f"через час начнётся {what}"


def window_of(bundle: Bundle, now: float, thresholds: Thresholds) -> list[Moment]:
    end = now + thresholds.lookahead.window_minutes * _MINUTE
    return [m for m in bundle.hourly if now - 30 * _MINUTE < m.time <= end]


def _precip_event(first: Moment, now: float) -> _Event:
    minutes = max(0, js_round((first.time - now) / _MINUTE))
    if first.condition == "thunderstorm":
        return _Event(
            kind="thunder",
            phrase="вот-вот начнётся гроза" if minutes <= 20 else "в ближайший час возможна гроза",
            weight=97,
            note=f"thunderstorm hour at +{minutes}min",
        )
    amount = "?" if first.precip is None else js_number(first.precip)
    return _Event(
        kind="start",
        phrase=_start_phrase(
            minutes, _PRECIP_NOUN[wet_kind(first)], first.condition == "heavy-rain"
        ),
        weight=95,
        note=f"wet hour at +{minutes}min ({first.condition}, {amount} mm)",
    )


def _precip_events(
    bundle: Bundle, now: float, window: list[Moment], ahead: list[Moment], wet_now: bool
) -> list[_Event]:
    current = bundle.current
    if not wet_now:
        first = next((m for m in window if is_wet(m)), None)
        return [] if first is None else [_precip_event(first, now)]
    if ahead and all(not is_wet(m) for m in ahead):
        return [
            _Event(
                kind="end",
                phrase=f"{_PRECIP_NOUN[wet_kind(current)]} скоро закончится",
                weight=90,
                note="wet now, every hour of the window dry",
            )
        ]
    return []


def _temp_event(current: Moment, last: Moment, thresholds: Thresholds) -> _Event | None:
    delta = spoken(last.temp) - spoken(current.temp)
    jump = thresholds.lookahead.temp_jump
    if abs(delta) < jump:
        return None
    return _Event(
        kind="temp",
        phrase=(
            f"через час {'похолодает' if delta < 0 else 'потеплеет'}: "
            f"будет {degrees(spoken(last.temp))}"
        ),
        weight=85,
        note=(
            f"air {js_number(spoken(current.temp))}° → {js_number(spoken(last.temp))}° "
            f"(needs ≥ {js_number(jump)}°)"
        ),
    )


def _wind_event(current: Moment, last: Moment, thresholds: Thresholds) -> _Event | None:
    before = wind_level(current.wind, current.gust, thresholds)
    after = wind_level(last.wind, last.gust, thresholds)
    if wind_level_rank(after) > wind_level_rank(before) and wind_level_rank(
        after
    ) >= wind_level_rank("strong"):
        return _Event(
            kind="wind",
            phrase="через час усилится ветер",
            weight=80,
            note=f"wind {before} → {after}",
        )
    return None


def _change_events(current: Moment, last: Moment, thresholds: Thresholds) -> list[_Event]:
    found = [_temp_event(current, last, thresholds), _wind_event(current, last, thresholds)]
    return [event for event in found if event is not None]


def describe_lookahead(bundle: Bundle, now: float, thresholds: Thresholds) -> Decision:
    window = window_of(bundle, now, thresholds)
    if len(window) == 0:
        return Decision(
            id="lookahead",
            fired=False,
            phrase=None,
            weight=95,
            why="no forecast hour inside the window",
        )
    current = bundle.current
    ahead = [m for m in window if m.time > now]
    precipitation = current_precipitation(bundle)
    events = _precip_events(bundle, now, window, ahead, precipitation.wet)
    last = ahead[-1] if ahead else window[-1]
    events.extend(_change_events(current, last, thresholds))
    if len(events) == 0:
        note = precipitation.note()
        suffix = f"; {note}" if note else ""
        wet_now = "true" if precipitation.wet else "false"
        return Decision(
            id="lookahead",
            fired=False,
            phrase=None,
            weight=95,
            why=(
                f"{len(window)} hour(s) in window, nothing material changes "
                f"(wet now: {wet_now}){suffix}"
            ),
        )
    events.sort(key=lambda event: -event.weight)
    top = events[0]
    tail = [precipitation.note()] if precipitation.note() else []
    return Decision(
        id="lookahead",
        fired=True,
        phrase=top.phrase,
        weight=top.weight,
        why="; ".join([*(f"{event.kind}: {event.note}" for event in events), *tail]),
    )
