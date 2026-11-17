"""Which forecast hours can answer "what is coming": one definition for the decisions and the
providers.

Accessed via: the umbrella/dress answers (`decisions/advice.py`), and the providers and `merge`
when they decide whether a bundle has a forecast at all.

Assumptions: a person asking about rain means the next 12 hours; an hour that started less than
30 minutes ago still describes the present. Hours that are all in the past (an old cache entry) or
absent are no forecast: that is "unknown", never "dry".
"""

from __future__ import annotations

from weather_cli.types import Moment

HORIZON_MS = 12 * 3_600_000
# An hour that began up to this long ago still counts as "now".
GRACE_MS = 30 * 60_000


def hours_ahead(hourly: list[Moment], now: float) -> list[Moment]:
    return [m for m in hourly if now - GRACE_MS < m.time <= now + HORIZON_MS]
