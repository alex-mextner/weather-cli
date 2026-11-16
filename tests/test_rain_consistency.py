"""Issue #2: the "now" text and the umbrella answer share one decision about current rain."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

import pytest
from builders import CONDITIONS, T, at, bundle, rng

from weather_cli.cache import bundle_to_json, decode_bundle_text
from weather_cli.compose import say
from weather_cli.conditions import current_precipitation
from weather_cli.decisions.advice import describe_dress, describe_umbrella, umbrella_advice
from weather_cli.decisions.lookahead import describe_lookahead
from weather_cli.types import Bundle, SourceName

NOW = at("2026-11-09", 14, 10)
RAIN_WORDS = re.compile(r"дожд|ливень|снег|снегопад|морос|гроза|град(?!ус)")


def ha_rain(precip: float = 0.0, source: SourceName | None = "home-assistant") -> Bundle:
    """HA reports rain; Open-Meteo's current hour (merged into `precip`) and hours are dry."""
    b = bundle(now=NOW, current={"condition": "rain", "precip": precip, "wind": 2})
    return replace(b, current_source=source)


def test_ha_rain_with_zero_model_amount_says_rain_everywhere() -> None:
    b = ha_rain()
    now_text = say(bundle=b, now=NOW).text
    assert "дождь" in now_text
    umbrella = describe_umbrella(b, NOW, T).phrase
    assert umbrella is not None
    assert umbrella.startswith("Да, сейчас идёт дождь")
    assert "начнётся" not in (describe_lookahead(b, NOW, T).phrase or "")


def test_lookahead_wet_bucket_continuing_the_rain_is_not_a_start() -> None:
    b = bundle(
        now=NOW,
        current={"condition": "rain", "precip": 0},
        hour=lambda _, s: {"condition": "rain", "precip": 1} if s <= NOW else {},
    )
    phrase = describe_lookahead(b, NOW, T).phrase or ""
    assert "начнётся" not in phrase
    assert phrase == "дождь скоро закончится"


def test_explain_names_the_deciding_source() -> None:
    b = ha_rain()
    note = "precipitation now: wet (condition, home-assistant: rain, 0 mm)"
    r = say(bundle=b, now=NOW)
    assert note in next(d for d in r.decisions if d.id == "sky").why
    assert note in describe_umbrella(b, NOW, T).why
    assert note in say(bundle=b, now=NOW, mode="umbrella").decisions[0].why


def test_ha_dry_with_open_meteo_wet_amount_is_wet_by_amount() -> None:
    b = bundle(now=NOW, current={"condition": "cloudy", "precip": 0.6})
    b = replace(b, current_source="open-meteo")
    p = current_precipitation(b)
    assert (p.wet, p.decided_by, p.source, p.kind) == (True, "amount", "open-meteo", "rain")
    assert "precipitation now: wet (amount, open-meteo: cloudy, 0.6 mm)" in (
        describe_umbrella(b, NOW, T).why
    )


def test_both_dry() -> None:
    b = replace(
        bundle(now=NOW, current={"condition": "cloudy", "precip": 0}), current_source="open-meteo"
    )
    p = current_precipitation(b)
    assert (p.wet, p.kind, p.decided_by) == (False, None, "amount")
    assert describe_umbrella(b, NOW, T).phrase == "Нет, дождя не ожидается."


def test_snow_kinds() -> None:
    snow = bundle(now=NOW, current={"condition": "snow", "precip": 0, "temp": -2})
    assert current_precipitation(snow).kind == "snow"
    snow_phrase = describe_umbrella(snow, NOW, T).phrase
    assert snow_phrase is not None
    assert snow_phrase.startswith("Идёт снег")
    sleet = bundle(now=NOW, current={"condition": "sleet", "precip": 0, "temp": 1})
    assert current_precipitation(sleet).kind == "mixed"
    assert "мокрый снег" in (describe_umbrella(sleet, NOW, T).phrase or "")


# The TypeScript oracle said «но но скоро закончится» (a doubled «но»); that was a bug.
def test_snow_ending_soon() -> None:
    b = bundle(
        now=NOW,
        current={"condition": "snow", "precip": 1, "temp": -2},
        hour=lambda _, s: (
            {"condition": "snow", "precip": 1, "temp": -2} if s <= NOW else {"temp": -2}
        ),
    )
    assert describe_lookahead(b, NOW, T).phrase == "снег скоро закончится"
    assert (
        describe_umbrella(b, NOW, T).phrase == "Идёт снег, зонт не обязателен, но скоро закончится."
    )


def test_missing_current_source_is_silent_in_explain_unless_contested() -> None:
    b = bundle(now=NOW, current={"condition": "cloudy", "precip": 0.6})
    assert b.current_source is None
    assert current_precipitation(b).source is None
    assert "precipitation now" not in describe_umbrella(b, NOW, T).why
    contested = ha_rain(source=None)
    assert "unknown source" in describe_umbrella(contested, NOW, T).why


def test_current_source_round_trips_and_is_omitted_when_absent() -> None:
    plain = bundle(now=NOW)
    assert "currentSource" not in bundle_to_json(plain)
    tagged = ha_rain()
    text = json.dumps(bundle_to_json(tagged))
    assert decode_bundle_text(text) == tagged
    assert decode_bundle_text(json.dumps(bundle_to_json(plain))) == plain


def _first_clause_says_falling(phrase: str) -> bool:
    return phrase.startswith(("Идёт", "Да, сейчас идёт"))


def test_property_now_and_umbrella_never_disagree_1500_worlds() -> None:
    random = rng(20261110)
    for _ in range(1500):
        cond = CONDITIONS[int(random() * len(CONDITIONS))]
        wet_cond = cond in (
            "drizzle",
            "rain",
            "heavy-rain",
            "thunderstorm",
            "snow",
            "heavy-snow",
            "sleet",
            "hail",
        )
        # The amount agrees with the condition or is the 0.0 mm of a dry model hour.
        precip = random() * 6 if wet_cond and random() < 0.5 else 0.0
        current: dict[str, Any] = {"condition": cond, "precip": precip, "temp": random() * 20 - 5}
        b = replace(
            bundle(
                now=NOW,
                current=current,
                hour=lambda _i, _s, c=current: {"temp": c["temp"]},
            ),
            current_source="home-assistant",
        )
        headline = say(bundle=b, now=NOW).sentences[0]
        umbrella = say(bundle=b, now=NOW, mode="umbrella").text
        assert bool(RAIN_WORDS.search(headline)) == _first_clause_says_falling(umbrella)


def test_umbrella_advice_is_structured() -> None:
    wet = umbrella_advice(ha_rain(), NOW, T)
    assert wet.needed and wet.phrase == f"Да, {wet.statement}"
    dry = umbrella_advice(bundle(now=NOW), NOW, T)
    assert not dry.needed


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_dress_never_repeats_a_sentence(seed: int) -> None:
    random = rng(seed)
    for _ in range(300):
        temp = random() * 30 - 8
        start_rain = at("2026-11-09", 15 + int(random() * 4))
        b = bundle(
            now=NOW,
            current={"temp": temp, "feels": temp, "condition": CONDITIONS[int(random() * 12)]},
            hour=lambda _i, s, t=temp, r=start_rain: {
                "temp": t - (7 if s >= at("2026-11-09", 18) else 0),
                **({"condition": "rain", "precip": 1} if s >= r else {}),
            },
        )
        parts = re.findall(r"[^.]+\.", describe_dress(b, NOW, T).phrase or "")
        stripped = [p.strip() for p in parts]
        assert len(stripped) == len(set(stripped))
