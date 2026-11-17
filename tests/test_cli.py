"""The CLI end to end: saved bundles (the README examples) and plumbing, as a subprocess."""

from __future__ import annotations

import json
import re

from runner import ROOT, bare_env, run_cli, weather

RAIN = ["--bundle", "examples/rain-wind-evening.json", "--at", "2026-11-09T21:40:00+01:00"]
CALM = ["--bundle", "examples/calm-noon.json", "--at", "2026-10-12T12:15:00+02:00"]


def say(*args: str) -> str:
    return weather("say", *args)[1]


class TestSavedBundles:
    def test_say(self):
        assert say(*CALM) == "На улице 18 градусов, ясно."
        assert say(*RAIN) == (
            "На улице 8 градусов, идёт дождь. Сильный ветер, зонт может вывернуть. "
            "Завтра днём 11, ночью 6 градусов, пасмурно."
        )

    def test_modes(self):
        assert say("--umbrella", *RAIN) == (
            "Идёт дождь, а ветер сильный: зонт унесёт, лучше куртка с капюшоном."
        )
        assert say("--tomorrow", *CALM).startswith("Завтра днём 18, ночью 8 градусов, ясно")
        assert say("--week", *CALM).endswith("Резких перемен не будет.")

    def test_json_carries_the_decisions(self):
        parsed = json.loads(say("--json", *RAIN))
        assert parsed["mode"] == "now"
        wind = next(d for d in parsed["decisions"] if d["id"] == "wind")
        assert wind["fired"] is True

    def test_explain_shows_silent_decisions_with_their_reasons(self):
        out = weather("explain", *CALM)[1]
        assert "· feels" in out
        assert "needs ≥ 6° either way" in out
        assert "→ На улице 18 градусов, ясно." in out


class TestPlumbing:
    def test_version_is_the_package_version(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        declared = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
        assert declared is not None
        assert weather("--version")[1] == declared.group(1)

    def test_help_works_with_nothing_configured(self):
        code, out, _ = weather("--help")
        assert code == 0
        assert "weather say" in out

    def test_usage_errors_exit_2(self):
        assert weather("nonsense")[0] == 2
        assert weather("say", "--week", "--tomorrow", *CALM)[0] == 2
        assert weather("say", *CALM, "--at", "yesterday")[0] == 2

    def test_no_location_configured_exits_1_with_a_hint(self):
        code, _, err = weather("say")
        assert code == 1
        assert "no location" in err

    def test_malformed_flags_exit_1_with_the_parser_message(self):
        code, _, err = weather("say", "--lat", "-5")
        assert code == 1
        assert "Option '--lat' argument is ambiguous." in err
        assert weather("say", "--bogus")[2].startswith("weather: Unknown option '--bogus'.")

    def test_the_wrapper_works_through_a_symlink_with_a_bare_environment(self, tmp_path):
        link = tmp_path / "weather"
        link.symlink_to(ROOT / "bin" / "weather")
        env = bare_env(tmp_path, WEATHER_CACHE_DIR=str(tmp_path / "cache"))
        code, out, _ = run_cli([str(link), "say", *CALM], env)
        assert (code, out) == (0, "На улице 18 градусов, ясно.")
        assert run_cli([str(link), "--version"], env)[0] == 0

    def test_a_stray_json_py_in_the_working_directory_cannot_shadow_the_stdlib(self, tmp_path):
        (tmp_path / "json.py").write_text("raise RuntimeError('shadowed')\n", encoding="utf-8")
        env = bare_env(tmp_path)
        bundle = ["--bundle", str(ROOT / "examples" / "calm-noon.json"), *CALM[2:]]
        code, out, err = run_cli([str(ROOT / "bin" / "weather"), "say", *bundle], env, tmp_path)
        assert (code, out, err) == (0, "На улице 18 градусов, ясно.", "")

    def test_a_wrapper_without_its_package_fails_clearly_and_never_uses_the_cwd(self, tmp_path):
        # A checkout whose weather_cli/ is gone (or a link into a half-deleted tree).
        bare = tmp_path / "checkout" / "bin"
        bare.mkdir(parents=True)
        script = bare / "weather"
        script.write_text((ROOT / "bin" / "weather").read_text(encoding="utf-8"), encoding="utf-8")
        script.chmod(0o755)
        link = tmp_path / "weather"
        link.symlink_to(script)
        # A decoy package in the working directory must not be imported instead.
        work = tmp_path / "work"
        decoy = work / "weather_cli"
        decoy.mkdir(parents=True)
        (decoy / "__init__.py").write_text("", encoding="utf-8")
        (decoy / "__main__.py").write_text("def main():\n    print('decoy')\n", encoding="utf-8")
        env = bare_env(tmp_path)
        code, out, err = run_cli([str(link), "--version"], env, work)
        assert code == 1
        assert "decoy" not in out
        assert "cannot find weather_cli" in err
