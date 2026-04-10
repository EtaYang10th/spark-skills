"""Validate generated Harbor tasks by running Harbor and parsing results."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
from typing import Any
import uuid


@dataclass(slots=True)
class CommandResult:
    """Captured output from one subprocess invocation."""

    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass(slots=True)
class ValidationResult:
    """Combined Harbor validation state for a generated task."""

    task_check: CommandResult
    oracle_run: CommandResult | None
    passed: bool
    reward: float
    trial_dir: Path | None = None
    verifier_stdout: str = ""
    failed_tests: list[str] = field(default_factory=list)
    exception_info: str = ""
    task_check_warning: str = ""

    def to_feedback_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reward": self.reward,
            "trial_dir": str(self.trial_dir) if self.trial_dir else None,
            "failed_tests": self.failed_tests,
            "exception_info": self.exception_info,
            "verifier_stdout": self.verifier_stdout,
            "task_check_warning": self.task_check_warning,
            "task_check": self.task_check.to_dict(),
            "oracle_run": self.oracle_run.to_dict() if self.oracle_run else None,
        }

    def to_repair_feedback(self) -> dict[str, Any]:
        feedback = {
            "passed": self.passed,
            "reward": self.reward,
            "failed_tests": self.failed_tests,
            "exception_info": self.exception_info,
            "verifier_stdout_tail": self.verifier_stdout[-3000:],
            "task_check_warning": self.task_check_warning,
        }
        if self.oracle_run is not None:
            feedback["oracle_stdout_tail"] = self.oracle_run.stdout[-1500:]
            feedback["oracle_stderr_tail"] = self.oracle_run.stderr[-1500:]
        if self.task_check_warning:
            return feedback

        feedback["task_check_stderr_tail"] = self.task_check.stderr[-1500:]
        return feedback


def validate_task(task_dir: Path, spark_root: Path, output_dir: Path, timeout_sec: int = 1800) -> ValidationResult:
    """Run `harbor tasks check` and the oracle on a generated task."""
    env = _build_env(spark_root)
    task_check = _run_command(
        ["uv", "run", "harbor", "tasks", "check", str(task_dir)],
        cwd=spark_root,
        env=env,
        timeout_sec=timeout_sec,
    )
    task_check_warning = ""
    if task_check.returncode != 0:
        task_check_warning = _classify_nonblocking_task_check_failure(task_check.stderr)
        if not task_check_warning:
            return ValidationResult(
                task_check=task_check,
                oracle_run=None,
                passed=False,
                reward=0.0,
                exception_info="harbor tasks check failed",
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    job_name = f"spark-task-gen/oracle/{task_dir.name}/{uuid.uuid4().hex[:8]}"
    oracle_run = _run_command(
        [
            "uv",
            "run",
            "harbor",
            "run",
            "-p",
            str(task_dir),
            "-a",
            "oracle",
            "-o",
            str(output_dir),
            "--job-name",
            job_name,
        ],
        cwd=spark_root,
        env=env,
        timeout_sec=timeout_sec,
    )

    trial_dir = _find_latest_trial(output_dir / job_name, task_dir.name)
    reward, failed_tests, verifier_stdout, exception_info = _parse_trial(trial_dir)
    passed = oracle_run.returncode == 0 and reward >= 1.0 and trial_dir is not None
    return ValidationResult(
        task_check=task_check,
        oracle_run=oracle_run,
        passed=passed,
        reward=reward,
        trial_dir=trial_dir,
        verifier_stdout=verifier_stdout,
        failed_tests=failed_tests,
        exception_info=exception_info,
        task_check_warning=task_check_warning,
    )


def _run_command(command: list[str], cwd: Path, env: dict[str, str], timeout_sec: int) -> CommandResult:
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
        return CommandResult(
            command=command,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=command,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + f"\nTimed out after {timeout_sec}s",
        )


def _build_env(spark_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env_file = spark_root / ".env"
    if not env_file.exists():
        return env

    for raw_line in env_file.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("'\"")
    return env


def _find_latest_trial(job_dir: Path, task_name: str) -> Path | None:
    if not job_dir.exists():
        return None
    # Harbor truncates task names to 32 characters in directory names
    prefix = f"{task_name}__"
    truncated_prefix = f"{task_name[:32]}__"
    candidates = [
        entry for entry in job_dir.iterdir()
        if entry.is_dir() and (entry.name.startswith(prefix) or entry.name.startswith(truncated_prefix))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def _parse_trial(trial_dir: Path | None) -> tuple[float, list[str], str, str]:
    if trial_dir is None:
        return 0.0, [], "", "trial directory not found"

    result_path = trial_dir / "result.json"
    reward = 0.0
    exception_info = ""
    if result_path.exists():
        result_data = json.loads(result_path.read_text())
        verifier_result = result_data.get("verifier_result") or {}
        rewards = verifier_result.get("rewards") or {}
        reward = float(rewards.get("reward", 0.0))
        exception_data = result_data.get("exception_info")
        if exception_data:
            exception_type = exception_data.get("exception_type", "")
            exception_message = exception_data.get("exception_message", "")
            exception_info = f"{exception_type}: {exception_message}".strip(": ")

    ctrf_path = trial_dir / "verifier" / "ctrf.json"
    failed_tests: list[str] = []
    if ctrf_path.exists():
        ctrf_data = json.loads(ctrf_path.read_text())
        for test in (ctrf_data.get("results", {}) or {}).get("tests", []):
            if test.get("status") == "failed":
                name = str(test.get("name", ""))
                message = str(test.get("message", ""))
                failed_tests.append(f"{name}: {message}" if message else name)

    stdout_path = trial_dir / "verifier" / "test-stdout.txt"
    verifier_stdout = stdout_path.read_text(errors="replace")[:8000] if stdout_path.exists() else ""
    return reward, failed_tests, verifier_stdout, exception_info


def _classify_nonblocking_task_check_failure(stderr: str) -> str:
    if "ModuleNotFoundError: No module named 'claude_agent_sdk'" in stderr:
        return "harbor tasks check failed because the local Harbor quality checker is missing claude_agent_sdk; proceeding to oracle validation"
    return ""
