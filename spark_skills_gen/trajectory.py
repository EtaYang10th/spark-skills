"""Trajectory recording for the SPARK iterative skill generation pipeline.

Writes a JSONL file (one JSON object per line) capturing every significant
event during the Execute → Judge → Reflect loop.  The file can be streamed
(each line is flushed immediately) so partial data survives crashes.

Event types
-----------
- execution_result   — after each Harbor run + Judge parse
- reflect_call       — after each Reflect LLM call
- skill_gen_call     — after the Skill Generation LLM call
- task_summary       — once at the very end of a task
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TRAJECTORY_FILENAME = "trajectory.jsonl"


@dataclass(frozen=True)
class LLMUsage:
    """Token counts and latency from a single LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


class TrajectoryWriter:
    """Append-only JSONL writer for one task's exploration trajectory."""

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir
        self._path = output_dir / TRAJECTORY_FILENAME
        self._output_dir.mkdir(parents=True, exist_ok=True)
        # Truncate any leftover file from a previous (interrupted) run
        self._path.write_text("")

    # ── low-level ──

    def _write(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", time.time())
        with self._path.open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
            f.flush()

    # ── public event writers ──

    def record_execution_result(
        self,
        *,
        attempt: int,
        success: bool,
        reward: float,
        status: str,
        n_passed: int,
        n_tests: int,
        agent_stdout_full: str,
        test_stdout_full: str,
        agent_commands: str,
        test_summary: str,
        result_json: dict | None,
        ctrf_json: dict | None,
        retry_injection: str,
        error: str = "",
    ) -> None:
        self._write({
            "type": "execution_result",
            "attempt": attempt,
            "success": success,
            "reward": reward,
            "status": status,
            "n_passed": n_passed,
            "n_tests": n_tests,
            "agent_stdout_full": agent_stdout_full,
            "test_stdout_full": test_stdout_full,
            "agent_commands": agent_commands,
            "test_summary": test_summary,
            "result_json": result_json,
            "ctrf_json": ctrf_json,
            "retry_injection": retry_injection,
            "error": error,
        })

    def record_reflect_call(
        self,
        *,
        attempt: int,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response: str,
        usage: LLMUsage,
        memo_before: str,
        memo_after: str,
    ) -> None:
        self._write({
            "type": "reflect_call",
            "attempt": attempt,
            "model": model,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response": response,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "latency_s": usage.latency_s,
            "memo_before": memo_before,
            "memo_after": memo_after,
        })

    def record_skill_gen_call(
        self,
        *,
        attempt: int,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response: str,
        usage: LLMUsage,
    ) -> None:
        self._write({
            "type": "skill_gen_call",
            "attempt": attempt,
            "model": model,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response": response,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "latency_s": usage.latency_s,
        })

    def record_pdi_snapshot(
        self,
        *,
        attempt: int,
        snapshot: dict[str, Any],
    ) -> None:
        self._write({
            "type": "pdi_snapshot",
            "attempt": attempt,
            **snapshot,
        })

    def record_task_summary(
        self,
        *,
        task_name: str,
        success: bool,
        total_attempts: int,
        reward_trajectory: list[float],
        status_trajectory: list[str],
        total_time_s: float,
        total_input_tokens: int,
        total_output_tokens: int,
        exploration_config: dict[str, Any],
    ) -> None:
        self._write({
            "type": "task_summary",
            "task_name": task_name,
            "success": success,
            "total_attempts": total_attempts,
            "reward_trajectory": reward_trajectory,
            "status_trajectory": status_trajectory,
            "total_time_s": total_time_s,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "exploration_config": exploration_config,
        })

    @property
    def path(self) -> Path:
        return self._path


# ── Utility: read back a trajectory file ──


def load_trajectory(path: Path) -> list[dict[str, Any]]:
    """Read a trajectory.jsonl and return a list of event dicts."""
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("Skipping malformed trajectory line: %s", line[:80])
    return events
