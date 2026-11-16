"""Domain types shared by providers, decision functions and the CLI.

Units are fixed here and never converted downstream: °C, m/s, mm, %, hPa, epoch milliseconds.
Providers convert at the boundary (km/h, ISO strings, WMO codes); decision functions
therefore never need to guess a unit.

Field names are the snake_case form of the TypeScript original (`precipProb` -> `precip_prob`).
Saved bundles and the `--json` output keep the original camelCase keys; the decoders in
`cache.py` and the encoder in `cli/` translate at that boundary only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Condition = Literal[
    "clear",
    "partly-cloudy",
    "cloudy",
    "fog",
    "drizzle",
    "rain",
    "heavy-rain",
    "thunderstorm",
    "snow",
    "heavy-snow",
    "sleet",
    "hail",
]

PrecipKind = Literal["none", "rain", "snow", "mixed"]

SourceName = Literal["home-assistant", "open-meteo"]

Mode = Literal["now", "tomorrow", "week", "umbrella", "dress"]

DecisionId = Literal[
    "temperature",
    "feels",
    "sky",
    "humidity",
    "wind",
    "lookahead",
    "tomorrow",
    "insight",
    "temp-anomaly",
    "advice",
    "week-summary",
    "umbrella",
    "dress",
]


@dataclass(frozen=True, slots=True)
class Place:
    lat: float
    lon: float
    # IANA zone, e.g. Europe/Belgrade. All "local" reasoning (month, evening, tomorrow) uses it.
    tz: str


@dataclass(frozen=True, slots=True)
class Moment:
    """One instant: the current observation or one forecast hour."""

    # Epoch ms. For forecast hours: the START of the hour the values describe.
    time: float
    temp: float
    condition: Condition
    # Apparent temperature. Filled by the provider or computed from temp/humidity/wind.
    feels: float | None = None
    humidity: float | None = None
    # Sustained wind, m/s.
    wind: float | None = None
    # Gust, m/s.
    gust: float | None = None
    # Precipitation during the hour starting at `time`, mm.
    precip: float | None = None
    # Probability of precipitation, 0..100.
    precip_prob: float | None = None


@dataclass(frozen=True, slots=True)
class Day:
    """One local calendar day."""

    # Local date, YYYY-MM-DD.
    date: str
    min: float
    max: float
    condition: Condition
    # Precipitation sum, mm.
    precip: float
    # Snowfall sum, cm.
    snow: float
    precip_prob: float | None = None
    wind_max: float | None = None
    gust_max: float | None = None


@dataclass(frozen=True, slots=True)
class Bundle:
    place: Place
    # Epoch ms the data was fetched (or last refreshed from a live provider).
    fetched_at: float
    current: Moment
    # Forecast hours, ascending, starting at or just before `current.time`.
    hourly: list[Moment]
    # Forecast days, ascending, first one is today (local).
    daily: list[Day]
    sources: list[SourceName]
    # Provider that supplied `current`; None for bundles saved before this was recorded.
    current_source: SourceName | None = None
    # A live provider's forecast request failed, so an empty `hourly`/`daily` means "unknown",
    # not "nothing coming". Never persisted: such a bundle is not cached.
    forecast_missing: bool = False


@dataclass(frozen=True, slots=True)
class ClimateSeries:
    """Monthly norms by local hour: index [month-1][hour]."""

    mean: list[list[float]]
    sd: list[list[float]]


@dataclass(frozen=True, slots=True)
class Climate:
    place: Place
    # Inclusive range of years the norms were computed from.
    years: tuple[int, int]
    humidity: ClimateSeries
    temp: ClimateSeries
    built_at: float


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome of one decision function. `why` is always filled (also when nothing fired) so
    `weather explain` can show the arithmetic behind silence."""

    id: DecisionId
    fired: bool
    phrase: str | None
    # Higher wins the limited sentence budget.
    weight: float
    why: str


@dataclass(frozen=True, slots=True)
class Report:
    mode: Mode
    text: str
    sentences: list[str]
    decisions: list[Decision]
    # Phrases of fired decisions that did not fit the sentence budget.
    dropped: list[DecisionId] = field(default_factory=list)
    as_of: float = 0
    stale: bool = False
    sources: list[SourceName] = field(default_factory=list)
