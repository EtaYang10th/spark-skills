"""Shared utilities for the spark_skills_gen package."""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


# ── Environment helpers ──────────────────────────────────────────────

def build_env(
    spark_root: Path,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a subprocess environment dict, loading ``.env`` if available.

    Parameters
    ----------
    spark_root:
        Project root that may contain a ``.env`` file.
    overrides:
        Extra key-value pairs merged last (highest priority).
    """
    env = os.environ.copy()

    env_file = spark_root / ".env"
    if env_file.exists():
        for raw_line in env_file.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip("'\"")

    # Enable BuildKit for faster, parallelised Docker builds.
    env.setdefault("DOCKER_BUILDKIT", "1")
    env.setdefault("COMPOSE_DOCKER_CLI_BUILD", "1")

    if overrides:
        env.update(overrides)
    return env


# ── Markdown fence stripping ─────────────────────────────────────────

def strip_markdown_fence(text: str) -> str:
    """Strip wrapping ````` markdown ... ````` fences that LLMs sometimes emit.

    Ensures the output starts with a clean ``---`` YAML frontmatter
    delimiter, which is required by agents like Codex for skill discovery.
    """
    lines = text.strip().splitlines()
    if not lines:
        return text

    # Strip leading fence (```markdown, ```yaml, ```md, or bare ```)
    first = lines[0].strip()
    if first.startswith("```"):
        lines = lines[1:]

    # Strip trailing fence
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    return "\n".join(lines).strip()
