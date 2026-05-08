"""Load the task blacklist from ``task_blacklist.txt`` next to this module."""

from __future__ import annotations

from pathlib import Path

_BLACKLIST_FILE = Path(__file__).with_name("task_blacklist.txt")


def load_blacklist(path: Path | None = None) -> set[str]:
    """Return a set of task names to skip.

    Reads from *path* (default: ``spark_skills_gen/task_blacklist.txt``).
    Lines starting with ``#`` and blank lines are ignored.
    """
    p = path or _BLACKLIST_FILE
    if not p.exists():
        return set()
    names: set[str] = set()
    for line in p.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            names.add(stripped)
    return names
