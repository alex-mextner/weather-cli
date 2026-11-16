from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, TypeVar

from builders import CONDITIONS, T, at, bundle, climate, rng

from weather_cli.compose import say
from weather_cli.types import Mode

NOW = at("2026-11-09", 14, 10)

_V = TypeVar("_V")


class TestSayNow:
    def test_quiet_day_is_one_plain_sentence(self) -> None:
        r = say(
            bundle=bundle(
                now=NOW,
                current={"temp": 6.9, "condition": "cloudy", "humidity": 70, "wind": 2},
            ),
            now=NOW,
        )
        assert r.text == "На улице 7 градусов, пасмурно."
        assert r.dropped == []

    def test_feels_like_joins_the_headline_only_when_it_differs_by_6(self) -> None:
        base = {"temp": 7, "condition": "cloudy", "humidity": 70, "wind": 2}
        mild = say(bundle=bundle(now=NOW, current={**base, "feels": 2}), now=NOW)
        assert mild.text == "На улице 7 градусов, пасмурно."
        felt = say(bundle=bundle(now=NOW, current={**base, "feels": 1}), now=NOW)
        assert felt.text == "На улице 7 градусов, ощущается как 1, пасмурно."

    def test_rain_with_a_rising_wind_rain_starting_later_and_tomorrow_compete_for_the_budget(
        self,
    ) -> None:
        now = at("2026-11-09", 21, 30)
        b = bundle(
            now=now,
            current={"temp": 8, "condition": "cloudy", "wind": 3},
            hour=lambda _, s: (
                {"condition": "rain", "precip": 2} if s >= at("2026-11-09", 22) else {}
            ),
            day=lambda o: {"max": 12, "min": 6, "condition": "rain", "precip": 8} if o == 1 else {},
        )
        r = say(bundle=b, now=now)
        assert len(r.sentences) == 3
        assert r.sentences[1] == "Скоро начнётся дождь."
        assert next(d for d in r.decisions if d.id == "tomorrow").fired is True

    def test_tomorrow_is_attached_from_21_00_local_not_before(self) -> None:
        def mk(now: int) -> str:
            return say(
                bundle=bundle(
                    now=now,
                    current={"temp": 8, "wind": 1},
                    day=lambda o: {"max": 12, "min": 6} if o == 1 else {},
                ),
                now=now,
            ).text

        assert "Завтра" not in mk(at("2026-11-09", 20, 59))
        assert "Завтра днём 12, ночью 6 градусов" in mk(at("2026-11-09", 21, 0))

    def test_humidity_speaks_only_against_a_loaded_norm(self) -> None:
        b = bundle(now=NOW, current={"temp": 12, "humidity": 40, "wind": 1})
        assert say(bundle=b, now=NOW).text == "На улице 12 градусов, пасмурно."
        assert (
            say(bundle=b, now=NOW, climate=climate(75, 10)).text
            == "На улице 12 градусов, пасмурно. Очень сухо для ноября."
        )

    def test_a_week_insight_is_added_when_nothing_more_urgent_competes(self) -> None:
        b = bundle(
            now=NOW,
            current={"temp": 9, "wind": 1},
            day=lambda o: {"max": 18 if o >= 4 else 11},
        )
        assert "В пятницу потеплеет до 18 градусов." in say(bundle=b, now=NOW).text

    def test_sentences_over_the_budget_are_reported_not_silently_lost(self) -> None:
        b = bundle(
            now=NOW,
            current={"temp": 9, "wind": 10, "humidity": 30, "condition": "rain", "precip": 1},
            hour=lambda _, s: {"condition": "rain", "precip": 0} if s > NOW else {},
            day=lambda o: {"max": 18 if o >= 4 else 11},
        )
        r = say(bundle=b, now=NOW, climate=climate(75, 10), max_sentences=2)
        assert len(r.sentences) == 2
        assert len(r.dropped) > 0

    def test_data_older_than_three_hours_is_flagged_stale(self) -> None:
        b = replace(bundle(now=NOW), fetched_at=NOW - 4 * 3_600_000)
        assert say(bundle=b, now=NOW).stale is True


class TestSayOtherModes:
    def test_tomorrow_week_umbrella_dress(self) -> None:
        b = bundle(
            now=NOW,
            day=lambda o: (
                {"max": 14, "min": 6, "condition": "rain", "precip": 4}
                if o == 1
                else {"max": 9, "min": 4}
            ),
        )
        assert say(bundle=b, now=NOW, mode="tomorrow").text.startswith(
            "Завтра днём 14, ночью 6 градусов, дождь"
        )
        assert say(bundle=b, now=NOW, mode="week").text.startswith("На неделе от 4 до 14 градусов")
        assert say(bundle=b, now=NOW, mode="umbrella").mode == "umbrella"
        assert say(bundle=b, now=NOW, mode="dress").text.startswith("Прохладно")

    def test_a_quiet_week_says_so(self) -> None:
        assert say(bundle=bundle(now=NOW), now=NOW, mode="week").text.endswith(
            "Резких перемен не будет."
        )


MODES: list[Mode] = ["now", "tomorrow", "week", "umbrella", "dress"]


class TestPropertySpokenOutputIsHuman:
    def test_never_a_decimal_speed_unit_percentage_minus_sign_or_placeholder_1500_random_worlds(
        self,
    ) -> None:
        random = rng(20261109)

        def between(lo: float, hi: float) -> float:
            return lo + random() * (hi - lo)

        def pick(options: list[_V]) -> _V:
            return options[int(random() * len(options))]

        def hour_shape(base: float) -> dict[str, Any]:
            return {
                "temp": base + between(-6, 6),
                "wind": between(0, 20),
                "gust": between(0, 30),
                "precip": between(0, 8) if random() < 0.3 else 0,
                "precip_prob": between(0, 100),
                "condition": pick(CONDITIONS),
            }

        def day_shape(base: float) -> dict[str, Any]:
            high = base + between(-10, 10)
            return {
                "max": high,
                "min": high - between(2, 14),
                "precip": between(0, 20) if random() < 0.4 else 0,
                "snow": between(0, 15) if random() < 0.2 else 0,
                "condition": pick(CONDITIONS),
                "wind_max": between(0, 18),
                "gust_max": between(0, 28),
            }

        for _ in range(1500):
            month = 1 + int(random() * 12)
            date = f"2026-{month:02d}-{1 + int(random() * 27):02d}"
            now = at(date, int(random() * 24), int(random() * 60))
            base = between(-25, 38)
            current = {
                "temp": base,
                "feels": base + between(-14, 14),
                "humidity": between(5, 100),
                "wind": between(0, 20),
                "gust": between(0, 30),
                "precip": between(0, 8) if random() < 0.4 else 0,
                "condition": pick(CONDITIONS),
            }
            b = bundle(
                now=now,
                current=current,
                hour=lambda _i, _s, base=base: hour_shape(base),
                day=lambda _o, base=base: day_shape(base),
            )
            norms = (
                climate(between(40, 90), between(4, 20), between(-5, 25), between(1, 6))
                if random() < 0.7
                else None
            )
            for mode in MODES:
                r = say(bundle=b, now=now, mode=mode, climate=norms, thresholds=T)
                assert len(r.text) > 0
                assert not re.search(r"\d[.,]\d", r.text)
                assert not re.search(r"км/ч|km/h|м/с|m/s|kph|mph", r.text, re.IGNORECASE)
                assert not re.search(r"%|процент", r.text, re.IGNORECASE)
                assert not re.search(
                    r"-\d|минус 0|NaN|undefined|null|Infinity|None|\bnan\b|\binf\b", r.text
                )
                assert re.search(r"[.!?]$", r.text)
                if mode == "now":
                    assert len(r.sentences) <= 3

    def test_the_timezone_is_respected_evening_in_belgrade_afternoon_in_new_york(
        self,
    ) -> None:
        now = at("2026-11-09", 22, 0)
        b = bundle(now=now, day=lambda o: {"max": 12, "min": 6} if o == 1 else {})
        assert "Завтра" in say(bundle=b, now=now).text
        ny = replace(b, place=replace(b.place, tz="America/New_York"))
        assert "Завтра" not in say(bundle=ny, now=now).text
