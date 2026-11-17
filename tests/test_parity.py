"""Parity with the recorded 0.1.0 reference ("the oracle"): library output and CLI behaviour.

The fixtures in tests/parity/ (gzipped JSON: 14 MB of recorded output, too large to review as
text) were recorded from weather-cli 0.1.0 (commit 0fb3ef4; the recorder went away with that code).
Library fixtures hold a saved-bundle JSON (camelCase, as on disk),
optional climate norms, the instant, the mode and the full expected report; the CLI matrix holds
argv -> exit code / stdout / stderr, run here against `bin/weather` with a minimal environment.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from runner import bare_env

from weather_cli.cache import decode_bundle_text, decode_climate_text
from weather_cli.cli.config import VERSION
from weather_cli.cli.render import render_explain, report_json
from weather_cli.compose import say
from weather_cli.conditions import WET_MM, current_precipitation, precip_kind_of
from weather_cli.thresholds import with_thresholds

ROOT = Path(__file__).resolve().parent.parent
PARITY = ROOT / "tests" / "parity"
LIBRARY_GROUPS = ("examples", "compose", "random", "edge")
# TZ is pinned: a few CLI cases parse `--at` without an offset and resolve the default zone.
CLI_TZ = "Europe/Belgrade"


def _load(name: str) -> dict[str, Any]:
    return json.loads(gzip.decompress((PARITY / f"{name}.gz").read_bytes()))


def _library_cases() -> list[Any]:
    cases = []
    for group in LIBRARY_GROUPS:
        data = _load(f"library_{group}.json")
        refs = data.get("bundles", {})
        for world in data["worlds"]:
            for mode, expected in world["results"].items():
                cases.append(
                    pytest.param(refs, world, mode, expected, id=f"{group}::{world['id']}::{mode}")
                )
    return cases


def _cli_cases() -> list[Any]:
    return [pytest.param(case, id=case["id"]) for case in _load("cli_matrix.json")["cases"]]


def _snake(name: str) -> str:
    return "".join(f"_{c.lower()}" if c.isupper() else c for c in name)


def _snake_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {_snake(k): _snake_keys(v) for k, v in value.items()}
    return value


def _flat_grid(value: float) -> list[list[float]]:
    return [[value] * 24 for _ in range(12)]


def _climate_text(spec: dict[str, Any]) -> str:
    """The cache-file text of a climate spec: `flat` norms or a full `ramp` object."""
    if "ramp" in spec:
        climate = spec["ramp"]
    else:
        humidity_mean, humidity_sd, temp_mean, temp_sd = spec["flat"]
        climate = {
            "place": {"lat": 44.8, "lon": 20.48, "tz": "Europe/Belgrade"},
            "years": [2015, 2024],
            "humidity": {"mean": _flat_grid(humidity_mean), "sd": _flat_grid(humidity_sd)},
            "temp": {"mean": _flat_grid(temp_mean), "sd": _flat_grid(temp_sd)},
            "builtAt": 0,
        }
    return json.dumps({"schema": 1, "climate": climate})


def _bundle_text(refs: dict[str, Any], world: dict[str, Any]) -> str:
    if "bundleText" in world:
        return world["bundleText"]
    source = refs[world["bundleRef"]] if "bundleRef" in world else world["bundle"]
    return json.dumps(source)


def rain_precedence_applies(bundle_json: Any) -> bool:
    """Issue #2: an observed precipitating condition with a smaller model amount.

    The oracle called such a current moment dry; the Python port intentionally
    follows the observation, so these cases check the new invariant instead of byte equality.
    """
    current = bundle_json.get("current") if isinstance(bundle_json, dict) else None
    if not isinstance(current, dict):
        return False
    precip = current.get("precip")
    return (
        precip_kind_of(current.get("condition")) != "none"
        and isinstance(precip, int | float)
        and not isinstance(precip, bool)
        and precip < WET_MM
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _plain(world: dict[str, Any]) -> bool:
    return "bundleText" not in world


def _assert_consistent(bundle: Any, report: Any, expected: dict[str, Any]) -> None:
    """The current precipitation decision is shared: now text and umbrella never disagree.

    The deviation touches only the current-precipitation wording, so the facts about the report
    (as-of, staleness, sources) stay the oracle's in every mode, and the forecast modes
    (tomorrow, week) are the oracle's byte for byte.
    """
    assert current_precipitation(bundle).wet
    assert report.as_of == expected["asOf"]
    assert report.stale is expected["stale"]
    assert report.sources == expected["sources"]
    if report.mode in ("tomorrow", "week"):
        assert report.text == expected["text"]
        assert report.sentences == expected["sentences"]
        assert _decisions(report) == expected["decisions"]
        assert report.dropped == expected["dropped"]
    if report.mode == "umbrella":
        assert report.text.startswith(("Идёт", "Да, сейчас идёт"))
    if report.mode == "now":
        assert "начнётся" not in report.sentences[0]


def _decisions(report: Any) -> list[list[Any]]:
    return [[d.id, d.fired, d.phrase, d.weight, d.why] for d in report.decisions]


def _as_the_oracle_marked_cuts(explain: str) -> str:
    """`explain` as the oracle printed it: when any insight was over the sentence budget it marked
    EVERY insight line cut (✂), spoken or not. The port marks only the ones left out."""
    lines = explain.split("\n")
    footer = next((ln for ln in lines if ln.startswith("  (✂ fired but over the sentence")), "")
    if "insight" not in footer.split(": ", 1)[-1].rstrip(")").split(", "):
        return explain
    return "\n".join("✂" + ln[1:] if ln.startswith("✔ insight ") else ln for ln in lines)


def sky_follows_umbrella(bundle: Any) -> bool:
    """The sky phrase names what the precipitation decision sees: a wet decision under a
    dry-sounding code says what falls, a rejected precipitating code says only "overcast".
    The oracle printed the code's own phrase."""
    wet = current_precipitation(bundle).wet
    observed = precip_kind_of(bundle.current.condition)
    return (wet and observed == "none") or (not wet and observed != "none")


# What the sky decision may now say where it deviates (recorded from the cases, not computed by
# the code under test): what falls when the decision is wet under a dry-sounding code, and
# "overcast" for a rejected precipitating code.
WET_SKY_PHRASES = {"идёт дождь", "сильный дождь", "идёт снег", "идёт сильный снег", "мокрый снег"}
DRY_SKY_PHRASE = "пасмурно"


def _assert_sky_deviation_only(bundle: Any, report: Any, expected: dict[str, Any]) -> None:
    """Everything except the sky wording is the oracle's, weight and reason of the sky included."""
    old = next(d for d in expected["decisions"] if d[0] == "sky")
    new = next(d for d in report.decisions if d.id == "sky")
    assert new.fired and old[1]
    allowed = WET_SKY_PHRASES if current_precipitation(bundle).wet else {DRY_SKY_PHRASE}
    assert new.phrase in allowed
    assert (new.weight, new.why) == (old[3], old[4])
    assert expected["text"].replace(old[2], new.phrase, 1) == report.text
    assert [s.replace(old[2], new.phrase, 1) for s in expected["sentences"]] == report.sentences
    rest = [row for row in _decisions(report) if row[0] != "sky"]
    assert rest == [d for d in expected["decisions"] if d[0] != "sky"]
    assert report.dropped == expected["dropped"]


def no_forecast_hours_ahead(bundle: Any, now: float) -> bool:
    """No forecast hour inside the umbrella's 12 h horizon (a stale bundle, or none at all).

    The oracle read that as a dry forecast; the port calls the forecast unknown.
    """
    start, end = now - 30 * 60_000, now + 12 * 3_600_000
    return not any(start < moment.time <= end for moment in bundle.hourly)


@pytest.mark.parametrize(("refs", "world", "mode", "expected"), _library_cases())
def test_library_matches_oracle(
    refs: dict[str, Any], world: dict[str, Any], mode: str, expected: dict[str, Any]
) -> None:
    bundle = decode_bundle_text(_bundle_text(refs, world))
    assert bundle is not None
    climate = decode_climate_text(_climate_text(world["climate"])) if world.get("climate") else None
    if world.get("climate"):
        assert climate is not None
    extra: dict[str, Any] = {}
    if "thresholds" in world:
        extra["thresholds"] = with_thresholds(_snake_keys(world["thresholds"]))
    if "maxSentences" in world:
        extra["max_sentences"] = world["maxSentences"]

    report = say(bundle=bundle, now=world["now"], mode=mode, climate=climate, **extra)

    if rain_precedence_applies(json.loads(_bundle_text(refs, world)) if _plain(world) else None):
        _assert_consistent(bundle, report, expected)
        return
    if report.mode == "umbrella" and no_forecast_hours_ahead(bundle, world["now"]):
        assert "дождя не ожидается" not in report.text
        return
    if sky_follows_umbrella(bundle) and any(d.id == "sky" for d in report.decisions):
        _assert_sky_deviation_only(bundle, report, expected)
        return
    assert report.text == expected["text"]
    assert report.sentences == expected["sentences"]
    assert _decisions(report) == expected["decisions"]
    assert report.dropped == expected["dropped"]
    assert report.as_of == expected["asOf"]
    assert report.stale is expected["stale"]
    assert report.sources == expected["sources"]
    explain = _as_the_oracle_marked_cuts(render_explain(report, bundle.place.tz))
    if "explain" in expected:
        assert explain == expected["explain"]
    else:
        assert _sha(explain) == expected["explainSha"]
    assert _sha(report_json(report, "file")) == expected["jsonSha"]


# `--at` forms the oracle's `Date.parse` accepts but the documented ISO-8601 contract does
# not. The Python CLI rejects them with a usage error; the oracle's answer is noted per id.
INTENTIONAL_AT_DIVERGENCES = {
    "space-offset",  # oracle accepted '2026-10-12 12:15:00 +02:00'
    "month-name",  # oracle accepted 'Oct 12 2026 12:15 GMT+0200'
    "rfc2822",  # oracle accepted 'Mon, 12 Oct 2026 10:15:00 GMT'
    "year-only",  # oracle accepted '2026' (as UTC midnight 1 Jan)
    "year-month",  # oracle accepted '2026-10'
    "bad-day",  # oracle rolled '2026-02-30' over to March
    "hour-24",  # oracle accepted 'T24:00:00Z' as next midnight
    "expanded-year",  # oracle accepted '+002026-10-12T10:15:00Z'
    "lowercase-z",  # oracle accepted '2026-10-12t10:15:00z'
    "slashes",  # oracle accepted '10/12/2026 12:15'
    "leap-day-bad",  # oracle rolled '2027-02-29' over to March
}
HELP_ADDITION = "          negative numbers need the = form: --lon=-74\n"
MALFORMED_COORDINATES = {"place/lat-nan", "place/lon-nan", "place/lat-infinity", "env/lat-nan"}
CLIMATE_OTHER_ZONE = {"climate/show-built-tz", "climate/show-other-place-key"}
BLANK_COORDINATES = {"place/lat-empty-is-zero", "env/empty-lat-is-zero"}
DIVERGENT_AT_IDS = {
    f"at/{prefix}{name}" for name in INTENTIONAL_AT_DIVERGENCES for prefix in ("", "explain/")
}


@pytest.mark.parametrize("case", _cli_cases())
def test_cli_matches_oracle(case: dict[str, Any], tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    for relative, content in case.get("seed", {}).items():
        target = home / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    env = bare_env(home, TZ=CLI_TZ)
    for key, value in case.get("env", {}).items():
        env[key] = value.replace("$HOME", str(home))

    done = subprocess.run(
        [str(ROOT / "bin" / "weather"), *case["argv"]],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=120,
        check=False,
    )

    if case["id"] in DIVERGENT_AT_IDS:
        assert done.returncode == 2
        assert "--at" in done.stderr.decode("utf-8")
        return
    expected = case["expect"]
    if case["id"] in BLANK_COORDINATES:
        # Deliberate deviation: the oracle read a blank latitude/longitude as 0; a blank value
        # now counts as unset, so no location is configured.
        assert done.returncode == 1
        assert done.stderr.decode("utf-8").startswith("weather: no location:")
        return
    if case["id"] == "modes/conflict-with-bad-bundle":
        # Deliberate deviation: conflicting mode flags are a usage error before anything else
        # (the oracle opened the bundle first and failed with ENOENT, exit 1).
        assert done.returncode == 2
        assert done.stderr.decode("utf-8") == "weather: pick one mode, got: tomorrow, week\n"
        return
    if case["id"] == "cache/bad-flag-tz":
        # Deliberate deviation: a zone the machine does not know is rejected up front (as on the
        # `--bundle` path); the oracle never looked at it while a cached bundle answered.
        assert done.returncode == 1
        assert done.stdout.decode("utf-8") == ""
        assert done.stderr.decode("utf-8") == "weather: invalid time zone: Foo/Bar\n"
        return
    if case["id"] == "cache/future-fetch":
        # Deliberate deviation: the oracle treated a bundle stamped 15 min ahead of the clock as
        # fresh. Beyond 5 min of skew it is a miss, and neither the fresh nor the stale-fallback
        # path serves it; with no provider left there is no data.
        assert done.returncode == 1
        assert done.stdout.decode("utf-8") == ""
        assert done.stderr.decode("utf-8") == "weather: no weather data: no provider configured\n"
        return
    if case["id"] in MALFORMED_COORDINATES:
        # Deliberate deviation: an explicit coordinate that is not a finite number is a usage
        # error (exit 2). The oracle ignored it and reported "no location" (exit 1).
        assert done.returncode == 2
        assert done.stdout.decode("utf-8") == ""
        assert "must be a number" in done.stderr.decode("utf-8")
        return
    if case["id"] in CLIMATE_OTHER_ZONE:
        # Deliberate deviation: norms built for another zone are not used (the grid is indexed by
        # local month and hour in its own zone), so they read as "not cached".
        assert done.returncode == 0
        assert done.stdout.decode("utf-8") == (
            "no climate norms cached; run `weather climate update`\n"
        )
        return
    if case["id"].startswith("plumbing/"):
        # The oracle printed its own 0.1.0; the release number is the only intended difference.
        expected = {
            **expected,
            "stdout": expected["stdout"].replace("0.1.0", VERSION),
            "stderr": expected["stderr"].replace("0.1.0", VERSION),
        }
    # Deliberate addition to `--help` since 0.1.0: one line about negative numbers.
    stdout = _as_the_oracle_marked_cuts(done.stdout.decode("utf-8").replace(HELP_ADDITION, ""))
    assert stdout == expected["stdout"]
    assert done.stderr.decode("utf-8") == expected["stderr"]
    assert done.returncode == expected["code"]
