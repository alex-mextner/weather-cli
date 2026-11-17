"""Public API of weather-cli: decision functions, providers, cache, climate.

Accessed via: embedding hosts (`from weather_cli import get_weather, say`). The CLI lives under
`weather_cli/cli` and is not part of the library surface: hosts may vendor this package without it.
"""

from __future__ import annotations

from .cache import MemoryStore, Store, bundle_key, climate_key
from .cache_fs import FileStore
from .climate import CLIMATE_MAX_AGE_MS, climate_is_stale, load_climate, refresh_climate
from .compose import say
from .decisions.advice import describe_advice, describe_dress, describe_umbrella
from .decisions.anomaly import describe_temp_anomaly
from .decisions.daily import (
    Insight,
    InsightKind,
    describe_insight,
    describe_tomorrow,
    describe_week_summary,
    week_insights,
)
from .decisions.humidity import describe_humidity
from .decisions.lookahead import describe_lookahead
from .decisions.temperature import describe_feels, describe_sky, describe_temperature, feels_like
from .decisions.wind import WindLevel, describe_wind, wind_level
from .providers.home_assistant import HaConnection, fetch_ha_place
from .providers.merge import merge_bundles
from .service import WeatherOrigin, WeatherResult, get_weather
from .thresholds import DEFAULT_THRESHOLDS, Thresholds, with_thresholds
from .types import (
    Bundle,
    Climate,
    Condition,
    Day,
    Decision,
    DecisionId,
    Mode,
    Moment,
    Place,
    Report,
    SourceName,
)

__all__ = [
    "CLIMATE_MAX_AGE_MS",
    "DEFAULT_THRESHOLDS",
    "Bundle",
    "Climate",
    "Condition",
    "Day",
    "Decision",
    "DecisionId",
    "FileStore",
    "HaConnection",
    "Insight",
    "InsightKind",
    "MemoryStore",
    "Mode",
    "Moment",
    "Place",
    "Report",
    "SourceName",
    "Store",
    "Thresholds",
    "WeatherOrigin",
    "WeatherResult",
    "WindLevel",
    "bundle_key",
    "climate_is_stale",
    "climate_key",
    "describe_advice",
    "describe_dress",
    "describe_feels",
    "describe_humidity",
    "describe_insight",
    "describe_lookahead",
    "describe_sky",
    "describe_temp_anomaly",
    "describe_temperature",
    "describe_tomorrow",
    "describe_umbrella",
    "describe_week_summary",
    "describe_wind",
    "feels_like",
    "fetch_ha_place",
    "get_weather",
    "load_climate",
    "merge_bundles",
    "refresh_climate",
    "say",
    "week_insights",
    "wind_level",
    "with_thresholds",
]
