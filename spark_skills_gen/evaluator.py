"""Evaluate whether generated SKILL.md files improve Harbor task performance."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

from spark_skills_gen.judge import find_trial_dirs
from spark_skills_gen.task_blacklist import load_blacklist

log = logging.getLogger(__name__)

_GENERATED_SKILL_SENTINEL = "# SPARK generated skill injection"
_GENERATED_SKILL_DIR = "spark-generated-skills"
_GENERATED_SKILL_PATHS = (
    "/root/.claude/skills",
    "/etc/claude-code/.claude/skills",
    "/root/.codex/skills",
    "/root/.opencode/skill",
    "/root/.goose/skills",
    "/root/.factory/skills",
    "/root/.agents/skills",
    "/root/.gemini/skills",
)
_GENERATED_TASK_SOURCE = "tasks-with-generated-skills"

# Instruction-level hint appended to instruction.md so that agents without
# built-in skill discovery are explicitly told to load the skill.
# Kept agent-agnostic: only references the universal fallback path.
_SKILL_INSTRUCTION_HINT = """\

---

> **IMPORTANT — A reference skill document (`SKILL.md`) has been provided for this task.**
>
> Before you begin, read it:
> ```
> cat /root/spark-skills/*/SKILL.md
> ```
> It contains task-specific strategies, code templates, and common pitfalls.
> Follow its guidance carefully.
"""
_PHASE_JOB_LABELS = {
    "baseline": "baseline",
    "with_generated_skills": "with-generated-skills",
    "with_human_skills": "with-human-skills",
}


@dataclass(frozen=True)
class SkillTask:
    task_name: str
    source_task_dir: Path
    generated_skill_md: Path


@dataclass
class EvaluationConfig:
    spark_root: Path
    agent: str = "qwen-coder"
    model: str = "qwen3-coder-next"
    tasks_dir: str = "tasks_no_skills_generate"
    skills_result_dir: Path | None = None
    skill_source_model: str = ""
    output_dir: Path | None = None
    eval_result_dir: Path | None = None
    staging_root: Path | None = None
    parallelism: int = 4
    agent_timeout_multiplier: float = 0.5
    task_limit: int | None = None
    task_names: list[str] | None = None
    env_overrides: dict[str, str] = field(default_factory=dict)
    agent_kwargs: dict[str, str] = field(default_factory=dict)
    resume: bool = False
    human_skills_dir: str = "tasks"
    phases: set[str] = field(default_factory=lambda: {"baseline", "generated", "human"})
    ref_eval_result_dir: Path | None = None  # cross-reference baseline/human from another eval dir (PDI mode)

    def __post_init__(self) -> None:
        if self.parallelism < 1:
            raise ValueError("parallelism must be at least 1")

        if not self.skill_source_model:
            self.skill_source_model = self.model

        self.skills_result_dir = _resolve_path(
            self.spark_root,
            self.skills_result_dir or Path("./spark_skills_gen/skills_gen_result"),
        )
        self.output_dir = _resolve_path(
            self.spark_root,
            self.output_dir or Path("./spark-jobs"),
        )
        self.eval_result_dir = _resolve_path(
            self.spark_root,
            self.eval_result_dir or Path("./spark_skills_gen/skills_eval_result"),
        )
        self.staging_root = _resolve_path(
            self.spark_root,
            self.staging_root or Path("./save/.spark-skill-eval"),
        )

        if self.ref_eval_result_dir is not None:
            self.ref_eval_result_dir = _resolve_path(
                self.spark_root, self.ref_eval_result_dir,
            )

        if self.task_names:
            self.task_names = list(dict.fromkeys(self.task_names))


@dataclass(frozen=True)
class HarborJob:
    label: str
    job_name: str
    job_dir: Path


def evaluate_generated_skills(config: EvaluationConfig) -> dict:
    skill_tasks = discover_skill_tasks(config)
    all_task_names = [task.task_name for task in skill_tasks]

    # --- Determine human-skills scope ---
    human_skills_root = config.spark_root / config.human_skills_dir
    human_task_names = (
        [t for t in all_task_names if (human_skills_root / t).is_dir()]
        if human_skills_root.is_dir()
        else []
    )

    # --- Resume: load previous per-task results ---
    prev_report: dict | None = None
    resumed_from: str | None = None
    cached: dict[str, dict[str, dict]] = {
        "baseline": {},
        "with_generated_skills": {},
        "with_human_skills": {},
    }
    if config.resume:
        prev_report, cached, resumed_from = _load_resume_state(
            config=config,
            skill_tasks=skill_tasks,
            human_task_names=human_task_names,
        )

    # --- Cross-reference: load baseline from a reference eval dir (PDI mode) ---
    # In PDI mode the "baseline" for comparison is the *without-PDI* generated
    # result, i.e. ref's "with_generated_skills" per-task results.  We FORCE
    # overwrite cached["baseline"] so the report's delta = PDI_generated − orig_generated.
    if config.ref_eval_result_dir is not None:
        ref_summary_file = config.ref_eval_result_dir / config.model / "summary.json"
        if ref_summary_file.is_file():
            try:
                ref_report = json.loads(ref_summary_file.read_text())
                task_name_set = set(all_task_names)
                # Use the ref's with_generated_skills as our baseline (without-PDI)
                ref_gen_tasks = (ref_report.get("with_generated_skills") or {}).get("tasks", {})
                loaded = 0
                for task_name, task_result in ref_gen_tasks.items():
                    if task_name in task_name_set:
                        cached["baseline"][task_name] = task_result  # force overwrite
                        loaded += 1
                log.info(
                    "Ref eval: loaded %d without-PDI generated results as baseline from %s",
                    loaded, ref_summary_file,
                )
                if not resumed_from:
                    resumed_from = f"ref:{ref_summary_file}"
            except (json.JSONDecodeError, OSError, KeyError) as exc:
                log.warning("Failed to load reference eval report %s: %s", ref_summary_file, exc)
        else:
            log.warning("Reference eval report not found: %s", ref_summary_file)

    # --- Tasks that still need Harbor runs ---
    run_baseline = "baseline" in config.phases
    run_generated = "generated" in config.phases
    run_human = "human" in config.phases

    new_baseline = [t for t in all_task_names if t not in cached["baseline"]] if run_baseline else []
    new_generated = [t for t in all_task_names if t not in cached["with_generated_skills"]] if run_generated else []
    new_human = [t for t in human_task_names if t not in cached["with_human_skills"]] if run_human else []

    staging_cleanup_path = config.staging_root / "current"

    n_phases = 4 + bool(new_generated)  # baseline + [staging] + generated + human + report

    log.info(
        "Evaluating %d tasks (new: baseline=%d, generated=%d, human=%d) "
        "with generated skills from %s",
        len(all_task_names), len(new_baseline), len(new_generated), len(new_human),
        config.skills_result_dir / config.skill_source_model,
    )

    phases = tqdm(
        total=max(n_phases, 2),
        desc="Eval progress",
        unit="phase",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}] {postfix}",
    )

    report: dict | None = None
    cleanup_error: str | None = None
    staged_tasks_dir: Path | None = None
    try:
        # ── Phase: baseline ──
        baseline_results: dict[str, dict] = dict(cached["baseline"])
        if new_baseline:
            phases.set_postfix_str("baseline")
            baseline_job = run_harbor_job(
                tasks_root=config.spark_root / config.tasks_dir,
                task_names=new_baseline,
                label="baseline",
                config=config,
            )
            baseline_results.update(_judge_harbor_tasks(baseline_job, new_baseline))
        phases.update(1)

        # ── Phase: with-generated-skills ──
        generated_results: dict[str, dict] = dict(cached["with_generated_skills"])
        if new_generated:
            phases.set_postfix_str("staging tasks")
            new_skill_tasks = [t for t in skill_tasks if t.task_name in set(new_generated)]
            staged_tasks_dir = stage_tasks_with_generated_skills(new_skill_tasks, config)
            phases.update(1)

            phases.set_postfix_str("with-generated-skills")
            skilled_job = run_harbor_job(
                tasks_root=staged_tasks_dir,
                task_names=new_generated,
                label="with-generated-skills",
                config=config,
            )
            generated_results.update(_judge_harbor_tasks(skilled_job, new_generated))
        phases.update(1)

        # ── Phase: with-human-skills ──
        human_results: dict[str, dict] = dict(cached["with_human_skills"])
        if new_human:
            phases.set_postfix_str("with-human-skills")
            human_job = run_harbor_job(
                tasks_root=human_skills_root,
                task_names=new_human,
                label="with-human-skills",
                config=config,
            )
            human_results.update(_judge_harbor_tasks(human_job, new_human))
        phases.update(1)

        # ── Summaries ──
        phases.set_postfix_str("report")
        baseline_summary = _build_phase_summary(
            "baseline",
            {t: baseline_results[t] for t in all_task_names if t in baseline_results},
        )
        generated_summary = _build_phase_summary(
            "with-generated-skills",
            {t: generated_results[t] for t in all_task_names if t in generated_results},
        )
        human_summary: dict | None = None
        if human_task_names:
            human_summary = _build_phase_summary(
                "with-human-skills",
                {t: human_results[t] for t in human_task_names if t in human_results},
            )

        report = build_evaluation_report(
            config=config,
            skill_tasks=skill_tasks,
            staged_tasks_dir=staged_tasks_dir,
            baseline_summary=baseline_summary,
            skilled_summary=generated_summary,
            human_summary=human_summary,
            human_task_names=human_task_names,
            resumed_from=resumed_from,
        )
        phases.update(1)
    finally:
        phases.close()
        cleanup_error = _cleanup_directory_tree(staging_cleanup_path)

    if report is None:
        raise RuntimeError("Evaluation finished without producing a report.")

    report["staging_cleanup_path"] = str(staging_cleanup_path)
    report["staging_cleaned_up"] = cleanup_error is None
    if cleanup_error is not None:
        report["staging_cleanup_error"] = cleanup_error

    save_evaluation_report(report, config)
    if cleanup_error is not None:
        raise RuntimeError(f"Failed to remove temporary staging directory {staging_cleanup_path}: {cleanup_error}")
    return report


def discover_skill_tasks(config: EvaluationConfig) -> list[SkillTask]:
    model_dir = config.skills_result_dir / config.skill_source_model
    if not model_dir.is_dir():
        raise FileNotFoundError(f"Skill result model directory does not exist: {model_dir}")

    available_skill_files: dict[str, Path] = {}
    for child in sorted(model_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if skill_md.is_file():
            available_skill_files[child.name] = skill_md

    if config.task_names:
        missing = [name for name in config.task_names if name not in available_skill_files]
        if missing:
            missing_text = ", ".join(missing)
            raise ValueError(
                "Requested tasks do not have generated SKILL.md files under "
                f"{model_dir}: {missing_text}"
            )
        selected_names = config.task_names
    else:
        selected_names = sorted(available_skill_files)

    if config.task_limit is not None:
        selected_names = selected_names[: config.task_limit]

    # ── Apply blacklist ──
    blacklist = load_blacklist()
    if blacklist:
        before = len(selected_names)
        selected_names = [n for n in selected_names if n not in blacklist]
        skipped = before - len(selected_names)
        if skipped:
            log.info("Blacklisted %d task(s) from evaluation, %d remaining", skipped, len(selected_names))

    if not selected_names:
        raise RuntimeError(
            "No comparable tasks found: no generated SKILL.md files matched the requested selection."
        )

    skill_tasks: list[SkillTask] = []
    for task_name in selected_names:
        source_task_dir = config.spark_root / config.tasks_dir / task_name
        if not source_task_dir.is_dir():
            raise FileNotFoundError(f"Source task directory does not exist: {source_task_dir}")
        skill_tasks.append(
            SkillTask(
                task_name=task_name,
                source_task_dir=source_task_dir,
                generated_skill_md=available_skill_files[task_name],
            )
        )
    return skill_tasks


def _load_resume_state(
    config: EvaluationConfig,
    skill_tasks: list[SkillTask],
    human_task_names: list[str],
) -> tuple[dict | None, dict[str, dict[str, dict]], str | None]:
    prev_report = find_previous_report(config)
    cached: dict[str, dict[str, dict]] = {
        "baseline": {},
        "with_generated_skills": {},
        "with_human_skills": {},
    }
    selected_task_names = {task.task_name for task in skill_tasks}
    allowed_names = {
        "baseline": selected_task_names,
        "with_generated_skills": selected_task_names,
        "with_human_skills": set(human_task_names),
    }

    if prev_report:
        for phase_key, phase_allowed_names in allowed_names.items():
            phase_data = prev_report.get(phase_key)
            if not isinstance(phase_data, dict):
                continue
            task_results = phase_data.get("tasks")
            if not isinstance(task_results, dict):
                continue
            for task_name, task_result in task_results.items():
                if task_name in phase_allowed_names:
                    cached[phase_key][task_name] = task_result

    phase_inputs: dict[str, dict[str, tuple[Path, ...]]] = {
        "baseline": {
            task.task_name: (task.source_task_dir,)
            for task in skill_tasks
        },
        "with_generated_skills": {
            task.task_name: (task.source_task_dir, task.generated_skill_md)
            for task in skill_tasks
        },
        "with_human_skills": {
            task_name: (config.spark_root / config.human_skills_dir / task_name,)
            for task_name in human_task_names
        },
    }

    recovered_counts = {
        "baseline": 0,
        "with_generated_skills": 0,
        "with_human_skills": 0,
    }
    for phase_key, task_inputs in phase_inputs.items():
        recovered = _recover_cached_phase_results_from_jobs(
            config=config,
            phase_key=phase_key,
            task_inputs=task_inputs,
        )
        for task_name, task_result in recovered.items():
            if task_name not in cached[phase_key]:
                cached[phase_key][task_name] = task_result
                recovered_counts[phase_key] += 1

    if prev_report:
        log.info(
            "Resume: cached baseline=%d, generated=%d, human=%d tasks",
            len(cached["baseline"]),
            len(cached["with_generated_skills"]),
            len(cached["with_human_skills"]),
        )
    if any(recovered_counts.values()):
        log.info(
            "Resume: recovered partial Harbor results baseline=%d, generated=%d, human=%d",
            recovered_counts["baseline"],
            recovered_counts["with_generated_skills"],
            recovered_counts["with_human_skills"],
        )

    resumed_from: str | None = None
    if prev_report or any(recovered_counts.values()):
        resumed_from = "previous run"
    return prev_report, cached, resumed_from


def _recover_cached_phase_results_from_jobs(
    *,
    config: EvaluationConfig,
    phase_key: str,
    task_inputs: dict[str, tuple[Path, ...]],
) -> dict[str, dict]:
    if not task_inputs:
        return {}

    job_label = _PHASE_JOB_LABELS[phase_key]
    job_dir = config.output_dir / "skill-eval" / config.model / job_label
    if not job_dir.is_dir():
        return {}

    input_mtime_cache: dict[Path, float] = {}
    latest_candidates: dict[str, tuple[float, Path]] = {}

    for trial_dir in job_dir.iterdir():
        if not trial_dir.is_dir():
            continue

        result_file = trial_dir / "result.json"
        if not result_file.is_file():
            continue

        try:
            raw_result = json.loads(result_file.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Skipping unreadable resume candidate %s: %s", result_file, exc)
            continue

        task_name = raw_result.get("task_name")
        if task_name not in task_inputs:
            continue
        if not _trial_matches_resume_config(raw_result, config, phase_key):
            continue

        try:
            result_mtime = result_file.stat().st_mtime
        except OSError as exc:
            log.warning("Skipping unreadable resume candidate %s: %s", result_file, exc)
            continue
        if not _result_is_fresh(task_inputs[task_name], result_mtime, input_mtime_cache):
            continue

        current = latest_candidates.get(task_name)
        if current is None or result_mtime > current[0]:
            latest_candidates[task_name] = (result_mtime, trial_dir)

    recovered: dict[str, dict] = {}
    for task_name, (_, trial_dir) in latest_candidates.items():
        try:
            recovered[task_name] = _task_result_from_trial_dir(trial_dir)
        except Exception as exc:
            log.warning("Failed to recover cached result for %s from %s: %s", task_name, trial_dir, exc)
    return recovered


def _trial_matches_resume_config(raw_result: dict, config: EvaluationConfig, phase_key: str) -> bool:
    raw_config = raw_result.get("config") or {}
    raw_agent = raw_config.get("agent") or {}
    raw_task = raw_config.get("task") or {}

    agent_name = raw_agent.get("name")
    if agent_name and agent_name != config.agent:
        return False

    model_name = raw_agent.get("model_name")
    if model_name and model_name != config.model:
        return False

    task_source = raw_result.get("source") or raw_task.get("source")
    if task_source and task_source != _expected_task_source(config, phase_key):
        return False

    raw_timeout = raw_config.get("agent_timeout_multiplier")
    if raw_timeout is not None:
        try:
            if abs(float(raw_timeout) - config.agent_timeout_multiplier) > 1e-9:
                return False
        except (TypeError, ValueError):
            return False

    return True


def _expected_task_source(config: EvaluationConfig, phase_key: str) -> str:
    if phase_key == "baseline":
        return Path(config.tasks_dir).name
    if phase_key == "with_generated_skills":
        return _GENERATED_TASK_SOURCE
    if phase_key == "with_human_skills":
        return Path(config.human_skills_dir).name
    raise ValueError(f"Unknown phase key: {phase_key}")


def _result_is_fresh(
    input_paths: tuple[Path, ...],
    result_mtime: float,
    input_mtime_cache: dict[Path, float],
) -> bool:
    latest_input_mtime = max(
        (_latest_path_mtime(path, input_mtime_cache) for path in input_paths),
        default=0.0,
    )
    return latest_input_mtime <= result_mtime + 1e-9


def _latest_path_mtime(path: Path, cache: dict[Path, float]) -> float:
    cached = cache.get(path)
    if cached is not None:
        return cached

    try:
        latest_mtime = path.stat().st_mtime
    except OSError:
        cache[path] = 0.0
        return 0.0

    if path.is_dir():
        try:
            for child in path.rglob("*"):
                try:
                    latest_mtime = max(latest_mtime, child.stat().st_mtime)
                except OSError:
                    continue
        except OSError:
            pass

    cache[path] = latest_mtime
    return latest_mtime


def _sanitize_skill_md(src: Path, dst: Path) -> None:
    """Copy a SKILL.md, stripping markdown fences and ensuring valid frontmatter.

    Many LLMs wrap their output in ```markdown ... ``` fences, which breaks
    YAML frontmatter parsing in agents like Codex.  This function strips
    those fences so the file starts with a clean ``---`` delimiter.
    """
    from spark_skills_gen._utils import strip_markdown_fence

    raw = src.read_text()
    cleaned = strip_markdown_fence(raw)
    dst.write_text(cleaned + "\n" if cleaned else raw)


def stage_tasks_with_generated_skills(
    skill_tasks: list[SkillTask],
    config: EvaluationConfig,
) -> Path:
    staged_root = config.staging_root / "current" / _GENERATED_TASK_SOURCE
    if staged_root.exists():
        _force_rmtree(staged_root)
    staged_root.mkdir(parents=True, exist_ok=True)

    for task in tqdm(skill_tasks, desc="  Staging tasks", unit="task"):
        staged_task_dir = staged_root / task.task_name
        shutil.copytree(task.source_task_dir, staged_task_dir)

        environment_dir = staged_task_dir / "environment"
        if not environment_dir.is_dir():
            raise FileNotFoundError(f"Task environment directory does not exist: {environment_dir}")

        dockerfile = environment_dir / "Dockerfile"
        if not dockerfile.is_file():
            raise FileNotFoundError(f"Task Dockerfile does not exist: {dockerfile}")

        generated_skill_name = f"spark-generated-{task.task_name}"
        generated_skill_dir = environment_dir / _GENERATED_SKILL_DIR / generated_skill_name
        generated_skill_dir.mkdir(parents=True, exist_ok=False)

        # Sanitize SKILL.md: strip markdown fences to ensure valid frontmatter
        _sanitize_skill_md(task.generated_skill_md, generated_skill_dir / "SKILL.md")

        _append_generated_skill_copy_block(dockerfile)

        # Inject skill hint into instruction.md so agents that lack built-in
        # skill discovery (e.g. opencode + deepseek-chat) are explicitly told
        # to load the skill before starting the task.
        _inject_skill_hint_into_instruction(staged_task_dir)

    return staged_root


def run_harbor_job(
    tasks_root: Path,
    task_names: list[str],
    label: str,
    config: EvaluationConfig,
) -> HarborJob:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    job_name = f"skill-eval/{config.model}/{label}"
    job_dir = config.output_dir / job_name

    # Clean stale job directory so Harbor treats this as a fresh job.
    # Our resume logic already extracted results from previous trials,
    # so we only need Harbor to run the new tasks.
    # NOTE: Harbor copies agent artifacts (sessions/, skills/, codex.txt, etc.)
    # from Docker containers where they are owned by root, so plain
    # shutil.rmtree will fail with PermissionError.  Fall back to
    # ``chmod -R u+rwX`` + rmtree, or ``rm -rf`` as a last resort.
    if job_dir.is_dir():
        _force_rmtree(job_dir)

    command = [
        "uv",
        "run",
        "harbor",
        "run",
        "-p",
        str(tasks_root),
        "-a",
        config.agent,
        "-m",
        config.model,
        "-o",
        str(config.output_dir),
        "--job-name",
        job_name,
        "-n",
        str(config.parallelism),
        "--agent-timeout-multiplier",
        str(config.agent_timeout_multiplier),
    ]
    for task_name in task_names:
        command.extend(["-t", task_name])

    for key, value in config.agent_kwargs.items():
        command.extend(["--agent-kwarg", f"{key}={value}"])

    log.info("Running Harbor job [%s] on %d tasks …", label, len(task_names))

    proc = subprocess.Popen(
        command,
        cwd=str(config.spark_root),
        env=_build_env(config.spark_root, config.env_overrides),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    captured: list[str] = []
    stop = threading.Event()

    def _poll_progress(pbar: tqdm) -> None:
        seen: set[str] = set()
        while not stop.is_set():
            try:
                if job_dir.is_dir():
                    existing = {d.name for d in job_dir.iterdir() if d.is_dir()}
                    for name in task_names:
                        if name not in seen and any(
                            e.startswith(f"{name}__") for e in existing
                        ):
                            seen.add(name)
                            pbar.update(1)
            except OSError:
                pass
            stop.wait(3)

    pbar = tqdm(total=len(task_names), desc=f"  Harbor [{label}]", unit="task")
    monitor = threading.Thread(target=_poll_progress, args=(pbar,), daemon=True)
    monitor.start()

    for line in proc.stdout:
        captured.append(line)

    proc.wait()
    stop.set()
    monitor.join(timeout=5)
    if pbar.n < pbar.total:
        pbar.update(pbar.total - pbar.n)
    pbar.close()

    if proc.returncode != 0:
        output = "".join(captured)
        raise RuntimeError(
            f"Harbor run for {label} failed with exit code {proc.returncode}.\n"
            f"output:\n{_tail(output)}"
        )
    if not job_dir.is_dir():
        raise FileNotFoundError(f"Harbor reported success but job directory is missing: {job_dir}")

    return HarborJob(label=label, job_name=job_name, job_dir=job_dir)


def _judge_harbor_tasks(job: HarborJob, task_names: list[str]) -> dict[str, dict]:
    """Judge individual tasks from a Harbor job and return per-task result dicts."""
    task_results: dict[str, dict] = {}
    for task_name in tqdm(task_names, desc=f"  Judging [{job.label}]", unit="task"):
        trial_dirs = find_trial_dirs(job.job_dir, task_name)
        if len(trial_dirs) != 1:
            raise RuntimeError(
                f"Expected exactly one trial for task {task_name} in {job.job_dir}, "
                f"found {len(trial_dirs)}"
            )

        task_results[task_name] = _task_result_from_trial_dir(trial_dirs[0])
    return task_results


def _task_result_from_trial_dir(trial_dir: Path) -> dict:
    result_file = trial_dir / "result.json"
    if not result_file.is_file():
        raise FileNotFoundError(f"Trial result is missing: {result_file}")

    raw_result = json.loads(result_file.read_text())
    verifier = raw_result.get("verifier_result") or {}
    rewards = verifier.get("rewards") or {}
    reward = float(rewards.get("reward", 0.0))
    exception_info = raw_result.get("exception_info")
    has_exception = exception_info is not None
    n_tests, n_passed, n_failed = _load_ctrf_counts(trial_dir / "verifier" / "ctrf.json")

    if has_exception:
        status = "ERROR"
    elif reward >= 1.0:
        status = "PASS"
    elif 0 < reward < 1:
        status = "PARTIAL"
    else:
        status = "FAIL"

    return {
        "task": raw_result.get("task_name", trial_dir.name),
        "trial_dir": str(trial_dir),
        "reward": reward,
        "passed": reward >= 1.0,
        "status": status,
        "n_tests": n_tests,
        "n_passed": n_passed,
        "n_failed": n_failed,
        "has_exception": has_exception,
        "exception_type": (exception_info or {}).get("exception_type", ""),
        "exception_message": (exception_info or {}).get("exception_message", ""),
    }


def _load_ctrf_counts(ctrf_path: Path) -> tuple[int, int, int]:
    if not ctrf_path.is_file():
        return 0, 0, 0

    try:
        data = json.loads(ctrf_path.read_text())
    except (json.JSONDecodeError, OSError):
        return 0, 0, 0

    summary = (data.get("results") or {}).get("summary") or {}
    return (
        int(summary.get("tests") or 0),
        int(summary.get("passed") or 0),
        int(summary.get("failed") or 0),
    )


def _build_phase_summary(
    label: str,
    task_results: dict[str, dict],
    job_name: str = "",
    job_dir: str = "",
) -> dict:
    """Compute aggregate statistics from per-task result dicts."""
    total = len(task_results)
    if total == 0:
        return {
            "label": label, "job_name": job_name, "job_dir": job_dir,
            "mean_reward": 0.0, "passed": 0, "failed": 0, "total": 0,
            "pass_rate": 0.0, "tasks": {},
        }
    reward_sum = sum(r["reward"] for r in task_results.values())
    passed = sum(1 for r in task_results.values() if r["passed"])
    return {
        "label": label,
        "job_name": job_name,
        "job_dir": job_dir,
        "mean_reward": reward_sum / total,
        "passed": passed,
        "failed": total - passed,
        "total": total,
        "pass_rate": passed / total,
        "tasks": task_results,
    }


def find_previous_report(config: EvaluationConfig) -> dict | None:
    """Find the previous compatible evaluation report under eval_result_dir."""
    summary_file = config.eval_result_dir / config.model / "summary.json"
    if not summary_file.is_file():
        return None

    try:
        report = json.loads(summary_file.read_text())
    except (json.JSONDecodeError, OSError, KeyError) as exc:
        log.warning("Failed to load previous report %s: %s", summary_file, exc)
        return None

    if _report_matches_resume_config(report, config):
        log.info("Found previous report: %s", summary_file)
        return report

    log.info("Ignoring incompatible previous report: %s", summary_file)
    return None


def _report_matches_resume_config(report: dict, config: EvaluationConfig) -> bool:
    if report.get("model") != config.model:
        return False
    if report.get("agent") != config.agent:
        return False
    if report.get("tasks_dir") != config.tasks_dir:
        return False
    if (report.get("skill_source_model") or report.get("model")) != config.skill_source_model:
        return False
    if report.get("human_skills_dir", "tasks") != config.human_skills_dir:
        return False

    report_timeout = report.get("agent_timeout_multiplier")
    if report_timeout is not None:
        try:
            if abs(float(report_timeout) - config.agent_timeout_multiplier) > 1e-9:
                return False
        except (TypeError, ValueError):
            return False

    return True


def build_evaluation_report(
    config: EvaluationConfig,
    skill_tasks: list[SkillTask],
    staged_tasks_dir: Path | None,
    baseline_summary: dict,
    skilled_summary: dict,
    human_summary: dict | None = None,
    human_task_names: list[str] | None = None,
    resumed_from: str | None = None,
) -> dict:
    human_task_set = set(human_task_names or [])
    is_pdi = config.ref_eval_result_dir is not None

    task_deltas: dict[str, dict] = {}
    for skill_task in skill_tasks:
        task_name = skill_task.task_name
        baseline_task = baseline_summary["tasks"].get(task_name, {})
        skilled_task = skilled_summary["tasks"].get(task_name, {})

        if is_pdi:
            # PDI mode: baseline = without-PDI generated, skilled = with-PDI generated
            delta: dict = {
                "task": task_name,
                "generated_skill_md": str(skill_task.generated_skill_md),
                "generated_reward": baseline_task.get("reward", 0.0),
                "generated_passed": baseline_task.get("passed", False),
                "generated_pdi_reward": skilled_task.get("reward", 0.0),
                "generated_pdi_passed": skilled_task.get("passed", False),
                "generated_pdi_delta": skilled_task.get("reward", 0.0) - baseline_task.get("reward", 0.0),
            }
        else:
            delta = {
                "task": task_name,
                "generated_skill_md": str(skill_task.generated_skill_md),
                "baseline_reward": baseline_task.get("reward", 0.0),
                "generated_reward": skilled_task.get("reward", 0.0),
                "generated_delta": skilled_task.get("reward", 0.0) - baseline_task.get("reward", 0.0),
                "baseline_passed": baseline_task.get("passed", False),
                "generated_passed": skilled_task.get("passed", False),
            }

        if not is_pdi and human_summary and task_name in human_task_set:
            human_task = human_summary["tasks"].get(task_name, {})
            delta["human_reward"] = human_task.get("reward", 0.0)
            delta["human_delta"] = human_task.get("reward", 0.0) - baseline_task.get("reward", 0.0)
            delta["human_passed"] = human_task.get("passed", False)

        task_deltas[task_name] = delta

    if is_pdi:
        report: dict = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "agent": config.agent,
            "model": config.model,
            "tasks_dir": config.tasks_dir,
            "agent_timeout_multiplier": config.agent_timeout_multiplier,
            "skills_result_dir": str(config.skills_result_dir),
            "skill_source_model": config.skill_source_model,
            "ref_eval_result_dir": str(config.ref_eval_result_dir),
            "pdi_mode": True,
            "task_names": [task.task_name for task in skill_tasks],
            "staged_tasks_dir": str(staged_tasks_dir) if staged_tasks_dir else "",
            "with_generated_skills": baseline_summary,       # without PDI (from ref)
            "with_generated_skills_pdi": skilled_summary,    # with PDI (newly run)
            "aggregate_delta": {
                "pdi_pass_rate_delta": skilled_summary["pass_rate"] - baseline_summary["pass_rate"],
                "pdi_mean_reward_delta": skilled_summary["mean_reward"] - baseline_summary["mean_reward"],
                "pdi_passed_delta": skilled_summary["passed"] - baseline_summary["passed"],
            },
            "per_task_delta": task_deltas,
        }
    else:
        report = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "agent": config.agent,
            "model": config.model,
            "tasks_dir": config.tasks_dir,
            "human_skills_dir": config.human_skills_dir,
            "agent_timeout_multiplier": config.agent_timeout_multiplier,
            "skills_result_dir": str(config.skills_result_dir),
            "skill_source_model": config.skill_source_model,
            "task_names": [task.task_name for task in skill_tasks],
            "staged_tasks_dir": str(staged_tasks_dir) if staged_tasks_dir else "",
            "baseline": baseline_summary,
            "with_generated_skills": skilled_summary,
            "aggregate_delta": {
                "pass_rate_delta": skilled_summary["pass_rate"] - baseline_summary["pass_rate"],
                "mean_reward_delta": skilled_summary["mean_reward"] - baseline_summary["mean_reward"],
                "passed_delta": skilled_summary["passed"] - baseline_summary["passed"],
            },
            "per_task_delta": task_deltas,
        }

        if human_summary is not None:
            report["with_human_skills"] = human_summary
            report["human_task_names"] = human_task_names or []
            report["aggregate_delta"]["human_pass_rate_delta"] = (
                human_summary["pass_rate"] - baseline_summary["pass_rate"]
            )
            report["aggregate_delta"]["human_mean_reward_delta"] = (
                human_summary["mean_reward"] - baseline_summary["mean_reward"]
            )
            report["aggregate_delta"]["human_passed_delta"] = (
                human_summary["passed"] - baseline_summary["passed"]
            )

    if resumed_from:
        report["resumed_from"] = resumed_from

    return report


def save_evaluation_report(report: dict, config: EvaluationConfig) -> Path:
    report_dir = config.eval_result_dir / report["model"]
    report_dir.mkdir(parents=True, exist_ok=True)

    (report_dir / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    (report_dir / "summary.md").write_text(_render_markdown_summary(report))
    log.info("Saved evaluation report to %s", report_dir)
    return report_dir


def _inject_skill_hint_into_instruction(task_dir: Path) -> None:
    """Append a skill-usage hint to instruction.md.

    This ensures that agents without built-in skill discovery (e.g. opencode
    with weaker models) are explicitly instructed to load the skill before
    starting the task.  For agents that already auto-discover skills (codex,
    claude-code) the hint is harmless — they will simply ignore it.
    """
    instruction_file = task_dir / "instruction.md"
    if not instruction_file.is_file():
        return
    original = instruction_file.read_text()
    if "Available Skill" in original:
        return  # already injected
    instruction_file.write_text(original.rstrip() + "\n" + _SKILL_INSTRUCTION_HINT)


def _append_generated_skill_copy_block(dockerfile: Path) -> None:
    """Append COPY instructions for generated skills to the Dockerfile.

    In addition to copying skills to agent-specific directories, this also
    copies them to ``/root/spark-skills/`` as a universal fallback.  Agents
    that don't support automatic skill discovery (e.g. opencode) can still
    find the SKILL.md files at this well-known path.
    """
    original = dockerfile.read_text()
    if _GENERATED_SKILL_SENTINEL in original:
        raise RuntimeError(f"Generated skill injection already exists in Dockerfile: {dockerfile}")

    lines = [original.rstrip(), "", _GENERATED_SKILL_SENTINEL]
    for target_path in _GENERATED_SKILL_PATHS:
        lines.append(f"COPY {_GENERATED_SKILL_DIR} {target_path}")

    # Universal fallback: always available regardless of agent type
    lines.append(f"COPY {_GENERATED_SKILL_DIR} /root/spark-skills")

    dockerfile.write_text("\n".join(lines) + "\n")


def _render_markdown_summary(report: dict) -> str:
    is_pdi = report.get("pdi_mode", False)

    if is_pdi:
        gen_no_pdi = report["with_generated_skills"]
        gen_pdi = report["with_generated_skills_pdi"]
        cleanup_status = "yes" if report.get("staging_cleaned_up") else "no"

        lines = [
            "# SPARK PDI Skill Evaluation Summary",
            "",
            f"- Agent: `{report['agent']}`",
            f"- Model: `{report['model']}`",
            f"- Skill source model: `{report['skill_source_model']}`",
            f"- Ref eval result dir: `{report.get('ref_eval_result_dir', '')}`",
            f"- Tasks evaluated: `{len(report['task_names'])}`",
        ]
        if report.get("resumed_from"):
            lines.append(f"- Resumed from: `{report['resumed_from']}`")
        lines.extend([
            f"- Staged tasks dir: `{report.get('staged_tasks_dir', '')}`",
            f"- Temporary staging cleaned up: `{cleanup_status}`",
            "",
            "## Aggregate",
            "",
            "| Setting | Passed | Total | Pass Rate | Mean Reward |",
            "| --- | ---: | ---: | ---: | ---: |",
            (
                f"| Generated (no PDI) | {gen_no_pdi['passed']} | {gen_no_pdi['total']} | "
                f"{gen_no_pdi['pass_rate']:.3f} | {gen_no_pdi['mean_reward']:.3f} |"
            ),
            (
                f"| Generated (PDI) | {gen_pdi['passed']} | {gen_pdi['total']} | "
                f"{gen_pdi['pass_rate']:.3f} | {gen_pdi['mean_reward']:.3f} |"
            ),
            (
                f"| Δ PDI | {report['aggregate_delta']['pdi_passed_delta']:+d} | — | "
                f"{report['aggregate_delta']['pdi_pass_rate_delta']:+.3f} | "
                f"{report['aggregate_delta']['pdi_mean_reward_delta']:+.3f} |"
            ),
            "",
            "## Per Task",
            "",
            "| Task | Generated (no PDI) | Generated (PDI) | Δ PDI |",
            "| --- | --- | --- | ---: |",
        ])
        for task_name in report["task_names"]:
            npt = gen_no_pdi["tasks"].get(task_name, {})
            pt = gen_pdi["tasks"].get(task_name, {})
            np_str = f"{npt.get('status', '—')} ({npt.get('reward', 0):.3f})"
            p_str = f"{pt.get('status', '—')} ({pt.get('reward', 0):.3f})"
            p_delta = pt.get("reward", 0) - npt.get("reward", 0)
            lines.append(f"| {task_name} | {np_str} | {p_str} | {p_delta:+.3f} |")

        lines.append("")
        if report.get("staging_cleanup_error"):
            lines.extend([
                "## Cleanup",
                "",
                f"- Temporary staging cleanup failed: `{report['staging_cleanup_error']}`",
                "",
            ])
        return "\n".join(lines)

    # ── Non-PDI (original) rendering ──
    baseline = report["baseline"]
    skilled = report["with_generated_skills"]
    human = report.get("with_human_skills")
    has_human = human is not None
    cleanup_status = "yes" if report.get("staging_cleaned_up") else "no"

    lines = [
        "# SPARK Skill Evaluation Summary",
        "",
        f"- Agent: `{report['agent']}`",
        f"- Model: `{report['model']}`",
        f"- Skill source model: `{report['skill_source_model']}`",
        f"- Tasks evaluated: `{len(report['task_names'])}`",
    ]
    if has_human:
        lines.append(f"- Human skills dir: `{report.get('human_skills_dir', 'tasks')}`")
        lines.append(f"- Human-skills tasks: `{human['total']}`")
    if report.get("resumed_from"):
        lines.append(f"- Resumed from: `{report['resumed_from']}`")
    lines.extend([
        f"- Staged tasks dir: `{report.get('staged_tasks_dir', '')}`",
        f"- Temporary staging cleaned up: `{cleanup_status}`",
        "",
        "## Aggregate",
        "",
    ])

    # ── Aggregate table ──
    if has_human:
        lines.extend([
            "| Setting | Passed | Total | Pass Rate | Mean Reward |",
            "| --- | ---: | ---: | ---: | ---: |",
            (
                f"| Baseline | {baseline['passed']} | {baseline['total']} | "
                f"{baseline['pass_rate']:.3f} | {baseline['mean_reward']:.3f} |"
            ),
            (
                f"| Generated skills | {skilled['passed']} | {skilled['total']} | "
                f"{skilled['pass_rate']:.3f} | {skilled['mean_reward']:.3f} |"
            ),
            (
                f"| Human skills | {human['passed']} | {human['total']} | "
                f"{human['pass_rate']:.3f} | {human['mean_reward']:.3f} |"
            ),
            (
                f"| Δ generated | {skilled['passed'] - baseline['passed']:+d} | — | "
                f"{report['aggregate_delta']['pass_rate_delta']:+.3f} | "
                f"{report['aggregate_delta']['mean_reward_delta']:+.3f} |"
            ),
            (
                f"| Δ human | {report['aggregate_delta'].get('human_passed_delta', 0):+d} | — | "
                f"{report['aggregate_delta'].get('human_pass_rate_delta', 0.0):+.3f} | "
                f"{report['aggregate_delta'].get('human_mean_reward_delta', 0.0):+.3f} |"
            ),
        ])
    else:
        lines.extend([
            "| Setting | Passed | Total | Pass Rate | Mean Reward |",
            "| --- | ---: | ---: | ---: | ---: |",
            (
                f"| Baseline | {baseline['passed']} | {baseline['total']} | "
                f"{baseline['pass_rate']:.3f} | {baseline['mean_reward']:.3f} |"
            ),
            (
                f"| Generated skills | {skilled['passed']} | {skilled['total']} | "
                f"{skilled['pass_rate']:.3f} | {skilled['mean_reward']:.3f} |"
            ),
            (
                f"| Δ generated | {skilled['passed'] - baseline['passed']:+d} | — | "
                f"{report['aggregate_delta']['pass_rate_delta']:+.3f} | "
                f"{report['aggregate_delta']['mean_reward_delta']:+.3f} |"
            ),
        ])

    lines.extend(["", "## Per Task", ""])

    # ── Per-task table ──
    human_task_set = set(report.get("human_task_names", []))
    if has_human:
        lines.extend([
            "| Task | Baseline | Generated | Human | Δ gen | Δ human |",
            "| --- | --- | --- | --- | ---: | ---: |",
        ])
        for task_name in report["task_names"]:
            bt = baseline["tasks"].get(task_name, {})
            gt = skilled["tasks"].get(task_name, {})
            b_str = f"{bt.get('status', '—')} ({bt.get('reward', 0):.3f})"
            g_str = f"{gt.get('status', '—')} ({gt.get('reward', 0):.3f})"
            g_delta = gt.get("reward", 0) - bt.get("reward", 0)
            if task_name in human_task_set:
                ht = human["tasks"].get(task_name, {})
                h_str = f"{ht.get('status', '—')} ({ht.get('reward', 0):.3f})"
                h_delta = ht.get("reward", 0) - bt.get("reward", 0)
            else:
                h_str = "—"
                h_delta = 0.0
            lines.append(
                f"| {task_name} | {b_str} | {g_str} | {h_str} | "
                f"{g_delta:+.3f} | {h_delta:+.3f} |"
            )
    else:
        lines.extend([
            "| Task | Baseline | Generated | Δ gen |",
            "| --- | --- | --- | ---: |",
        ])
        for task_name in report["task_names"]:
            bt = baseline["tasks"].get(task_name, {})
            gt = skilled["tasks"].get(task_name, {})
            b_str = f"{bt.get('status', '—')} ({bt.get('reward', 0):.3f})"
            g_str = f"{gt.get('status', '—')} ({gt.get('reward', 0):.3f})"
            g_delta = gt.get("reward", 0) - bt.get("reward", 0)
            lines.append(f"| {task_name} | {b_str} | {g_str} | {g_delta:+.3f} |")

    lines.append("")
    if report.get("staging_cleanup_error"):
        lines.extend([
            "## Cleanup",
            "",
            f"- Temporary staging cleanup failed: `{report['staging_cleanup_error']}`",
            "",
        ])
    return "\n".join(lines)


def _build_env(spark_root: Path, overrides: dict[str, str]) -> dict[str, str]:
    from spark_skills_gen._utils import build_env
    return build_env(spark_root, overrides)


def _resolve_path(spark_root: Path, raw_path: Path) -> Path:
    if raw_path.is_absolute():
        return raw_path
    return spark_root / raw_path


def _tail(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _force_rmtree(path: Path) -> None:
    """Remove a directory tree that may contain root-owned files.

    Harbor copies agent artifacts from Docker containers where they are
    owned by root.  A plain ``shutil.rmtree`` will fail with
    ``PermissionError`` on those files.  We first try ``chmod -R u+rwX``
    to reclaim ownership-agnostic write permission (works when the parent
    dir is ours), then fall back to ``rm -rf`` which may use sudo-less
    deletion on systems where the user owns the parent directory.
    """
    try:
        shutil.rmtree(path)
    except PermissionError:
        log.warning("shutil.rmtree failed (root-owned files?), retrying with chmod -R u+rwX …")
        try:
            subprocess.run(
                ["chmod", "-R", "u+rwX", str(path)],
                check=True,
                capture_output=True,
            )
            shutil.rmtree(path)
        except (subprocess.CalledProcessError, PermissionError, OSError):
            log.warning("chmod fallback failed, trying rm -rf …")
            subprocess.run(
                ["rm", "-rf", str(path)],
                check=True,
                capture_output=True,
            )


def _cleanup_directory_tree(path: Path) -> str | None:
    try:
        if path.exists():
            _force_rmtree(path)
        parent = path.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
        return None
    except OSError as exc:
        return str(exc)
