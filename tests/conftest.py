"""Shared fixtures."""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def scrubbed_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """The process environment minus every weather/XDG/Home Assistant variable, so `main()` cannot
    see a developer's config. Callers set `HOME` / `WEATHER_CACHE_DIR` on the returned patcher."""
    for name in list(os.environ):
        if name.startswith(("WEATHER_", "HA_", "XDG_")):
            monkeypatch.delenv(name)
    return monkeypatch
