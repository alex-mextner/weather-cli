"""Runs the CLI as a subprocess with a bare, isolated environment (shared by the CLI tests)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The interpreter running the tests comes first on PATH, so `bin/weather` (which runs `python3`)
# uses it rather than whatever `python3` happens to be first.
PATH = os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])
# No real home or cache: a developer's own config and climate norms must not reach an assertion.
BARE_ENV = {
    "PATH": PATH,
    "HOME": "/nonexistent",
    "WEATHER_CACHE_DIR": "/nonexistent/cache",
}


def bare_env(home: Path | None = None, **extra: str) -> dict[str, str]:
    """A minimal environment: PATH and (when given) a HOME that is not the developer's."""
    env = {"PATH": PATH, **extra}
    if home is not None:
        env["HOME"] = str(home)
    return env


def run_cli(
    command: list[str], env: dict[str, str] | None = None, cwd: Path = ROOT
) -> tuple[int, str, str]:
    """(exit code, stripped stdout, stripped stderr)."""
    done = subprocess.run(
        command,
        cwd=cwd,
        env=BARE_ENV if env is None else env,
        capture_output=True,
        check=False,
        timeout=60,
    )
    return done.returncode, done.stdout.decode().strip(), done.stderr.decode().strip()


def weather(*args: str, extra: dict[str, str] | None = None) -> tuple[int, str, str]:
    """`python -m weather_cli ARGS` from the checkout."""
    env = {**BARE_ENV, **(extra or {}), "PYTHONPATH": str(ROOT)}
    return run_cli([sys.executable, "-m", "weather_cli", *args], env)
