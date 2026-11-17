"""`weather explain` marks a decision cut only when its sentence was really left out."""

from __future__ import annotations

from builders import PLACE, bundle
from payloads import NOW

from weather_cli.cli.render import render_explain
from weather_cli.compose import say
from weather_cli.types import Decision, Report


def insight(phrase: str) -> Decision:
    return Decision(id="insight", fired=True, phrase=phrase, weight=60, why="x")


def lines_of(report: Report) -> list[str]:
    return [
        line for line in render_explain(report, PLACE.tz).splitlines() if line and line[0] in "✔✂·"
    ]


def test_week_insights_that_were_spoken_are_not_marked_cut() -> None:
    report = Report(
        mode="week",
        text="Первое. Второе.",
        sentences=["Первое.", "Второе."],
        decisions=[insight("первое"), insight("второе")],
        dropped=["insight"],
        as_of=NOW,
    )
    assert [line[0] for line in lines_of(report)] == ["✔", "✔"]


def test_a_fired_decision_that_was_left_out_is_marked_cut() -> None:
    spoken = Decision(id="wind", fired=True, phrase="ветер", weight=70, why="x")
    cut = Decision(id="humidity", fired=True, phrase="сухо", weight=40, why="x")
    report = Report(
        mode="now",
        text="Ветер.",
        sentences=["Ветер."],
        decisions=[spoken, cut],
        dropped=["humidity"],
        as_of=NOW,
    )
    assert [line[0] for line in lines_of(report)] == ["✔", "✂"]


def test_a_real_week_with_more_insights_than_the_budget_marks_only_the_cut_ones() -> None:
    changes = {2: {"max": 25}, 3: {"max": 5, "precip": 8}, 4: {"max": 24}, 5: {"max": 2}}
    b = bundle(now=NOW, day=lambda offset: changes.get(offset, {}))
    report = say(bundle=b, now=NOW, mode="week", max_sentences=2)
    marks = [line[0] for line in lines_of(report) if "insight" in line]
    assert marks
    assert marks == ["✔"] * len(marks)
