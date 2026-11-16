from __future__ import annotations

import math

import pytest
from builders import TZ, at, zoned_time_to_ms

from weather_cli.local_time import (
    add_days,
    day_part_of,
    days_between,
    local_date,
    local_parts,
    weekday_of,
)
from weather_cli.providers.json import js_number
from weather_cli.ru import day_name, degrees, degrees_up_to, plural, sentence, signed, to_fixed
from weather_cli.units import spoken


class TestTime:
    @pytest.mark.parametrize(
        ("date", "hour"),
        [("2026-01-15", 9), ("2026-07-15", 23), ("2026-11-09", 0)],
    )
    def test_zoned_time_to_ms_round_trips_local_wall_clock_time_winter_and_summer(
        self, date: str, hour: int
    ) -> None:
        ms = zoned_time_to_ms(TZ, date, hour, 30)
        p = local_parts(ms, TZ)
        assert [local_date(ms, TZ), p.hour, p.minute] == [date, hour, 30]

    def test_tomorrow_is_a_calendar_step_the_eve_of_the_spring_dst_change(self) -> None:
        # 2026-03-29 02:00 CET jumps to 03:00 CEST: the 29th is 23 hours long.
        now = at("2026-03-28", 23, 30)
        assert add_days(local_date(now, TZ), 1) == "2026-03-29"
        # The naive "+24h" lands on the 30th: exactly the bug this guards against.
        assert local_date(now + 24 * 3_600_000, TZ) == "2026-03-30"

    def test_tomorrow_is_a_calendar_step_the_eve_of_the_autumn_dst_change_25_hour_day(
        self,
    ) -> None:
        now = at("2026-10-24", 23, 30)
        assert add_days(local_date(now, TZ), 1) == "2026-10-25"
        assert days_between("2026-10-24", "2026-10-26") == 2

    def test_weekday_and_month_arithmetic(self) -> None:
        assert weekday_of("2026-11-09") == 1
        assert add_days("2026-12-31", 1) == "2027-01-01"
        assert add_days("2026-03-01", -1) == "2026-02-28"

    @pytest.mark.parametrize(
        ("hour", "part"),
        [
            (5, "night"),
            (6, "morning"),
            (10, "morning"),
            (11, "day"),
            (16, "day"),
            (17, "evening"),
            (21, "evening"),
            (22, "night"),
            (0, "night"),
        ],
    )
    def test_hour_is_part(self, hour: int, part: str) -> None:
        assert day_part_of(hour) == part


class TestRussianWording:
    @pytest.mark.parametrize(
        ("n", "expected"),
        [
            (0, "ноль градусов"),
            (1, "1 градус"),
            (2, "2 градуса"),
            (5, "5 градусов"),
            (11, "11 градусов"),
            (21, "21 градус"),
            (22, "22 градуса"),
            (-1, "минус 1 градус"),
            (-3, "минус 3 градуса"),
            (-12, "минус 12 градусов"),
        ],
    )
    def test_degrees(self, n: int, expected: str) -> None:
        assert degrees(n) == expected

    @pytest.mark.parametrize(
        ("n", "expected"),
        [
            (0, "до нуля"),
            (1, "до 1 градуса"),
            (2, "до 2 градусов"),
            (-2, "до минус 2 градусов"),
            (11, "до 11 градусов"),
            (21, "до 21 градуса"),
        ],
    )
    def test_degrees_up_to(self, n: int, expected: str) -> None:
        assert degrees_up_to(n) == expected

    def test_signed_and_plural(self) -> None:
        assert signed(-5) == "минус 5"
        assert signed(0) == "0"
        assert plural(3, "день", "дня", "дней") == "дня"
        assert plural(12, "день", "дня", "дней") == "дней"

    def test_day_names_relative_to_monday_9_nov_2026(self) -> None:
        assert day_name("2026-11-09", 1, "on") == "завтра"
        assert day_name("2026-11-09", 2, "on") == "послезавтра"
        assert day_name("2026-11-09", 3, "on") == "в четверг"
        assert day_name("2026-11-09", 8, "from") == "со вторника"
        assert day_name("2026-11-09", 4, "to") == "к пятнице"

    def test_sentence_capitalises_and_terminates_once(self) -> None:
        assert sentence("дождь скоро закончится") == "Дождь скоро закончится."
        assert sentence("Уже готово.") == "Уже готово."
        assert sentence("  ") == ""


class TestSpokenRounding:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (6.9, 7),
            (6.4, 6),
            (6.5, 7),
            (-6.5, -7),
            (-0.4, 0),
            (0.4, 0),
            (-1.9, -2),
        ],
    )
    def test_spoken_is_never_negative_zero(self, value: float, expected: int) -> None:
        result = spoken(value)
        assert result == expected
        assert math.copysign(1.0, result) > 0 or result < 0  # a float -0.0 would fail here
        assert str(result) == str(expected) and not str(result).startswith("-0")


class TestJsNumberFormatting:
    """`js_number` and `to_fixed` must print what `String(n)` and `n.toFixed(d)` print."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1, "1"),
            (1.0, "1"),
            (-0.0, "0"),
            (0.1 + 0.2, "0.30000000000000004"),
            (2.5, "2.5"),
            (-3.0, "-3"),
            (1e21, "1e+21"),
            (1e-7, "1e-7"),
            (123456789.123, "123456789.123"),
            (0.000001, "0.000001"),
        ],
    )
    def test_js_number(self, value: float, expected: str) -> None:
        assert js_number(value) == expected

    @pytest.mark.parametrize(
        ("value", "digits", "expected"),
        [
            (0.25, 1, "0.3"),
            (-0.25, 1, "-0.3"),
            (6.9, 1, "6.9"),
            (-0.04, 1, "-0.0"),
            (-0.0, 1, "0.0"),
            (1.005, 2, "1.00"),
            (2.5, 0, "3"),
            (10, 1, "10.0"),
        ],
    )
    def test_to_fixed(self, value: float, digits: int, expected: str) -> None:
        assert to_fixed(value, digits) == expected
