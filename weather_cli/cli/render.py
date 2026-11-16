"""Text and JSON rendering for the CLI: help, `explain`, `--json`.

Accessed via: weather_cli/cli/main.py. Wording of the answers themselves lives in the library; this
module only lays out what the library returned.
"""

from __future__ import annotations

from ..local_time import local_parts
from ..providers.json import JsonRecord, dumps
from ..types import Decision, Report
from .config import VERSION

HELP = f"""weather {VERSION} — weather in human Russian

Usage:
  weather say [--now|--tomorrow|--week|--umbrella|--dress] [--json]
  weather explain [mode flags]     which decisions fired, and why
  weather climate update|show      monthly norms (humidity, temperature) for the place

Place:    --lat N --lon N [--tz Europe/Belgrade]   (or WEATHER_LAT/WEATHER_LON/WEATHER_TZ,
          ~/.config/weather-cli/config.json, or Home Assistant's home zone)
          negative numbers need the = form: --lon=-74
Sources:  Open-Meteo always; Home Assistant when WEATHER_HA_URL + WEATHER_HA_TOKEN
          (HA_URL/HA_TOKEN) are set; --no-ha / --no-open-meteo to switch one off
Other:    --timeout MS (default 1200)   --offline (cache only)   --no-cache
          --bundle FILE --at ISO_TIME   (answer from a saved bundle at a given moment)
          --help  --version
"""


def format_time(ms: float, tz: str) -> str:
    p = local_parts(ms, tz)
    return f"{p.year}-{p.month:02d}-{p.day:02d} {p.hour:02d}:{p.minute:02d}"


def _mark(decision: Decision, dropped: list[str]) -> str:
    if not decision.fired:
        return "·"
    return "✂" if decision.id in dropped else "✔"


def _decision_lines(decision: Decision, dropped: list[str]) -> list[str]:
    mark = _mark(decision, dropped)
    said = f"«{decision.phrase}»" if decision.fired and decision.phrase else "silent"
    return [f"{mark} {decision.id.ljust(13)} {said}", f"    {decision.why}"]


def render_explain(report: Report, tz: str) -> str:
    sources = "+".join(report.sources) or "—"
    stale = " · STALE" if report.stale else ""
    lines = [
        f"mode {report.mode} · data as of {format_time(report.as_of, tz)} ({sources}){stale}",
        "",
    ]
    for decision in report.decisions:
        lines.extend(_decision_lines(decision, list(report.dropped)))
    lines.extend(["", f"→ {report.text}"])
    if report.dropped:
        lines.append(f"  (✂ fired but over the sentence budget: {', '.join(report.dropped)})")
    return "\n".join(lines)


def _decision_json(decision: Decision) -> JsonRecord:
    # The umbrella decision has always been built with `weight` before `phrase`; keep the
    # key order so `--json` output stays byte-identical for scripts that diff it.
    head: JsonRecord = {"id": decision.id, "fired": decision.fired}
    if decision.id == "umbrella":
        return {**head, "weight": decision.weight, "phrase": decision.phrase, "why": decision.why}
    return {**head, "phrase": decision.phrase, "weight": decision.weight, "why": decision.why}


def report_json(report: Report, origin: str) -> str:
    """`JSON.stringify({...report, origin}, null, 2)`: camelCase keys, same key order."""
    return dumps(
        {
            "mode": report.mode,
            "text": report.text,
            "sentences": list(report.sentences),
            "decisions": [_decision_json(d) for d in report.decisions],
            "dropped": list(report.dropped),
            "asOf": report.as_of,
            "stale": report.stale,
            "sources": list(report.sources),
            "origin": origin,
        },
        indent=2,
    )
