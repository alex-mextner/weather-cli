"""Russian wording helpers: plural agreement, signed temperatures, weekday and month forms.

Accessed via: every decision function that builds a phrase.

Assumptions: all numbers are already whole (callers pass `spoken(x)`); output never contains
a decimal separator, a percent sign or a speed unit — the property test in tests/ enforces it
on whole reports.

`to_fixed` (and `js_number`, from providers/json.py) print exactly what JavaScript's
`n.toFixed(d)` / `String(n)` would: the spoken text and the `explain` output are a byte-level
contract, and Python's `str(float)` / `format` differ from JS on whole floats, exponents and
exact ties.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from weather_cli.local_time import DayPart, add_days, day_part_of, weekday_of
from weather_cli.providers.json import js_number

DayCase = Literal["on", "from", "to"]


def plural(n: float, one: str, few: str, many: str) -> str:
    absolute = abs(n)
    last_two = absolute % 100
    last = absolute % 10
    if 11 <= last_two <= 14:
        return many
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def signed(n: float) -> str:
    """«минус 3», «0», «7»: a temperature without the unit word."""
    return f"минус {js_number(-n)}" if n < 0 else js_number(n)


def degrees(n: float) -> str:
    """«минус 3 градуса», «1 градус», «ноль градусов»."""
    word = plural(n, "градус", "градуса", "градусов")
    return f"ноль {word}" if n == 0 else f"{signed(n)} {word}"


_MONTH_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def month_genitive(month: int) -> str:
    """«ноября» for month 11."""
    return _MONTH_GENITIVE[month - 1] if 1 <= month <= 12 else ""


# Weekday forms indexed by 0 = Sunday: (on, from, to).
_WEEKDAYS: tuple[dict[DayCase, str], ...] = (
    {"on": "в воскресенье", "from": "с воскресенья", "to": "к воскресенью"},
    {"on": "в понедельник", "from": "с понедельника", "to": "к понедельнику"},
    {"on": "во вторник", "from": "со вторника", "to": "ко вторнику"},
    {"on": "в среду", "from": "со среды", "to": "к среде"},
    {"on": "в четверг", "from": "с четверга", "to": "к четвергу"},
    {"on": "в пятницу", "from": "с пятницы", "to": "к пятнице"},
    {"on": "в субботу", "from": "с субботы", "to": "к субботе"},
)

# Offsets 0..2 have their own words: today, tomorrow, the day after tomorrow.
_NEAR_DAYS: tuple[dict[DayCase, str], ...] = (
    {"on": "сегодня", "from": "с сегодняшнего дня", "to": "с сегодняшнего дня"},
    {"on": "завтра", "from": "с завтрашнего дня", "to": "к завтрашнему дню"},
    {"on": "послезавтра", "from": "с послезавтра", "to": "к послезавтрашнему дню"},
)


def day_name(today_date: str, offset: int, grammatical_case: DayCase) -> str:
    """A day named relative to today.

    Offset 1 → «завтра», 2 → «послезавтра», further → weekday in the requested case.
    `today_date` is the local YYYY-MM-DD of today.
    """
    if 0 <= offset <= 2:
        return _NEAR_DAYS[offset][grammatical_case]
    return _WEEKDAYS[weekday_of(add_days(today_date, offset))][grammatical_case]


_DAY_PART_ON: dict[DayPart, str] = {
    "night": "ночью",
    "morning": "утром",
    "day": "днём",
    "evening": "вечером",
}


def day_part_on(hour: int) -> str:
    """«вечером» for hour 19."""
    return _DAY_PART_ON[day_part_of(hour)]


def capitalize(text: str) -> str:
    return text if len(text) == 0 else text[0].upper() + text[1:]


def sentence(text: str) -> str:
    """A sentence: capitalised, ends with a full stop unless it already ends with a mark."""
    trimmed = text.strip()
    if len(trimmed) == 0:
        return ""
    capitalised = capitalize(trimmed)
    return capitalised if capitalised[-1] in ".!?…" else f"{capitalised}."


def degrees_genitive(n: float) -> str:
    """Genitive form used after «от»/«до»: «1 градуса», «минус 2 градусов»."""
    absolute = abs(n)
    word = "градуса" if absolute % 10 == 1 and absolute % 100 != 11 else "градусов"
    return f"{signed(n)} {word}"


def degrees_up_to(n: float) -> str:
    """After «до»: «до минус 2 градусов», «до 1 градуса», «до нуля»."""
    return "до нуля" if n == 0 else f"до {degrees_genitive(n)}"


def to_fixed(value: float, digits: int) -> str:
    """`value.toFixed(digits)`: the exact binary value rounded half away from zero."""
    # JS falls back to String(x) from 1e21 on.
    if value != value or abs(value) >= 1e21:
        return js_number(value)
    quantum = Decimal(1).scaleb(-digits)
    rounded = Decimal(abs(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    return f"{'-' if value < 0 else ''}{rounded:f}"
