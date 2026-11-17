"""Regenerates examples/*.json: synthetic but realistic bundles for the README commands.

Run: python3 scripts/make_examples.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from builders import at, bundle  # noqa: E402

from weather_cli.cache import bundle_to_json  # noqa: E402
from weather_cli.providers.json import dumps  # noqa: E402
from weather_cli.types import Bundle  # noqa: E402


def rain_wind_evening() -> Bundle:
    """Wet, windy autumn evening: the wind decides the umbrella; tomorrow joins after 21:00."""
    rain_ends = at("2026-11-09", 23)

    def hour(_index: int, start: float) -> dict[str, object]:
        raining = start < rain_ends
        return {
            "condition": "rain" if raining else "cloudy",
            "precip": 1.2 if raining else 0,
            "wind": 10,
            "gust": 15,
        }

    def day(offset: int) -> dict[str, object]:
        if offset == 1:
            return {"max": 11, "min": 6, "condition": "cloudy", "precip": 0.4}
        return {"max": 10, "min": 5}

    return bundle(
        now=at("2026-11-09", 21, 40),
        current={
            "temp": 8.3,
            "feels": 3.1,
            "humidity": 93,
            "wind": 10.5,
            "gust": 15,
            "condition": "rain",
            "precip": 1.2,
        },
        hour=hour,
        day=day,
    )


def calm_noon() -> Bundle:
    """Dry, calm and warm in the middle of the day: almost nothing to say."""
    return bundle(
        now=at("2026-10-12", 12, 15),
        current={"temp": 17.6, "humidity": 55, "wind": 2, "condition": "clear"},
        day=lambda _offset: {"max": 18, "min": 8, "condition": "clear"},
    )


def snow_week() -> Bundle:
    """Rain turns to snow during the week, then a cold snap."""

    def day(offset: int) -> dict[str, object]:
        if offset <= 1:
            return {"max": 4, "min": 1, "condition": "rain", "precip": 5}
        if offset == 2:
            return {"max": 1, "min": -2, "condition": "snow", "precip": 6, "snow": 5}
        return {"max": -3, "min": -9, "condition": "cloudy"}

    return bundle(
        now=at("2026-12-01", 9, 0),
        current={"temp": 3.2, "feels": 1, "humidity": 85, "wind": 3, "condition": "cloudy"},
        day=day,
    )


def windy_cold_morning() -> Bundle:
    """A cold snap with a wind: what it feels like matters more than the air."""
    return bundle(
        now=at("2026-12-15", 8, 0),
        current={
            "temp": 2,
            "feels": -6.5,
            "humidity": 70,
            "wind": 9.5,
            "gust": 14,
            "condition": "partly-cloudy",
        },
        hour=lambda _index, _start: {"temp": 2, "wind": 9.5, "gust": 14},
        day=lambda _offset: {"max": 3, "min": -1},
    )


EXAMPLES = {
    "rain-wind-evening": rain_wind_evening,
    "calm-noon": calm_noon,
    "snow-week": snow_week,
    "windy-cold-morning": windy_cold_morning,
}


SCALAR_ARRAY = re.compile(r"\[\n\s+([^\[\]{}\n]+(?:,\n\s+[^\[\]{}\n]+)*)\n\s*\]")


def _collapse_short_arrays(text: str) -> str:
    """Keep arrays of scalars on one line, the way the committed files were formatted."""
    return SCALAR_ARRAY.sub(lambda m: "[" + re.sub(r",\n\s+", ", ", m.group(1)) + "]", text)


def render(name: str) -> str:
    """The exact text of `examples/<name>.json`."""
    return _collapse_short_arrays(dumps(bundle_to_json(EXAMPLES[name]()), indent=2)) + "\n"


def main() -> None:
    for name in EXAMPLES:
        (ROOT / "examples" / f"{name}.json").write_text(render(name), encoding="utf-8")


if __name__ == "__main__":
    main()
