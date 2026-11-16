"""`say()`: run the decision functions for a mode and assemble a short spoken report.

Accessed via: the CLI (`weather say`, `weather explain`) and any embedding host (the voice
assistant calls `say()` with a fetched bundle).

Assumptions: a voice answer is 2-3 short sentences. The headline (temperature, feels-like,
sky) always speaks; extra sentences compete for the remaining budget by `weight`, and the
ones that lose are reported in `dropped` so `explain` can show what was left out.
"""

from __future__ import annotations

from dataclasses import dataclass

from weather_cli.conditions import current_precipitation, is_wet
from weather_cli.decisions.advice import describe_advice, describe_dress, describe_umbrella
from weather_cli.decisions.anomaly import describe_temp_anomaly
from weather_cli.decisions.daily import (
    describe_insight,
    describe_tomorrow,
    describe_week_summary,
    week_insights,
)
from weather_cli.decisions.humidity import HumidityInput, describe_humidity
from weather_cli.decisions.lookahead import describe_lookahead, window_of
from weather_cli.decisions.temperature import describe_feels, describe_sky, describe_temperature
from weather_cli.decisions.wind import WindInput, describe_wind
from weather_cli.ru import sentence
from weather_cli.thresholds import DEFAULT_THRESHOLDS, Thresholds
from weather_cli.types import Bundle, Climate, Decision, DecisionId, Mode, Report

# Data older than this is reported as stale.
_STALE_MS = 3 * 3_600_000


@dataclass(frozen=True, slots=True)
class _Request:
    bundle: Bundle
    now: float
    climate: Climate | None
    thresholds: Thresholds
    # Sentence budget including the headline; None picks the mode's default.
    max_sentences: int | None


def _finish(
    mode: Mode,
    req: _Request,
    sentences: list[str],
    decisions: list[Decision],
    dropped: list[DecisionId],
) -> Report:
    return Report(
        mode=mode,
        text=" ".join(sentences),
        sentences=sentences,
        decisions=decisions,
        dropped=dropped,
        as_of=req.bundle.fetched_at,
        stale=req.now - req.bundle.fetched_at > _STALE_MS,
        sources=req.bundle.sources,
    )


def _headline(temperature: Decision, feels: Decision, sky: Decision) -> str:
    parts = [f"на улице {temperature.phrase}"]
    if feels.fired and feels.phrase:
        parts.append(feels.phrase)
    parts.append(sky.phrase or "")
    return ", ".join(parts)


def _say_now(req: _Request) -> Report:
    bundle, now, climate, thresholds = req.bundle, req.now, req.climate, req.thresholds
    current = bundle.current
    tz = bundle.place.tz

    temperature = describe_temperature(current)
    feels = describe_feels(current, thresholds)
    precipitation = current_precipitation(bundle)
    sky = describe_sky(current, precipitation)
    lookahead = describe_lookahead(bundle, now, thresholds)
    wet_soon = any(is_wet(m) for m in window_of(bundle, now, thresholds))
    wind = describe_wind(
        WindInput(wet=precipitation.wet or wet_soon, wind=current.wind, gust=current.gust),
        thresholds,
    )
    humidity = describe_humidity(
        HumidityInput(
            humidity=current.humidity,
            climate=climate,
            time=current.time,
            tz=tz,
            wet_or_fog=precipitation.wet or current.condition == "fog",
        ),
        thresholds,
    )
    tomorrow = describe_tomorrow(bundle, now, thresholds, False)
    insight = describe_insight(bundle, now, thresholds)
    anomaly = describe_temp_anomaly(current, climate, tz, thresholds)
    advice = describe_advice(bundle, now, thresholds)

    budget = max(1, 3 if req.max_sentences is None else req.max_sentences) - 1
    candidates = sorted(
        (d for d in (lookahead, wind, humidity, tomorrow, insight, anomaly, advice) if d.fired),
        key=lambda d: -d.weight,
    )
    chosen = candidates[:budget]
    dropped = [d.id for d in candidates[budget:]]
    return _finish(
        "now",
        req,
        [sentence(_headline(temperature, feels, sky)), *(sentence(d.phrase or "") for d in chosen)],
        [temperature, feels, sky, lookahead, wind, humidity, tomorrow, insight, anomaly, advice],
        dropped,
    )


def _say_tomorrow(req: _Request) -> Report:
    tomorrow = describe_tomorrow(req.bundle, req.now, req.thresholds, True)
    insights = [i for i in week_insights(req.bundle, req.now, req.thresholds) if i.offset >= 2]
    decisions = [tomorrow]
    sentences = [sentence(tomorrow.phrase or "")] if tomorrow.fired else ["Прогноза на завтра нет."]
    if insights:
        top = insights[0]
        decisions.append(_insight_decision(top.kind, top.offset, top.phrase, top.weight))
        sentences.append(sentence(top.phrase))
    return _finish("tomorrow", req, sentences, decisions, [])


def _insight_decision(kind: str, offset: int, phrase: str, weight: float) -> Decision:
    return Decision(
        id="insight", fired=True, phrase=phrase, weight=weight, why=f"{kind}@+{offset}d"
    )


def _say_week(req: _Request) -> Report:
    summary = describe_week_summary(req.bundle, req.now, req.thresholds)
    insights = week_insights(req.bundle, req.now, req.thresholds)
    budget = max(1, 4 if req.max_sentences is None else req.max_sentences) - 1
    chosen = insights[:budget]
    decisions = [summary]
    sentences = [sentence(summary.phrase or "")] if summary.fired else ["Прогноза на неделю нет."]
    for i in chosen:
        decisions.append(_insight_decision(i.kind, i.offset, i.phrase, i.weight))
        sentences.append(sentence(i.phrase))
    if summary.fired and not insights:
        sentences.append("Резких перемен не будет.")
    dropped: list[DecisionId] = ["insight" for _ in insights[budget:]]
    return _finish("week", req, sentences, decisions, dropped)


def _say_umbrella(req: _Request) -> Report:
    umbrella = describe_umbrella(req.bundle, req.now, req.thresholds)
    return _finish("umbrella", req, [sentence(umbrella.phrase or "")], [umbrella], [])


def _say_dress(req: _Request) -> Report:
    dress = describe_dress(req.bundle, req.now, req.thresholds)
    return _finish("dress", req, [sentence(dress.phrase or "")], [dress], [])


_MODES = {
    "now": _say_now,
    "tomorrow": _say_tomorrow,
    "week": _say_week,
    "umbrella": _say_umbrella,
    "dress": _say_dress,
}


def say(
    *,
    bundle: Bundle,
    now: float,
    mode: Mode = "now",
    climate: Climate | None = None,
    thresholds: Thresholds | None = None,
    max_sentences: int | None = None,
) -> Report:
    request = _Request(
        bundle=bundle,
        now=now,
        climate=climate,
        thresholds=DEFAULT_THRESHOLDS if thresholds is None else thresholds,
        max_sentences=max_sentences,
    )
    return _MODES[mode](request)
