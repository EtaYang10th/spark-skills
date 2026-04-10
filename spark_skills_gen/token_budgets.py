"""Centralised token / character budget configuration loaded from JSON."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

_trunc_log = logging.getLogger("spark.truncation")

_RED = "\033[91m"
_RESET = "\033[0m"


def truncate_head(text: str, limit: int, label: str) -> str:
    """Keep the first *limit* characters. Warn in red if anything is dropped."""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    _trunc_log.warning(
        "%s[TRUNCATED] %s: kept first %d chars, dropped %d chars (%.1f%%)%s",
        _RED, label, limit, dropped, dropped / len(text) * 100, _RESET,
    )
    return text[:limit]


def truncate_tail(text: str, limit: int, label: str) -> str:
    """Keep the last *limit* characters. Warn in red if anything is dropped."""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    _trunc_log.warning(
        "%s[TRUNCATED] %s: kept last %d chars, dropped %d chars (%.1f%%)%s",
        _RED, label, limit, dropped, dropped / len(text) * 100, _RESET,
    )
    return text[-limit:]


@dataclass
class _OutputTokens:
    reflect: int = 800
    skill_generation: int = 6000


@dataclass
class _InputChars:
    instruction: int = 32768
    exploration_memo: int = 3000
    agent_commands: int = 2000
    test_summary: int = 1500
    agent_stdout_for_skill: int = 12000
    environment_info: int = 4000


@dataclass
class _JudgeChars:
    agent_stdout_read: int = 12000
    test_stdout_read: int = 8000


@dataclass
class TokenBudgets:
    output_tokens: _OutputTokens = field(default_factory=_OutputTokens)
    input_chars: _InputChars = field(default_factory=_InputChars)
    judge_chars: _JudgeChars = field(default_factory=_JudgeChars)

    @classmethod
    def from_json(cls, path: str | Path) -> TokenBudgets:
        raw = json.loads(Path(path).read_text())
        budgets = cls()
        for section_name in ("output_tokens", "judge_chars"):
            if section_name in raw:
                target = getattr(budgets, section_name)
                for k, v in raw[section_name].items():
                    if hasattr(target, k):
                        setattr(target, k, int(v))
        if "input_chars" in raw:
            target = budgets.input_chars
            for k, v in raw["input_chars"].items():
                if hasattr(target, k):
                    setattr(target, k, int(v))
        return budgets
