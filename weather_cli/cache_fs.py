"""File-backed `Store`: one JSON file per key in a directory, written atomically.

Accessed via: the CLI and the voice host. The only module in the library that touches the disk.

Assumptions: the directory may be missing (created on first write) or unwritable (writes fail
silently upstream: no cache is a slower answer, not a broken one). File names and contents match
the 0.1.0 release, so an existing cache directory keeps working.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class FileStore:
    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self._dir = Path(directory)

    def read(self, key: str) -> str | None:
        try:
            return (self._dir / key).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def write(self, key: str, value: str) -> None:
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = self._dir / key
        # A unique, exclusively created temp name: threads and processes never share one, and a
        # planted symlink cannot be followed.
        descriptor, name = tempfile.mkstemp(dir=self._dir, prefix=f"{key}.", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value)
            os.replace(name, target)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
