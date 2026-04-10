"""Main pipeline: Execute → Judge → Reflect loop with evolving exploration memo."""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spark_skills_gen.context import TaskContext
from spark_skills_gen.executor import (
    ExecutionConfig,
    cleanup_stale_docker_artifacts,
    cleanup_task_images,
    execute_task,
    list_available_tasks,
    prefetch_base_images,
)
from spark_skills_gen.judge import JudgmentResult, judge_trial
from spark_skills_gen.skill_evidence import build_skill_evidence
from spark_skills_gen.summarizer import (
    LLMCallRecord,
    generate_skill,
    reflect,
    save_skill_result,
)
from spark_skills_gen.prompts import VERIFIER_HINT_HEADER, VERIFIER_HINT_FOOTER
from spark_skills_gen.task_blacklist import load_blacklist
from spark_skills_gen.token_budgets import TokenBudgets, truncate_head
from spark_skills_gen.trajectory import TrajectoryWriter

log = logging.getLogger(__name__)

# ── Verifier answer stripping ────────────────────────────────────────────────
# Remove hardcoded expected values from test_outputs.py so the agent sees
# only the *structure* of the verifier (file names, formats, validation logic)
# but NOT the actual answers.


def _strip_verifier_answers(source: str) -> str:
    """Remove hardcoded answer data from verifier source, keeping structure.

    Strategy:
    1. Detect variable assignments whose names suggest answers
       (EXPECTED_*, REFERENCE_*, expected = {...}, etc.) and replace values.
    2. Detect _compute_reference_* functions and strip their bodies.
    3. Keep imports, test function signatures, assertions, and file-path logic.
    """
    lines = source.split("\n")
    result: list[str] = []
    skip_until_dedent = False
    skip_indent: int = 0
    in_large_assignment = False
    brace_depth = 0

    # Patterns for answer-bearing variable names (any indentation level)
    answer_var_pattern = re.compile(
        r"^(\s*)(EXPECTED|REFERENCE|ANSWER|ORACLE|GROUND_TRUTH|CORRECT|SOLUTION|GOLD)"
        r"[A-Z_]*\s*=\s*[\{\[\(\"']",
        re.IGNORECASE,
    )
    # Pattern for local `expected = {` or `expected = [` with dict/list literal
    local_expected_pattern = re.compile(
        r"^(\s+)expected\s*=\s*[\{\[]",
    )
    # Pattern for reference-computing functions
    ref_func_pattern = re.compile(
        r"^\s*def\s+(_compute_reference|_generate_expected|_build_oracle"
        r"|_get_ground_truth|_make_expected|_reference_)",
    )

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        # ── Skip function bodies that compute reference answers ──
        if skip_until_dedent:
            current_indent = len(line) - len(line.lstrip()) if stripped else skip_indent + 1
            if stripped and current_indent <= skip_indent:
                skip_until_dedent = False
                # Fall through to process this line normally
            else:
                i += 1
                continue

        # ── Detect reference-computing functions ──
        if ref_func_pattern.match(line):
            indent = len(line) - len(stripped)
            result.append(f"{line}  # [body stripped — contains reference answers]")
            result.append(f"{' ' * (indent + 4)}pass")
            skip_until_dedent = True
            skip_indent = indent
            i += 1
            continue

        # ── Track multi-line large literal assignments ──
        if in_large_assignment:
            brace_depth += line.count("{") + line.count("[") + line.count("(")
            brace_depth -= line.count("}") + line.count("]") + line.count(")")
            if brace_depth <= 0:
                in_large_assignment = False
                brace_depth = 0
            i += 1
            continue

        # ── Detect EXPECTED_RESULT = { ... } style assignments (any indent) ──
        m = answer_var_pattern.match(line)
        if m:
            indent = m.group(1)
            var_part = stripped.split("=")[0].strip()
            opens = line.count("{") + line.count("[") + line.count("(")
            closes = line.count("}") + line.count("]") + line.count(")")
            result.append(f"{indent}{var_part} = {{}}  # [answer data stripped]")
            if opens > closes:
                in_large_assignment = True
                brace_depth = opens - closes
            i += 1
            continue

        # ── Detect local `expected = {dict_literal}` inside test functions ──
        if local_expected_pattern.match(line):
            indent_str = line[: len(line) - len(stripped)]
            opens = line.count("{") + line.count("[")
            closes = line.count("}") + line.count("]")
            result.append(f"{indent_str}expected = {{}}  # [answer data stripped]")
            if opens > closes:
                in_large_assignment = True
                brace_depth = opens - closes
            i += 1
            continue

        # ── Keep everything else ──
        result.append(line)
        i += 1

    return "\n".join(result)

# ── Infrastructure failure detection ─────────────────────────────────────────
# Patterns that indicate the failure is caused by the runner / Docker / host
# environment rather than the agent or the task itself.  When detected the
# pipeline marks the attempt as INFRA_ERROR and stops retrying immediately —
# further attempts would just burn tokens for the same broken environment.

_INFRA_ERROR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"Agent setup failed with exit code", re.IGNORECASE),
    re.compile(r"setup failed", re.IGNORECASE),
    re.compile(r"exit code 100\b"),
    re.compile(r"exit code 127\b"),
    re.compile(r"exit code 137\b"),
    re.compile(r"No space left on device", re.IGNORECASE),
    re.compile(r"apt.*(?:update|install).*(?:fail|error)", re.IGNORECASE),
]

# Maximum consecutive INFRA_ERROR attempts before we give up on this task.
_MAX_INFRA_RETRIES = 2


def _is_infra_error(message: str) -> bool:
    """Return True if *message* matches any known infrastructure failure pattern."""
    return any(p.search(message) for p in _INFRA_ERROR_PATTERNS)


@dataclass
class PipelineConfig:
    spark_root: Path
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    result_dir: Path | None = None
    max_retries: int = 3
    parallelism: int = 4
    task_limit: int | None = None
    task_names: list[str] | None = None
    summary_model: str = ""
    summary_api_base: str | None = None
    summary_api_key: str | None = None
    token_budgets: TokenBudgets = field(default_factory=TokenBudgets)
    dashboard_port: int = 8765
    resume: bool = False
    shuffle: bool = False
    shared_result_dir: Path | None = None
    pdi_enabled: bool = False
    pdi_observe_only: bool = False

    def __post_init__(self) -> None:
        if not self.summary_model:
            self.summary_model = self.execution.model
        if self.result_dir is None:
            self.result_dir = self.spark_root / "skills_gen_result"


EVENT_TASK_START = "task_start"
EVENT_ATTEMPT_START = "attempt_start"
EVENT_ATTEMPT_DONE = "attempt_done"
EVENT_TASK_DONE = "task_done"
EVENT_SKILL_GENERATED = "skill_generated"
EVENT_PIPELINE_DONE = "pipeline_done"
EVENT_REFLECT_DONE = "reflect_done"
EVENT_TASK_CANCELLED = "task_cancelled"
EVENT_PDI_UPDATE = "pdi_update"


class EventBus:
    """Simple event bus that stores events and notifies listeners."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._listeners: list[Any] = []

    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        event = {"type": event_type, "ts": time.time(), **data}
        self._events.append(event)
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass

    def add_listener(self, fn: Any) -> None:
        self._listeners.append(fn)

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)


class SparkPipeline:
    """Two-stage iterative skill generation pipeline: Execute → Judge/Reflect."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.events = EventBus()
        self.results: dict[str, dict] = {}
        self._cancel_events: dict[str, threading.Event] = {}

    def cancel_task(self, task_name: str) -> None:
        """Mark a task for cancellation. The running loop will pick it up."""
        ev = self._cancel_events.get(task_name)
        if ev is not None:
            ev.set()
        log.info("Task %s marked for cancellation", task_name)

    def run(self) -> dict[str, dict]:
        """Run the full pipeline for all tasks."""
        cleanup_stale_docker_artifacts(
            self.config.spark_root,
            self.config.execution.staging_root,
        )
        prefetch_base_images(self.config.spark_root, self.config.execution.tasks_dir)

        tasks = self._resolve_tasks()
        log.info(
            "Pipeline starting: %d tasks, max_retries=%d, parallelism=%d",
            len(tasks), self.config.max_retries, self.config.parallelism,
        )

        if self.config.parallelism <= 1:
            for task_name in tasks:
                self._run_single_task(task_name)
        else:
            with ThreadPoolExecutor(max_workers=self.config.parallelism) as pool:
                futures = {pool.submit(self._run_single_task, t): t for t in tasks}
                for future in as_completed(futures):
                    task_name = futures[future]
                    try:
                        future.result()
                    except Exception:
                        log.exception("Task %s raised an exception", task_name)

        self.events.emit(EVENT_PIPELINE_DONE, {"tasks": list(self.results.keys())})
        self._save_summary()
        return self.results

    @staticmethod
    def _is_valid_skill(skill_path: Path) -> bool:
        """Check that a SKILL.md exists and holds genuine content."""
        if not skill_path.exists():
            return False
        content = skill_path.read_text().strip()
        if not content or content.startswith("[LLM ERROR]"):
            return False
        return True

    def _has_existing_skill(self, task_name: str) -> bool:
        """Return True if *task_name* already has a valid SKILL.md."""
        if self.config.shared_result_dir:
            if self._is_valid_skill(
                self.config.shared_result_dir / task_name / "SKILL.md"
            ):
                return True
        if self.config.resume:
            if self._is_valid_skill(
                self.config.result_dir
                / self.config.execution.model
                / task_name
                / "SKILL.md"
            ):
                return True
        return False

    def _effective_save_params(self) -> tuple[Path, str]:
        """Return (result_base, model_subdir) for persisting results."""
        if self.config.shared_result_dir:
            return self.config.shared_result_dir, ""
        return self.config.result_dir, self.config.execution.model

    def _run_single_task(self, task_name: str) -> None:
        """Execute the two-stage loop for one task."""
        from spark_skills_gen.context import PDITracker

        ctx = TaskContext(
            task_name=task_name,
            max_retries=self.config.max_retries,
            pdi_tracker=PDITracker(
                enabled=self.config.pdi_enabled,
                observe_only=self.config.pdi_observe_only,
            ),
        )

        # ── Trajectory writer ──
        result_base, model_for_save = self._effective_save_params()
        traj_dir = result_base / model_for_save / task_name if model_for_save else result_base / task_name
        tw = TrajectoryWriter(traj_dir)
        task_start_time = time.time()
        cumulative_input_tokens = 0
        cumulative_output_tokens = 0

        self.events.emit(EVENT_TASK_START, {
            "task": task_name, "max_retries": self.config.max_retries,
        })

        # Create a per-task cancel event
        cancel_event = threading.Event()
        self._cancel_events[task_name] = cancel_event

        instruction = self._read_instruction(task_name)
        verifier_hint = self._read_verifier_hint(task_name)

        while ctx.should_retry:
            # ── Check cancellation ──
            if cancel_event.is_set():
                log.info("Task %s cancelled by user", task_name)
                self.events.emit(EVENT_TASK_CANCELLED, {"task": task_name})
                break

            attempt = ctx.current_attempt
            self.events.emit(EVENT_ATTEMPT_START, {
                "task": task_name, "attempt": attempt,
            })

            # ── Stage 1: Execute ──
            retry_memo = ctx.build_injection() if attempt > 0 else ""
            # Always inject verifier hint + retry memo (if any)
            injection = verifier_hint + retry_memo
            has_retries_left = (ctx.current_attempt + 1) < self.config.max_retries

            exec_result = execute_task(
                task_name=task_name,
                attempt=attempt,
                config=self.config.execution,
                spark_root=self.config.spark_root,
                retry_context=injection,
                cancel_event=cancel_event,
                keep_images=has_retries_left,
            )

            # ── Check cancellation after execution ──
            if cancel_event.is_set():
                log.info("Task %s cancelled by user (post-execution)", task_name)
                self.events.emit(EVENT_TASK_CANCELLED, {"task": task_name})
                break

            if not exec_result.success or exec_result.trial_dir is None:
                error_msg = exec_result.error or "unknown execution error"
                is_infra = _is_infra_error(error_msg)
                status_label = "INFRA_ERROR" if is_infra else "ERROR"

                ctx.add_attempt(
                    status=status_label,
                    reward=0.0,
                    agent_commands="",
                    test_summary=f"Execution failed: {error_msg}",
                )
                self.events.emit(EVENT_ATTEMPT_DONE, {
                    "task": task_name, "attempt": attempt,
                    "status": status_label, "reward": 0.0,
                    "error": error_msg,
                })

                # Record execution error in trajectory
                tw.record_execution_result(
                    attempt=attempt,
                    success=False,
                    reward=0.0,
                    status="ERROR",
                    n_passed=0,
                    n_tests=0,
                    agent_stdout_full="",
                    test_stdout_full="",
                    agent_commands="",
                    test_summary=f"Execution failed: {error_msg}",
                    result_json=None,
                    ctrf_json=None,
                    retry_injection=injection,
                    error=error_msg,
                )

                if not ctx.exploration_memo:
                    memo = (
                        "## Exploration Memo (1 failed attempt)\n\n"
                        "### Attempts Log\n"
                        f"- #1: Execution error — {error_msg}\n\n"
                        "### Next Strategy\n"
                        "Fix the execution environment issues first."
                    )
                    ctx.update_memo(memo)

                # ── INFRA_ERROR early-exit: stop retrying if infrastructure
                # is broken — further attempts will just fail the same way.
                if is_infra:
                    infra_count = sum(
                        1 for a in ctx.attempts if a.status == "INFRA_ERROR"
                    )
                    if infra_count >= _MAX_INFRA_RETRIES:
                        log.warning(
                            "Task %s hit %d consecutive INFRA_ERROR(s) — "
                            "aborting retries (setup/environment broken).",
                            task_name, infra_count,
                        )
                        break

                continue

            # ── Stage 2: Judge ──
            judgment = judge_trial(
                exec_result.trial_dir,
                agent_stdout_limit=self.config.token_budgets.judge_chars.agent_stdout_read,
                test_stdout_limit=self.config.token_budgets.judge_chars.test_stdout_read,
            )

            # ── Check for infra failure that slipped through execution ──
            # (e.g. setup failed but trial_dir was still created)
            if judgment.has_exception and _is_infra_error(judgment.exception_message):
                ctx.add_attempt(
                    status="INFRA_ERROR",
                    reward=0.0,
                    agent_commands=judgment.agent_commands,
                    test_summary=f"Infrastructure error: {judgment.exception_message}",
                )
                self.events.emit(EVENT_ATTEMPT_DONE, {
                    "task": task_name, "attempt": attempt,
                    "status": "INFRA_ERROR", "reward": 0.0,
                    "error": judgment.exception_message,
                })
                tw.record_execution_result(
                    attempt=attempt,
                    success=False,
                    reward=0.0,
                    status="INFRA_ERROR",
                    n_passed=0,
                    n_tests=0,
                    agent_stdout_full=judgment.agent_stdout_full,
                    test_stdout_full=judgment.test_stdout_full,
                    agent_commands=judgment.agent_commands,
                    test_summary=judgment.test_summary,
                    result_json=judgment.result_json_raw,
                    ctrf_json=judgment.ctrf_json_raw,
                    retry_injection=injection,
                    error=judgment.exception_message,
                )
                infra_count = sum(
                    1 for a in ctx.attempts if a.status == "INFRA_ERROR"
                )
                if infra_count >= _MAX_INFRA_RETRIES:
                    log.warning(
                        "Task %s hit %d INFRA_ERROR(s) after judge — "
                        "aborting retries (setup/environment broken).",
                        task_name, infra_count,
                    )
                    break
                continue

            # Record execution result in trajectory (with full data)
            tw.record_execution_result(
                attempt=attempt,
                success=judgment.passed,
                reward=judgment.reward,
                status=judgment.status,
                n_passed=judgment.n_passed,
                n_tests=judgment.n_tests,
                agent_stdout_full=judgment.agent_stdout_full,
                test_stdout_full=judgment.test_stdout_full,
                agent_commands=judgment.agent_commands,
                test_summary=judgment.test_summary,
                result_json=judgment.result_json_raw,
                ctrf_json=judgment.ctrf_json_raw,
                retry_injection=injection,
            )

            if judgment.passed:
                ctx.add_attempt(
                    status="PASS",
                    reward=judgment.reward,
                    agent_commands=judgment.agent_commands,
                    test_summary=judgment.test_summary,
                    n_passed=judgment.n_passed,
                    n_tests=judgment.n_tests,
                )
                self.events.emit(EVENT_ATTEMPT_DONE, {
                    "task": task_name, "attempt": attempt,
                    "status": "PASS", "reward": judgment.reward,
                    "agent_commands": judgment.agent_commands,
                    "test_summary": judgment.test_summary,
                    "n_passed": judgment.n_passed,
                    "n_tests": judgment.n_tests,
                })

                # ── Generate Skill ──
                env_info = self._read_environment_info(task_name)
                evidence = build_skill_evidence(
                    task_name=task_name,
                    instruction=instruction,
                    judgment=judgment,
                    attempts=list(ctx.attempts),
                    exploration_memo=ctx.exploration_memo,
                    memo_history=list(ctx.memo_history),
                    environment_info=env_info,
                    trial_dir=exec_result.trial_dir,
                    budgets=self.config.token_budgets,
                )
                skill_content, skill_record = generate_skill(
                    evidence=evidence,
                    model=self.config.summary_model,
                    api_base=self.config.summary_api_base,
                    api_key=self.config.summary_api_key,
                    budgets=self.config.token_budgets,
                )

                # Track tokens
                cumulative_input_tokens += skill_record.usage.input_tokens
                cumulative_output_tokens += skill_record.usage.output_tokens

                # Record skill generation in trajectory
                tw.record_skill_gen_call(
                    attempt=attempt,
                    model=self.config.summary_model,
                    system_prompt=skill_record.system_prompt,
                    user_prompt=skill_record.user_prompt,
                    response=skill_record.response,
                    usage=skill_record.usage,
                )

                save_skill_result(
                    result_base=result_base,
                    model_name=model_for_save,
                    task_name=task_name,
                    skill_content=skill_content,
                    context_dict=ctx.to_dict(),
                    success=True,
                )

                self.events.emit(EVENT_SKILL_GENERATED, {
                    "task": task_name,
                    "skill_content": skill_content,
                    "llm_call": {
                        "label": skill_record.label,
                        "system_prompt": skill_record.system_prompt,
                        "user_prompt": skill_record.user_prompt,
                        "response": skill_record.response,
                    },
                })
                break

            # ── Reflect: rewrite exploration memo ──
            ctx.add_attempt(
                status=judgment.status,
                reward=judgment.reward,
                agent_commands=judgment.agent_commands,
                test_summary=judgment.test_summary,
                n_passed=judgment.n_passed,
                n_tests=judgment.n_tests,
            )

            memo_before = ctx.exploration_memo

            new_memo, reflect_record = reflect(
                task_name=task_name,
                attempt=attempt,
                status=judgment.status,
                reward=judgment.reward,
                n_passed=judgment.n_passed,
                n_tests=judgment.n_tests,
                agent_commands=judgment.agent_commands,
                test_summary=judgment.test_summary,
                exploration_memo=ctx.exploration_memo,
                model=self.config.summary_model,
                api_base=self.config.summary_api_base,
                api_key=self.config.summary_api_key,
                budgets=self.config.token_budgets,
            )

            ctx.update_memo(new_memo)

            # ── PDI check (runs even when disabled, for observation) ──
            reflect_step = len(ctx.memo_history)  # 0-indexed
            previous_test_summary = ""
            if len(ctx.attempts) >= 2:
                previous_test_summary = ctx.attempts[-2].test_summary
            pdi_snapshot = ctx.pdi_tracker.compute(
                step=reflect_step,
                current_memo=new_memo,
                previous_memo=memo_before,
                agent_commands=judgment.agent_commands,
                test_summary=judgment.test_summary,
                previous_test_summary=previous_test_summary,
            )
            self.events.emit(EVENT_PDI_UPDATE, {
                "task": task_name,
                "attempt": attempt,
                **pdi_snapshot.to_dict(),
                "pdi_enabled": self.config.pdi_enabled,
            })

            # Record PDI snapshot in trajectory
            tw.record_pdi_snapshot(
                attempt=attempt,
                snapshot=pdi_snapshot.to_dict(),
            )

            # Track tokens
            cumulative_input_tokens += reflect_record.usage.input_tokens
            cumulative_output_tokens += reflect_record.usage.output_tokens

            # Record reflect call in trajectory
            tw.record_reflect_call(
                attempt=attempt,
                model=self.config.summary_model,
                system_prompt=reflect_record.system_prompt,
                user_prompt=reflect_record.user_prompt,
                response=reflect_record.response,
                usage=reflect_record.usage,
                memo_before=memo_before,
                memo_after=new_memo,
            )

            attempt_event: dict = {
                "task": task_name, "attempt": attempt,
                "status": judgment.status, "reward": judgment.reward,
                "agent_commands": judgment.agent_commands,
                "test_summary": judgment.test_summary,
                "n_passed": judgment.n_passed,
                "n_tests": judgment.n_tests,
            }
            if judgment.has_exception and judgment.exception_message:
                attempt_event["error"] = judgment.exception_message
            self.events.emit(EVENT_ATTEMPT_DONE, attempt_event)
            self.events.emit(EVENT_REFLECT_DONE, {
                "task": task_name, "attempt": attempt,
                "memo": new_memo,
                "llm_call": {
                    "label": reflect_record.label,
                    "system_prompt": reflect_record.system_prompt,
                    "user_prompt": reflect_record.user_prompt,
                    "response": reflect_record.response,
                },
            })

        # ── Finalize ──
        is_cancelled = cancel_event.is_set()
        self._cancel_events.pop(task_name, None)

        # Clean up Docker images now that all retries are done
        cleanup_task_images(task_name)

        if not ctx.is_success and not is_cancelled:
            save_skill_result(
                result_base=result_base,
                model_name=model_for_save,
                task_name=task_name,
                skill_content=None,
                context_dict=ctx.to_dict(),
                success=False,
            )

        # Record task summary in trajectory
        tw.record_task_summary(
            task_name=task_name,
            success=ctx.is_success,
            total_attempts=ctx.current_attempt,
            reward_trajectory=[a.reward for a in ctx.attempts],
            status_trajectory=[a.status for a in ctx.attempts],
            total_time_s=round(time.time() - task_start_time, 2),
            total_input_tokens=cumulative_input_tokens,
            total_output_tokens=cumulative_output_tokens,
            exploration_config={
                "model": self.config.execution.model,
                "agent": self.config.execution.agent,
                "summary_model": self.config.summary_model,
                "max_retries": self.config.max_retries,
            },
        )

        if is_cancelled:
            task_result = {
                "task": task_name,
                "success": False,
                "skipped": True,
                "attempts": ctx.current_attempt,
                "final_status": "SKIPPED",
                "final_reward": 0.0,
            }
        else:
            task_result = {
                "task": task_name,
                "success": ctx.is_success,
                "attempts": ctx.current_attempt,
                "final_status": ctx.attempts[-1].status if ctx.attempts else "NONE",
                "final_reward": ctx.attempts[-1].reward if ctx.attempts else 0.0,
            }
        self.results[task_name] = task_result
        self.events.emit(EVENT_TASK_DONE, task_result)

    def _resolve_tasks(self) -> list[str]:
        if self.config.task_names:
            tasks = self.config.task_names
        else:
            tasks = list_available_tasks(
                self.config.spark_root, self.config.execution.tasks_dir,
            )

        # ── Apply blacklist ──
        blacklist = load_blacklist()
        if blacklist:
            before = len(tasks)
            tasks = [t for t in tasks if t not in blacklist]
            skipped = before - len(tasks)
            if skipped:
                log.info("Blacklisted %d task(s), %d remaining", skipped, len(tasks))

        if self.config.shared_result_dir or self.config.resume:
            before = len(tasks)
            tasks = [t for t in tasks if not self._has_existing_skill(t)]
            skipped = before - len(tasks)
            if skipped:
                log.info(
                    "Skipping %d tasks with existing valid skills, %d remaining",
                    skipped, len(tasks),
                )

        if self.config.shuffle:
            random.shuffle(tasks)

        if self.config.task_limit:
            tasks = tasks[: self.config.task_limit]
        return tasks

    def _read_instruction(self, task_name: str) -> str:
        path = (
            self.config.spark_root
            / self.config.execution.tasks_dir
            / task_name
            / "instruction.md"
        )
        if path.exists():
            return path.read_text()
        return ""

    def _read_environment_info(self, task_name: str) -> str:
        """Read Dockerfile for context."""
        task_dir = (
            self.config.spark_root
            / self.config.execution.tasks_dir
            / task_name
        )
        parts: list[str] = []

        dockerfile = task_dir / "environment" / "Dockerfile"
        if dockerfile.exists():
            raw = dockerfile.read_text()
            trimmed = truncate_head(
                raw, self.config.token_budgets.input_chars.environment_info,
                f"dockerfile[{task_name}]",
            )
            parts.append(f"### Dockerfile:\n{trimmed}")

        return "\n".join(parts)

    def _read_verifier_hint(self, task_name: str) -> str:
        """Read tests/test_outputs.py, strip hardcoded answers, and wrap as a hint.

        We extract only the *structural* information the agent needs:
        - test function names and signatures
        - expected file names, formats, column names
        - validation logic (tolerances, schemas, comparison methods)
        - library imports (hints at which approach to use)

        We aggressively strip:
        - Large literal dicts/lists that contain expected answers
        - Hardcoded numeric constants that look like reference values
        - Any variable named EXPECTED_*, REFERENCE_*, ANSWER_*, ORACLE_*
        """
        task_dir = (
            self.config.spark_root
            / self.config.execution.tasks_dir
            / task_name
        )
        test_file = task_dir / "tests" / "test_outputs.py"
        if not test_file.exists():
            return ""
        try:
            raw = test_file.read_text()
        except OSError:
            return ""
        if not raw.strip():
            return ""

        content = _strip_verifier_answers(raw)

        # Truncate if still excessively large
        max_chars = 6000
        if len(content) > max_chars:
            content = content[:max_chars] + "\n# ... (truncated) ..."
        return f"{VERIFIER_HINT_HEADER}{content}{VERIFIER_HINT_FOOTER}"

    def _save_summary(self) -> None:
        if self.config.shared_result_dir:
            summary_dir = self.config.shared_result_dir
        else:
            summary_dir = self.config.result_dir / self.config.execution.model
        summary_dir.mkdir(parents=True, exist_ok=True)

        summary = {
            "model": self.config.execution.model,
            "agent": self.config.execution.agent,
            "max_retries": self.config.max_retries,
            "total_tasks": len(self.results),
            "passed": sum(1 for r in self.results.values() if r["success"]),
            "failed": sum(
                1 for r in self.results.values()
                if not r["success"] and r.get("final_status") not in ("SKIPPED", "INFRA_ERROR")
            ),
            "infra_error": sum(
                1 for r in self.results.values()
                if r.get("final_status") == "INFRA_ERROR"
            ),
            "tasks": self.results,
        }

        (summary_dir / "pipeline_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False)
        )
        log.info(
            "Pipeline complete — %d/%d tasks passed",
            summary["passed"], summary["total_tasks"],
        )
