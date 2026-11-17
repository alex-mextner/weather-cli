"""Lookup of a month/hour norm in a climate series."""

from __future__ import annotations

from weather_cli.local_time import LocalParts
from weather_cli.types import ClimateSeries


def _cell(table: list[list[float]], local: LocalParts) -> float | None:
    month_index, hour = local.month - 1, local.hour
    if not 0 <= month_index < len(table):
        return None
    row = table[month_index]
    return row[hour] if 0 <= hour < len(row) else None


def norm_at(series: ClimateSeries, local: LocalParts) -> tuple[float, float] | None:
    """(mean, sd) for the local month and hour, or None when the series has no such cell."""
    mean = _cell(series.mean, local)
    sd = _cell(series.sd, local)
    if mean is None or sd is None:
        return None
    return mean, sd
