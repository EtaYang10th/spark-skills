"""Stage 2 — Judgment: parse Harbor execution results and determine pass/fail."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from spark_skills_gen.token_budgets import truncate_tail


@dataclass
class TestCaseResult:
    name: str
    status: str  # "passed" | "failed" | "skipped"
    duration: float = 0.0
    message: str = ""


@dataclass
class JudgmentResult:
    task_name: str
    trial_name: str
    reward: float = 0.0
    passed: bool = False
    has_exception: bool = False
    exception_type: str = ""
    exception_message: str = ""
    test_cases: list[TestCaseResult] = field(default_factory=list)
    test_stdout: str = ""
    agent_stdout: str = ""
    agent_commands: str = ""
    n_tests: int = 0
    n_passed: int = 0
    n_failed: int = 0
    # Full (untruncated) copies for trajectory recording
    agent_stdout_full: str = ""
    test_stdout_full: str = ""
    result_json_raw: dict = field(default_factory=dict)
    ctrf_json_raw: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.has_exception:
            return "ERROR"
        if self.passed:
            return "PASS"
        if 0 < self.reward < 1:
            return "PARTIAL"
        return "FAIL"

    @property
    def test_summary(self) -> str:
        """Compact test summary: passed/failed test names only."""
        report = self._build_report_from_stdout()
        if report:
            return report
        return self._build_report_from_ctrf()

    def _build_report_from_stdout(self) -> str:
        if not self.test_stdout:
            return ""
        passed_lines: list[str] = []
        failed_lines: list[str] = []
        for line in self.test_stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("PASSED "):
                passed_lines.append(f"  ✓ {stripped[7:].strip()}")
            elif stripped.startswith("FAILED "):
                failed_lines.append(f"  ✗ {stripped[7:].strip()}")
        if not passed_lines and not failed_lines:
            return ""
        parts: list[str] = []
        if passed_lines:
            parts.append("PASSED (do NOT change logic for these):")
            parts.extend(passed_lines)
        if failed_lines:
            parts.append("FAILED (fix these):")
            parts.extend(failed_lines)
        return "\n".join(parts)

    def _build_report_from_ctrf(self) -> str:
        if not self.test_cases:
            return ""
        passed = [tc for tc in self.test_cases if tc.status == "passed"]
        failed = [tc for tc in self.test_cases if tc.status == "failed"]
        lines: list[str] = []
        if passed:
            lines.append("PASSED (do NOT change logic for these):")
            for tc in passed:
                lines.append(f"  ✓ {tc.name}")
        if failed:
            lines.append("FAILED (fix these):")
            for tc in failed:
                msg = tc.message.strip().replace("\n", " ")
                if len(msg) > 200:
                    msg = msg[:200] + "…"
                lines.append(f"  ✗ {tc.name}: {msg}" if msg else f"  ✗ {tc.name}")
        return "\n".join(lines)


_SHELL_WRAPPER_RE = re.compile(r"^/bin/(?:ba)?sh\s+-\w*c\s+(.+)$")


def extract_agent_commands(agent_stdout: str, limit: int = 8000) -> str:
    """Extract shell commands from agent stdout.

    Handles three formats:
      1. Harbor JSONL (codex/claude-code): each line is a JSON object with
         command_execution items
      2. Plain text shell prompts: lines starting with $ or root@...#
      3. Fallback (qwen-coder etc.): agent's raw text output used as-is
    """
    if not agent_stdout:
        return ""
    commands: list[str] = []
    seen: set[str] = set()

    for line in agent_stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        cmd = _try_extract_jsonl(stripped) or _try_extract_plain(stripped)
        if cmd and cmd not in seen:
            seen.add(cmd)
            commands.append(cmd)

    if commands:
        result = "\n".join(commands)
        if len(result) > limit:
            result = result[-limit:]
            first_nl = result.find("\n")
            if first_nl > 0:
                result = result[first_nl + 1:]
        return result

    # Fallback: no structured commands found (e.g. qwen-coder).
    # Use the raw agent output directly — it typically contains
    # the agent's own summary of what it did.
    raw = agent_stdout.strip()
    if len(raw) > limit:
        raw = raw[-limit:]
        first_nl = raw.find("\n")
        if first_nl > 0:
            raw = raw[first_nl + 1:]
    return f"[Agent Output]\n{raw}" if raw else ""


def _try_extract_jsonl(line: str) -> str | None:
    """Parse a JSONL line from Harbor agent stdout and extract the command.

    Supports two formats:
      - Codex/Claude-Code: {"item": {"type": "command_execution", "command": "..."}}
      - OpenCode: {"type": "tool_use", "part": {"tool": "bash", "state": {"input": {"command": "..."}}}}
    """
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    item = obj.get("item")
    if isinstance(item, dict) and item.get("type") == "command_execution":
        raw_cmd = item.get("command", "")
        return _unwrap_shell(raw_cmd) if raw_cmd else None

    if obj.get("type") == "tool_use":
        part = obj.get("part") or {}
        if part.get("tool") == "bash":
            state = part.get("state") or {}
            inp = state.get("input") or {}
            raw_cmd = inp.get("command", "")
            return raw_cmd if raw_cmd else None

    return None


def _try_extract_plain(line: str) -> str | None:
    """Fallback: extract command from plain-text shell prompts."""
    if line.startswith("$ "):
        return line[2:].strip()
    if line.startswith("> "):
        return line[2:].strip()
    m = re.match(r"^root@[^:]+:[^#]*#\s+(.+)$", line)
    if m:
        return m.group(1).strip()
    return None


def _unwrap_shell(cmd: str) -> str:
    """Strip /bin/bash -lc wrapper that Harbor adds around every command."""
    m = _SHELL_WRAPPER_RE.match(cmd)
    if not m:
        return cmd
    inner = m.group(1).strip()
    if (inner.startswith("'") and inner.endswith("'")) or \
       (inner.startswith('"') and inner.endswith('"')):
        inner = inner[1:-1]
    return inner


def judge_trial(
    trial_dir: Path,
    *,
    agent_stdout_limit: int = 12000,
    test_stdout_limit: int = 8000,
) -> JudgmentResult:
    """Parse a single trial directory and return a structured judgment."""
    result_file = trial_dir / "result.json"
    if not result_file.exists():
        return JudgmentResult(
            task_name=trial_dir.name,
            trial_name=trial_dir.name,
            has_exception=True,
            exception_message="result.json not found",
        )

    data = json.loads(result_file.read_text())

    verifier = data.get("verifier_result") or {}
    rewards_obj = verifier.get("rewards") or {}
    reward = float(rewards_obj.get("reward", 0.0))

    exc_info = data.get("exception_info")
    has_exc = exc_info is not None
    exc_type = (exc_info or {}).get("exception_type", "")
    exc_msg = (exc_info or {}).get("exception_message", "")

    result = JudgmentResult(
        task_name=data.get("task_name", trial_dir.name),
        trial_name=data.get("trial_name", trial_dir.name),
        reward=reward,
        passed=reward >= 1.0,
        has_exception=has_exc,
        exception_type=exc_type,
        exception_message=exc_msg,
        result_json_raw=data,
    )

    _parse_ctrf(trial_dir / "verifier" / "ctrf.json", result)
    _read_test_stdout(trial_dir / "verifier" / "test-stdout.txt", result, limit=test_stdout_limit)

    raw_agent_stdout = _read_agent_stdout_raw(trial_dir / "agent", limit=0)  # read full
    result.agent_stdout_full = raw_agent_stdout
    result.agent_stdout = truncate_tail(raw_agent_stdout, agent_stdout_limit, "judge/agent_stdout")
    result.agent_commands = extract_agent_commands(raw_agent_stdout)

    return result


def _parse_ctrf(ctrf_path: Path, result: JudgmentResult) -> None:
    if not ctrf_path.exists():
        return
    try:
        data = json.loads(ctrf_path.read_text())
        result.ctrf_json_raw = data
        results = data.get("results", {})
        summary = results.get("summary", {})
        result.n_tests = summary.get("tests", 0)
        result.n_passed = summary.get("passed", 0)
        result.n_failed = summary.get("failed", 0)

        for tc in results.get("tests", []):
            result.test_cases.append(
                TestCaseResult(
                    name=tc.get("name", ""),
                    status=tc.get("status", ""),
                    duration=tc.get("duration", 0.0),
                    message=tc.get("message", ""),
                )
            )
    except (json.JSONDecodeError, KeyError):
        pass


def _read_test_stdout(path: Path, result: JudgmentResult, *, limit: int = 8000) -> None:
    if path.exists():
        raw = path.read_text(errors="replace")
        result.test_stdout_full = raw
        result.test_stdout = truncate_tail(
            raw, limit, f"judge/test_stdout[{result.task_name}]",
        )


_AGENT_OUTPUT_FILES = [
    "opencode.txt", "codex.txt", "aider.txt",
]


def _read_agent_stdout_raw(agent_dir: Path, *, limit: int = 12000) -> str:
    if not agent_dir.exists():
        return ""
    parts: list[str] = []
    for cmd_dir in sorted(agent_dir.glob("command-*")):
        stdout_file = cmd_dir / "stdout.txt"
        if stdout_file.exists():
            parts.append(stdout_file.read_text(errors="replace"))
    raw = "\n".join(parts)
    if not raw.strip():
        for name in _AGENT_OUTPUT_FILES:
            fallback = agent_dir / name
            if fallback.exists():
                raw = fallback.read_text(errors="replace")
                break
    if limit > 0:
        return truncate_tail(raw, limit, "judge/agent_stdout")
    return raw


def find_trial_dirs(job_dir: Path, task_name: str) -> list[Path]:
    """Find all trial directories for a given task name under a job directory."""
    # Harbor truncates task names to 32 characters in directory names
    prefix = task_name[:32]
    return sorted(
        d for d in job_dir.iterdir()
        if d.is_dir() and d.name.startswith(f"{prefix}__")
    )
