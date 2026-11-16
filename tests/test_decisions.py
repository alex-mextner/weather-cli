from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from builders import TZ, T, at, bundle, climate, moment

from weather_cli.decisions.advice import describe_advice, describe_dress, describe_umbrella
from weather_cli.decisions.anomaly import describe_temp_anomaly
from weather_cli.decisions.daily import (
    describe_insight,
    describe_tomorrow,
    describe_week_summary,
    week_insights,
)
from weather_cli.decisions.humidity import HumidityInput, describe_humidity
from weather_cli.decisions.lookahead import describe_lookahead
from weather_cli.decisions.temperature import (
    describe_feels,
    describe_sky,
    describe_temperature,
    feels_like,
)
from weather_cli.decisions.wind import WindInput, describe_wind, wind_level
from weather_cli.thresholds import DEFAULT_THRESHOLDS, with_thresholds

NOW = at("2026-11-09", 14, 10)


class TestTemperature:
    def test_rounds_to_whole_degrees_no_decimals(self) -> None:
        assert describe_temperature(moment(NOW, temp=6.9)).phrase == "7 градусов"
        assert describe_temperature(moment(NOW, temp=-0.4)).phrase == "ноль градусов"
        assert describe_temperature(moment(NOW, temp=-2.6)).phrase == "минус 3 градуса"
        assert describe_temperature(moment(NOW, temp=1.2)).phrase == "1 градус"


class TestFeelsLikeBoundary:
    """Spoken whole degrees, ≥ 6 either way."""

    @pytest.mark.parametrize(
        ("air", "feels", "fires"),
        [
            (7, 2, False),  # gap 5
            (7, 1, True),  # gap 6
            (7, 0, True),
            (10, 15, False),  # +5
            (10, 16, True),  # +6
            (-2, -8, True),
            (-2, -7, False),
            (6.5, 1.5, False),  # spoken 7 vs 2 → gap 5 (rounding is what the listener hears)
            (5.6, 0, True),  # spoken 6 vs 0 → gap 6
        ],
    )
    def test_air_feels_fires(self, air: float, feels: float, fires: bool) -> None:
        decision = describe_feels(moment(NOW, temp=air, feels=feels), T)
        assert decision.fired is fires
        assert (decision.phrase is not None) is fires

    def test_wording_depends_on_the_cause(self) -> None:
        assert (
            describe_feels(moment(NOW, temp=5, feels=-3, wind=10), T).phrase
            == "дует — ощущается как минус 3"
        )
        assert (
            describe_feels(moment(NOW, temp=5, feels=-3, wind=7), T).phrase
            == "на ветру ощущается как минус 3"
        )
        assert (
            describe_feels(moment(NOW, temp=5, feels=-3, wind=1), T).phrase
            == "ощущается как минус 3"
        )
        assert (
            describe_feels(moment(NOW, temp=28, feels=35, humidity=75, wind=1), T).phrase
            == "из-за влажности ощущается как 35"
        )

    def test_computes_apparent_temperature_when_the_provider_gives_none(self) -> None:
        cold = moment(NOW, temp=5, humidity=80, wind=12)
        assert feels_like(cold) is not None
        assert describe_feels(cold, T).fired is True
        assert feels_like(moment(NOW, temp=5)) is None
        assert describe_feels(moment(NOW, temp=5), T).fired is False

    def test_the_threshold_is_configurable(self) -> None:
        t = with_thresholds({"feels_gap": 3})
        assert describe_feels(moment(NOW, temp=7, feels=4), t).fired is True


class TestSky:
    @pytest.mark.parametrize(
        ("condition", "precip", "phrase"),
        [
            ("clear", 0, "ясно"),
            ("cloudy", 0, "пасмурно"),
            ("rain", 0.5, "небольшой дождь"),
            ("rain", 2, "идёт дождь"),
            ("rain", 5, "сильный дождь"),
            ("snow", 0.3, "идёт снег"),
            ("thunderstorm", 3, "гроза"),
        ],
    )
    def test_condition_mm(self, condition: str, precip: float, phrase: str) -> None:
        assert describe_sky(moment(NOW, condition=condition, precip=precip)).phrase == phrase


class TestHumidityVsTheMonthNorm:
    @staticmethod
    def humidity_input(humidity: float, wet_or_fog: bool = False) -> HumidityInput:
        return HumidityInput(
            humidity=humidity,
            climate=climate(75, 10),
            time=NOW,
            tz=TZ,
            wet_or_fog=wet_or_fog,
        )

    @pytest.mark.parametrize(
        ("humidity", "phrase"),
        [
            (75, None),
            (68, None),  # z -0.7
            (66, None),  # z -0.9
            (65, "суше обычного для ноября"),  # z -1.0, gap 10: the boundary is inclusive
            (64, "суше обычного для ноября"),  # z -1.1, gap 11
            (50, "очень сухо для ноября"),  # z -2.5
            (84, None),  # z +0.9
            (85, "влажнее обычного для ноября"),  # z +1.0, gap 10: the boundary is inclusive
            (86, "влажнее обычного для ноября"),  # z +1.1, gap 11
            (95, "очень сыро для ноября"),  # z +2.0
        ],
    )
    def test_humidity(self, humidity: float, phrase: str | None) -> None:
        d = describe_humidity(self.humidity_input(humidity), T)
        assert d.phrase == phrase
        assert d.fired is (phrase is not None)

    def test_damp_air_is_not_announced_while_it_rains(self) -> None:
        assert describe_humidity(self.humidity_input(95, True), T).fired is False

    def test_a_wide_norm_needs_a_wider_gap_a_narrow_one_does_not_shout_on_small_gaps(
        self,
    ) -> None:
        # sd 20: 60 is only z -0.75 from 75 → silent. sd 2 floors at 3: gap 6 < 10 → silent.
        wide = replace(self.humidity_input(60), climate=climate(75, 20))
        narrow = replace(self.humidity_input(69), climate=climate(75, 2))
        assert describe_humidity(wide, T).fired is False
        assert describe_humidity(narrow, T).fired is False

    def test_silent_without_norms_or_without_a_reading(self) -> None:
        no_norms = replace(self.humidity_input(20), climate=None)
        no_reading = replace(self.humidity_input(20), humidity=None)
        assert describe_humidity(no_norms, T).fired is False
        assert describe_humidity(no_reading, T).fired is False

    def test_the_norm_is_the_same_month_and_hour_a_humid_night_is_normal_a_humid_noon_is_not(
        self,
    ) -> None:
        grid = climate(75, 8)
        for row in grid.humidity.mean:
            row[2] = 92  # nights are damp
            row[14] = 55  # afternoons are dry
        night = at("2026-11-09", 2, 0)
        noon = at("2026-11-09", 14, 0)
        assert (
            describe_humidity(
                HumidityInput(humidity=92, climate=grid, time=night, tz=TZ, wet_or_fog=False), T
            ).fired
            is False
        )
        assert (
            describe_humidity(
                HumidityInput(humidity=92, climate=grid, time=noon, tz=TZ, wet_or_fog=False), T
            ).fired
            is True
        )

    def test_never_says_a_percentage(self) -> None:
        for h in [10, 30, 95, 99]:
            phrase = describe_humidity(self.humidity_input(h), T).phrase or ""
            assert not re.search(r"\d|%|процент", phrase)


class TestTemperatureAgainstTheMonthNorm:
    def test_warm_and_cold_anomalies_silent_inside_the_norm(self) -> None:
        c = climate(70, 10, 8, 3)
        assert (
            describe_temp_anomaly(moment(NOW, temp=17), c, TZ, T).phrase
            == "для ноября необычно тепло"
        )
        assert (
            describe_temp_anomaly(moment(NOW, temp=-2), c, TZ, T).phrase
            == "для ноября необычно холодно"
        )
        assert describe_temp_anomaly(moment(NOW, temp=11), c, TZ, T).fired is False
        assert describe_temp_anomaly(moment(NOW, temp=17), None, TZ, T).fired is False


class TestWindCategories:
    @pytest.mark.parametrize(
        ("wind", "gust", "level"),
        [
            (3, 5, "none"),
            (5.9, 9.9, "none"),
            (6, 0, "breezy"),
            (2, 10, "breezy"),  # gusts alone
            (8.9, 13.9, "breezy"),
            (9, 0, "strong"),
            (4, 14, "strong"),
            (12.9, 18.9, "strong"),
            (13, 0, "storm"),
            (5, 19, "storm"),
        ],
    )
    def test_wind_gust(self, wind: float, gust: float, level: str) -> None:
        assert wind_level(wind, gust, T) == level

    def test_silent_when_insignificant_never_prints_a_number(self) -> None:
        d = describe_wind(WindInput(wind=3, gust=5, wet=True), T)
        assert d.fired is False
        assert d.phrase is None

    def test_umbrella_meaningful_wording(self) -> None:
        assert describe_wind(WindInput(wind=7, wet=False), T).phrase == "ветрено"
        assert (
            describe_wind(WindInput(wind=7, wet=True), T).phrase
            == "ветрено, зонт придётся держать крепко"
        )
        assert (
            describe_wind(WindInput(wind=10, wet=True), T).phrase
            == "сильный ветер, зонт может вывернуть"
        )
        assert (
            describe_wind(WindInput(wind=15, wet=True), T).phrase == "штормовой ветер, зонт унесёт"
        )
        assert describe_wind(WindInput(wind=3, gust=11, wet=False), T).phrase == "порывистый ветер"


class TestLookaheadTheNextHour:
    def test_rain_starting_within_the_hour(self) -> None:
        b = bundle(
            now=NOW,
            hour=lambda _, start: (
                {"condition": "rain", "precip": 1.2, "precip_prob": 80}
                if start == at("2026-11-09", 15)
                else {}
            ),
        )
        assert describe_lookahead(b, NOW, T).phrase == "через час начнётся дождь"  # +50 min

    @pytest.mark.parametrize(
        ("now", "phrase"),
        [
            (at("2026-11-09", 14, 50), "вот-вот начнётся дождь"),
            (at("2026-11-09", 14, 30), "скоро начнётся дождь"),
            (at("2026-11-09", 14, 5), "через час начнётся дождь"),
        ],
    )
    def test_rain_at_15_00_asked_at(self, now: int, phrase: str) -> None:
        b = bundle(
            now=now,
            hour=lambda _, start: (
                {"condition": "rain", "precip": 1.2} if start == at("2026-11-09", 15) else {}
            ),
        )
        assert describe_lookahead(b, now, T).phrase == phrase

    def test_rain_ending_within_the_hour(self) -> None:
        b = bundle(
            now=NOW,
            current={"condition": "rain", "precip": 1},
            hour=lambda _, start: {"condition": "rain", "precip": 1} if start <= NOW else {},
        )
        assert describe_lookahead(b, NOW, T).phrase == "дождь скоро закончится"

    def test_steady_rain_nothing_to_report(self) -> None:
        b = bundle(
            now=NOW,
            current={"condition": "rain", "precip": 1},
            hour=lambda _i, _s: {"condition": "rain", "precip": 1},
        )
        assert describe_lookahead(b, NOW, T).fired is False

    def test_snow_and_thunder_and_heavy_rain_have_their_own_words(self) -> None:
        start = at("2026-11-09", 15)
        snow = bundle(
            now=NOW,
            current={"temp": -1},
            hour=lambda _, s: {"condition": "snow", "precip": 1, "temp": -1} if s == start else {},
        )
        assert "снег" in (describe_lookahead(snow, NOW, T).phrase or "")
        storm = bundle(
            now=NOW,
            hour=lambda _, s: {"condition": "thunderstorm", "precip": 3} if s == start else {},
        )
        assert describe_lookahead(storm, NOW, T).phrase == "в ближайший час возможна гроза"
        heavy = bundle(
            now=NOW,
            hour=lambda _, s: {"condition": "heavy-rain", "precip": 6} if s == start else {},
        )
        assert "ливень" in (describe_lookahead(heavy, NOW, T).phrase or "")

    @pytest.mark.parametrize(
        ("now", "nxt", "fires"),
        [
            (10, 12, False),  # +2
            (10, 13, True),  # +3 warms
            (10, 7, True),  # −3 cools
            (10, 8, False),
        ],
    )
    def test_temperature_within_the_hour_fires(self, now: int, nxt: int, fires: bool) -> None:
        b = bundle(
            now=NOW,
            current={"temp": now},
            hour=lambda _, s: {"temp": nxt} if s == at("2026-11-09", 15) else {"temp": now},
        )
        d = describe_lookahead(b, NOW, T)
        assert d.fired is fires
        if fires:
            assert re.search("потеплеет" if nxt > now else "похолодает", d.phrase or "")

    def test_wind_picking_up_to_strong(self) -> None:
        b = bundle(
            now=NOW,
            current={"wind": 3},
            hour=lambda _, s: {"wind": 10} if s == at("2026-11-09", 15) else {"wind": 3},
        )
        assert describe_lookahead(b, NOW, T).phrase == "через час усилится ветер"

    def test_rain_two_hours_away_is_not_news(self) -> None:
        b = bundle(
            now=NOW,
            hour=lambda _, s: (
                {"condition": "rain", "precip": 2} if s >= at("2026-11-09", 16) else {}
            ),
        )
        assert describe_lookahead(b, NOW, T).fired is False


def _tomorrow(offset: int) -> dict[str, Any]:
    if offset == 1:
        return {"max": 14, "min": 6, "condition": "rain", "precip": 4}
    return {"max": 9, "min": 4}


class TestTomorrow:
    @pytest.mark.parametrize(
        ("now", "fires"),
        [
            (at("2026-11-09", 20, 59), False),
            (at("2026-11-09", 21, 0), True),
            (at("2026-11-09", 23, 59), True),
            (at("2026-11-09", 12, 0), False),
        ],
    )
    def test_asked_at_fires(self, now: int, fires: bool) -> None:
        assert describe_tomorrow(bundle(now=now, day=_tomorrow), now, T, False).fired is fires

    def test_forced_always_answers_with_shift_against_today(self) -> None:
        b = bundle(now=NOW, day=_tomorrow)
        assert (
            describe_tomorrow(b, NOW, T, True).phrase
            == "завтра днём 14, ночью 6 градусов, дождь, на 5 градусов теплее, чем сегодня"
        )

    def test_late_evening_threshold_is_configurable(self) -> None:
        now = at("2026-11-09", 19, 0)
        assert (
            describe_tomorrow(
                bundle(now=now, day=_tomorrow), now, with_thresholds({"late_hour": 18}), False
            ).fired
            is True
        )

    def test_dst_on_the_eve_of_the_spring_change_tomorrow_is_the_29th_not_the_30th(self) -> None:
        now = at("2026-03-28", 23, 30)
        b = bundle(
            now=now,
            day=lambda offset: {"max": 20, "min": 9} if offset == 1 else {"max": 10, "min": 3},
        )
        assert b.daily[1].date == "2026-03-29"
        d = describe_tomorrow(b, now, T, False)
        assert d.fired is True
        assert "днём 20" in (d.phrase or "")

    def test_wind_and_sub_zero_values_read_correctly(self) -> None:
        b = bundle(
            now=NOW,
            day=lambda o: (
                {
                    "max": -1,
                    "min": -6,
                    "condition": "snow",
                    "precip": 3,
                    "snow": 4,
                    "wind_max": 10,
                }
                if o == 1
                else {"max": 5, "min": 0}
            ),
        )
        assert describe_tomorrow(b, NOW, T, True).phrase == (
            "завтра днём минус 1, ночью минус 6 градусов, снег, сильный ветер, "
            "на 6 градусов холоднее, чем сегодня"
        )


DayShape = Callable[[int], dict[str, Any]]


def _kinds(shape: DayShape) -> list[str]:
    return [f"{i.kind}@{i.offset}" for i in week_insights(bundle(now=NOW, day=shape), NOW, T)]


class TestWeekInsights:
    def test_quiet_week_nothing_to_say(self) -> None:
        assert _kinds(lambda _: {}) == []
        assert describe_insight(bundle(now=NOW), NOW, T).fired is False

    def test_warming_needs_5_above_the_level_of_today_and_tomorrow(self) -> None:
        assert _kinds(lambda o: {"max": 16 if o >= 4 else 11}) == ["warming@4"]
        assert _kinds(lambda o: {"max": 15 if o >= 4 else 11}) == []  # +4: not enough
        b = bundle(now=NOW, day=lambda o: {"max": 17 if o >= 4 else 11})
        assert week_insights(b, NOW, T)[0].phrase == "в пятницу потеплеет до 17 градусов"

    def test_cooling_with_the_weekday_spoken_in_the_right_case(self) -> None:
        b = bundle(now=NOW, day=lambda o: {"max": 4 if o >= 3 else 12})
        assert week_insights(b, NOW, T)[0].phrase == "с четверга похолодает, днём всего 4 градуса"

    def test_snow_onset_rain_onset_rain_end(self) -> None:
        snow_week = bundle(
            now=NOW,
            day=lambda o: (
                {"condition": "snow", "precip": 4, "snow": 5, "max": 1, "min": -2}
                if o == 3
                else {"max": 1, "min": 1}
            ),
        )
        assert week_insights(snow_week, NOW, T)[0].phrase == "в четверг пойдёт снег"
        assert "rain-onset@2" in _kinds(
            lambda o: {"condition": "rain", "precip": 5} if 2 <= o <= 3 else {}
        )
        b = bundle(now=NOW, day=lambda o: {"condition": "rain", "precip": 6} if o <= 1 else {})
        end = next((i for i in week_insights(b, NOW, T) if i.kind == "rain-end"), None)
        assert end is not None
        assert end.phrase == "дожди закончатся к послезавтрашнему дню"

    def test_rain_then_snow_then_dry_the_spell_is_precipitation_not_rain(self) -> None:
        """Спелл «осадки», а не «дожди»."""

        def shape(o: int) -> dict[str, Any]:
            if o == 0:
                return {"condition": "rain", "precip": 5}
            if o == 1:
                return {"condition": "snow", "precip": 5, "snow": 4}
            return {}

        b = bundle(now=NOW, day=shape)
        end = next(i for i in week_insights(b, NOW, T) if i.kind == "rain-end")
        assert end.phrase == "осадки закончатся к послезавтрашнему дню"

    def test_one_rainy_day_followed_by_dry_days_ends_tomorrow(self) -> None:
        """«Дождь закончится уже завтра»."""
        b = bundle(now=NOW, day=lambda o: {"condition": "rain", "precip": 6} if o == 0 else {})
        end = next(i for i in week_insights(b, NOW, T) if i.kind == "rain-end")
        assert end.phrase == "дождь закончится уже завтра"

    def test_frost_arriving_after_mild_nights_hard_frost_heat(self) -> None:
        frost = bundle(now=NOW, day=lambda o: {"min": -2} if o == 2 else {"min": 4})
        assert (
            week_insights(frost, NOW, T)[0].phrase
            == "послезавтра ночью заморозки до минус 2 градусов"
        )
        hard = bundle(now=NOW, day=lambda o: {"min": -12} if o == 3 else {"min": 4})
        assert "сильный мороз" in week_insights(hard, NOW, T)[0].phrase
        heat = bundle(now=NOW, day=lambda o: {"max": 36} if o == 5 else {"max": 22})
        heat_insight = next(i for i in week_insights(heat, NOW, T) if i.kind == "heat")
        assert heat_insight.phrase == "в субботу сильная жара до 36 градусов"

    def test_already_freezing_today_is_not_news(self) -> None:
        assert "frost@1" not in _kinds(lambda _: {"min": -3})

    def test_week_summary_has_a_range_and_the_wet_day_count(self) -> None:
        b = bundle(
            now=NOW,
            day=lambda o: {
                "min": 2 + o,
                "max": 8 + o,
                **({"precip": 3, "condition": "rain"} if o in (2, 3) else {}),
            },
        )
        assert (
            describe_week_summary(b, NOW, T).phrase
            == "на неделе от 2 до 15 градусов, дождь в 2 из 8 дней"
        )


class TestAdvice:
    @staticmethod
    def wet_from(hour: int) -> Callable[[int, float], dict[str, Any]]:
        return lambda _, start: (
            {"condition": "rain", "precip": 1.5} if start >= at("2026-11-09", hour) else {}
        )

    def test_umbrella_wet_now_wet_later_dry_snow_storm_wind(self) -> None:
        wet_now = bundle(
            now=NOW,
            current={"condition": "rain", "precip": 1},
            hour=lambda _i, s: (
                {"condition": "rain", "precip": 1} if s < at("2026-11-09", 17) else {}
            ),
        )
        assert (
            describe_umbrella(wet_now, NOW, T).phrase
            == "Да, сейчас идёт дождь, закончится вечером."
        )
        assert (
            describe_umbrella(bundle(now=NOW, hour=self.wet_from(18)), NOW, T).phrase
            == "Да, вечером будет дождь."
        )
        assert (
            describe_umbrella(bundle(now=NOW, hour=self.wet_from(15)), NOW, T).phrase
            == "Да, скоро начнётся дождь."
        )
        assert describe_umbrella(bundle(now=NOW), NOW, T).phrase == "Нет, дождя не ожидается."
        windy = bundle(
            now=NOW,
            current={"condition": "rain", "precip": 2, "wind": 11},
            hour=lambda _i, _s: {"condition": "rain", "precip": 2},
        )
        assert "зонт унесёт" in (describe_umbrella(windy, NOW, T).phrase or "")
        snow = bundle(
            now=NOW,
            current={"temp": -2},
            hour=lambda _, s: (
                {"condition": "snow", "precip": 1, "temp": -2}
                if s >= at("2026-11-09", 18)
                else {"temp": -2}
            ),
        )
        assert "Зонт не нужен" in (describe_umbrella(snow, NOW, T).phrase or "")

    def test_late_evening_with_a_wet_tomorrow_adds_a_note(self) -> None:
        now = at("2026-11-09", 22, 0)
        b = bundle(now=now, day=lambda o: {"precip": 5, "condition": "rain"} if o == 1 else {})
        assert (
            describe_umbrella(b, now, T).phrase
            == "Нет, дождя не ожидается. Завтра зонт пригодится."
        )

    def test_now_mode_hint_only_when_wet_not_when_the_wind_already_covers_the_umbrella(
        self,
    ) -> None:
        assert describe_advice(bundle(now=NOW), NOW, T).fired is False
        rainy = bundle(now=NOW, current={"condition": "rain", "precip": 1})
        assert describe_advice(rainy, NOW, T).phrase == "возьми зонт"
        windy = bundle(now=NOW, current={"condition": "rain", "precip": 1, "wind": 11})
        assert describe_advice(windy, NOW, T).fired is False

    @pytest.mark.parametrize(
        ("temp", "start"),
        [
            (-20, "Очень холодно"),
            (-8, "Мороз"),
            (0, "Холодно"),
            (7, "Прохладно"),
            (14, "Свежо"),
            (20, "Тепло: хватит"),
            (25, "Тепло: футболка"),
            (31, "Жарко"),
            (37, "Очень жарко"),
        ],
    )
    def test_dress_at_temp(self, temp: float, start: str) -> None:
        b = bundle(now=NOW, current={"temp": temp, "feels": temp})
        assert (describe_dress(b, NOW, T).phrase or "").startswith(start)

    def test_dress_uses_the_felt_temperature_warns_about_an_evening_drop_and_rain(self) -> None:
        def shape(_: int, s: float) -> dict[str, Any]:
            over: dict[str, Any] = {"temp": 4 if s >= at("2026-11-09", 19) else 12}
            if s >= at("2026-11-09", 15):
                over.update({"condition": "rain", "precip": 1})
            return over

        b = bundle(now=NOW, current={"temp": 12, "feels": 6}, hour=shape)
        phrase = describe_dress(b, NOW, T).phrase or ""
        assert phrase.startswith("Прохладно")
        assert "Вечером станет 4 градуса, захвати что-то тёплое." in phrase
        assert "Скоро начнётся дождь." in phrase


class TestThresholds:
    def test_with_thresholds_deep_merges_and_keeps_defaults(self) -> None:
        t = with_thresholds({"wind": {"strong": 8}, "late_hour": 20})
        assert t.wind.strong == 8
        assert t.wind.breezy == DEFAULT_THRESHOLDS.wind.breezy
        assert t.late_hour == 20
        assert t.week == DEFAULT_THRESHOLDS.week
