"""Stage 1 — Execution: run a single task via Harbor in a Docker container."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_DOCKER_NAME_MAX_LEN = 32
_COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
_COMPOSE_WORKDIR_LABEL = "com.docker.compose.project.working_dir"
_SYSTEM_NETWORKS = {"bridge", "host", "none"}


@dataclass
class ExecutionConfig:
    agent: str = "qwen-coder"
    model: str = "qwen3-coder-next"
    tasks_dir: str = "tasks_no_skills_generate"
    output_dir: str = "./spark-jobs"
    staging_root: str = "./save/.spark-staging"
    parallelism: int = 1
    agent_timeout_multiplier: float = 1.0
    env_overrides: dict[str, str] = field(default_factory=dict)
    agent_kwargs: dict[str, str] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    task_name: str
    attempt: int
    trial_dir: Path | None = None
    job_dir: Path | None = None
    success: bool = False
    error: str = ""


def execute_task(
    task_name: str,
    attempt: int,
    config: ExecutionConfig,
    spark_root: Path,
    retry_context: str = "",
    cancel_event: threading.Event | None = None,
    keep_images: bool = False,
) -> ExecutionResult:
    """Run a single task through Harbor with optional retry context injected.

    When *keep_images* is True, Docker images are preserved after execution
    so that subsequent retry attempts can reuse the build cache.
    """
    staging_dir, staging_task_dir = _prepare_staging(
        task_name=task_name,
        attempt=attempt,
        spark_root=spark_root,
        tasks_dir=config.tasks_dir,
        staging_root=config.staging_root,
        retry_context=retry_context,
    )

    job_name = f"spark-iter/{config.model}/{task_name}/attempt-{attempt}"
    job_output = spark_root / config.output_dir

    env = _build_env(config.env_overrides, spark_root)

    cmd = [
        "uv", "run", "harbor", "run",
        "-p", str(staging_dir),
        "-a", config.agent,
        "-m", config.model,
        "-o", str(job_output),
        "--job-name", job_name,
        "-n", "1",
        "--agent-timeout-multiplier", str(config.agent_timeout_multiplier),
        "-t", task_name,
    ]

    for flag in _agent_env_flags(env):
        cmd.extend(flag)

    for key, val in config.agent_kwargs.items():
        cmd.extend(["--agent-kwarg", f"{key}={val}"])

    log.info("Executing task %s (attempt %d): %s", task_name, attempt, _redact_cmd(cmd))

    try:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(spark_root),
                env=env,
            )
            # Poll with cancel check — check every 2 seconds
            _POLL_INTERVAL = 2
            _TIMEOUT = 1800
            elapsed = 0.0
            while proc.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    log.info("Cancel requested for task %s — killing subprocess", task_name)
                    proc.kill()
                    proc.wait(timeout=30)
                    return ExecutionResult(
                        task_name=task_name, attempt=attempt,
                        error="Cancelled by user",
                    )
                try:
                    proc.wait(timeout=_POLL_INTERVAL)
                except subprocess.TimeoutExpired:
                    pass
                elapsed += _POLL_INTERVAL
                if elapsed >= _TIMEOUT:
                    proc.kill()
                    proc.wait(timeout=30)
                    return ExecutionResult(
                        task_name=task_name, attempt=attempt,
                        error="Harbor process timed out after 1800s",
                    )

            stdout = proc.stdout.read() if proc.stdout else ""
            stderr = proc.stderr.read() if proc.stderr else ""
            log.info("Harbor exit code: %d", proc.returncode)
            if stdout:
                log.debug("Harbor stdout (tail):\n%s", stdout[-3000:])
            if stderr:
                stderr_level = logging.WARNING if proc.returncode != 0 else logging.DEBUG
                log.log(stderr_level, "Harbor stderr (tail):\n%s", stderr[-3000:])
        except Exception as e:
            return ExecutionResult(task_name=task_name, attempt=attempt, error=str(e))

        trial_dir = _find_latest_trial(job_output / job_name, task_name)

        return ExecutionResult(
            task_name=task_name,
            attempt=attempt,
            trial_dir=trial_dir,
            job_dir=job_output / job_name,
            success=trial_dir is not None,
            error="" if trial_dir else "Could not find trial output directory",
        )
    finally:
        _cleanup_docker_for_task(task_name, staging_task_dir, keep_images=keep_images)
        _cleanup_staging_task(staging_task_dir, staging_dir)


def _prepare_staging(
    task_name: str,
    attempt: int,
    spark_root: Path,
    tasks_dir: str,
    staging_root: str,
    retry_context: str,
) -> tuple[Path, Path]:
    """Copy task to a staging directory and inject retry context into instruction.md."""
    source = spark_root / tasks_dir / task_name
    staging_base = _resolve_relative_path(spark_root, staging_root)
    staging_task_dir = staging_base / task_name
    staging_base.mkdir(parents=True, exist_ok=True)

    if staging_task_dir.exists():
        _force_rmtree(staging_task_dir)
    shutil.copytree(source, staging_task_dir)

    if retry_context:
        instruction_file = staging_task_dir / "instruction.md"
        original = instruction_file.read_text() if instruction_file.exists() else ""
        instruction_file.write_text(original + "\n" + retry_context)

    return staging_base, staging_task_dir


def _cleanup_staging_task(staging_task_dir: Path, staging_base: Path) -> None:
    """Remove per-task staging data after Harbor finishes using it."""
    try:
        if staging_task_dir.exists():
            _force_rmtree(staging_task_dir)
        if staging_base.exists() and not any(staging_base.iterdir()):
            staging_base.rmdir()
    except OSError:
        log.exception("Failed to clean staging directory %s", staging_task_dir)


def _find_latest_trial(job_dir: Path, task_name: str) -> Path | None:
    """Find the most recent trial directory for a given task.

    Harbor truncates the task name to ``_DOCKER_NAME_MAX_LEN`` characters in
    the trial directory name, so we match using the truncated prefix as well.
    """
    if not job_dir.exists():
        return None
    prefix = f"{task_name}__"
    truncated_prefix = f"{task_name[:_DOCKER_NAME_MAX_LEN]}__"
    candidates = [
        d for d in job_dir.iterdir()
        if d.is_dir() and (d.name.startswith(prefix) or d.name.startswith(truncated_prefix))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime)


_SECRET_RE = re.compile(r"((?:KEY|TOKEN|SECRET)=)\S+", re.IGNORECASE)


def _redact_cmd(cmd: list[str]) -> str:
    """Join command parts, masking secret values for safe logging."""
    return _SECRET_RE.sub(r"\1***", " ".join(cmd))


_AGENT_ENV_KEYS = [
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "DASHSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "GOOGLE_API_KEY",
    "XAI_API_KEY",
    "TOGETHER_API_KEY",
]


def _agent_env_flags(env: dict[str, str]) -> list[list[str]]:
    """Build --ae flags to forward API credentials into the Harbor sandbox."""
    flags: list[list[str]] = []
    for key in _AGENT_ENV_KEYS:
        val = env.get(key)
        if val:
            flags.append(["--ae", f"{key}={val}"])
    return flags


def _build_env(overrides: dict[str, str], spark_root: Path) -> dict[str, str]:
    """Build the subprocess environment, loading .env if available."""
    from spark_skills_gen._utils import build_env
    return build_env(spark_root, overrides)


def _resolve_relative_path(spark_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return spark_root / path


def _force_rmtree(path: Path) -> None:
    """Remove a directory tree that may contain root-owned files.

    Harbor runs agents inside Docker containers as root, so output files
    (sessions/, skills/, codex.txt, etc.) are owned by root.  A plain
    ``shutil.rmtree`` fails with ``PermissionError`` on those files.
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


def list_available_tasks(spark_root: Path, tasks_dir: str = "tasks_no_skills_generate") -> list[str]:
    """Return sorted list of task names from the tasks directory."""
    td = spark_root / tasks_dir
    if not td.exists():
        return []
    return sorted(
        d.name for d in td.iterdir()
        if d.is_dir() and (d / "instruction.md").exists()
    )


# ---------------------------------------------------------------------------
# Docker cleanup — prevent container/image accumulation across retries
# ---------------------------------------------------------------------------

def _path_is_within(candidate: str, root: Path) -> bool:
    """Return True if *candidate* is within *root*, even if the path no longer exists."""
    if not candidate:
        return False
    try:
        return os.path.commonpath([os.path.abspath(candidate), str(root)]) == str(root)
    except ValueError:
        return False


def _inspect_compose_containers() -> list[dict]:
    """Return docker inspect payloads for all compose-managed containers."""
    try:
        ids = subprocess.run(
            ["docker", "ps", "-aq"],
            capture_output=True, text=True, timeout=60,
        )
        container_ids = ids.stdout.split()
        if ids.returncode != 0 or not container_ids:
            return []

        inspect = subprocess.run(
            ["docker", "inspect", *container_ids],
            capture_output=True, text=True, timeout=120,
        )
        if inspect.returncode != 0 or not inspect.stdout.strip():
            return []
        return json.loads(inspect.stdout)
    except Exception:
        log.debug("Container inspect failed", exc_info=True)
        return []


def _collect_compose_artifacts(
    *,
    staging_root: Path | None = None,
    environment_dir: Path | None = None,
) -> list[dict]:
    """Collect compose artifacts scoped to the Harbor staging area."""
    artifacts: list[dict] = []
    environment_dir_str = str(environment_dir) if environment_dir else None

    for item in _inspect_compose_containers():
        labels = item.get("Config", {}).get("Labels") or {}
        working_dir = labels.get(_COMPOSE_WORKDIR_LABEL, "")
        if not working_dir:
            continue
        if environment_dir_str is not None and working_dir != environment_dir_str:
            continue
        if staging_root is not None and not _path_is_within(working_dir, staging_root):
            continue

        networks = sorted(
            name
            for name in (item.get("NetworkSettings", {}).get("Networks") or {}).keys()
            if name not in _SYSTEM_NETWORKS
        )
        artifacts.append({
            "id": item["Id"],
            "name": item["Name"].lstrip("/"),
            "image": item.get("Config", {}).get("Image", ""),
            "project": labels.get(_COMPOSE_PROJECT_LABEL, ""),
            "working_dir": working_dir,
            "status": item.get("State", {}).get("Status", ""),
            "networks": networks,
        })
    return artifacts


def _remove_containers_and_networks(artifacts: list[dict], *, scope: str) -> None:
    """Remove containers and networks only, keeping images for build cache reuse."""
    _remove_compose_artifacts(artifacts, scope=scope, remove_images=False)


def _remove_compose_artifacts(
    artifacts: list[dict],
    *,
    scope: str,
    remove_images: bool = True,
) -> None:
    """Force-remove compose containers, networks, and optionally task images."""
    if not artifacts:
        return

    removed_containers = 0
    removed_networks = 0
    removed_images = 0

    for artifact in artifacts:
        try:
            rm = subprocess.run(
                ["docker", "rm", "-f", artifact["id"]],
                capture_output=True, text=True, timeout=60,
            )
            if rm.returncode == 0:
                removed_containers += 1
        except Exception:
            log.debug("Container cleanup failed for %s", artifact["name"], exc_info=True)

    networks = sorted({network for artifact in artifacts for network in artifact["networks"]})
    for network in networks:
        try:
            rm = subprocess.run(
                ["docker", "network", "rm", network],
                capture_output=True, text=True, timeout=60,
            )
            if rm.returncode == 0:
                removed_networks += 1
        except Exception:
            log.debug("Network cleanup failed for %s", network, exc_info=True)

    if remove_images:
        images = sorted({artifact["image"] for artifact in artifacts if artifact["image"]})
        for image in images:
            try:
                rm = subprocess.run(
                    ["docker", "rmi", "-f", image],
                    capture_output=True, text=True, timeout=120,
                )
                if rm.returncode == 0:
                    removed_images += 1
            except Exception:
                log.debug("Image cleanup failed for %s", image, exc_info=True)

    log.info(
        "Docker cleanup [%s]: removed %d container(s), %d network(s), %d image(s)",
        scope, removed_containers, removed_networks, removed_images,
    )


def _cleanup_docker_for_task(
    task_name: str,
    staging_task_dir: Path,
    *,
    keep_images: bool = False,
) -> None:
    """Remove Harbor compose artifacts created by one task attempt.

    Harbor creates a uniquely-tagged image ``{task_name}__{rand}-main`` per run.
    Force-removing the compose project here avoids stranded running containers
    when Harbor is interrupted mid-run.

    When *keep_images* is True, only containers and networks are removed;
    images are kept so that subsequent retry attempts can reuse the build
    cache instead of rebuilding from scratch.
    """
    artifacts = _collect_compose_artifacts(environment_dir=staging_task_dir / "environment")
    if keep_images:
        _remove_containers_and_networks(artifacts, scope=task_name)
    else:
        _remove_compose_artifacts(artifacts, scope=task_name)


def cleanup_task_images(task_name: str) -> None:
    """Remove Docker images associated with a task after all retries are done.

    Called once per task (not per attempt) to reclaim disk space while still
    allowing retry attempts to benefit from cached image layers.
    """
    try:
        # Harbor names images as {task_name}__{hash}-main
        prefix = task_name[:_DOCKER_NAME_MAX_LEN]
        result = subprocess.run(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}", "--filter",
             f"reference=*{prefix}*"],
            capture_output=True, text=True, timeout=60,
        )
        images = [img.strip() for img in result.stdout.splitlines() if img.strip()]
        if not images:
            return
        removed = 0
        for image in images:
            try:
                rm = subprocess.run(
                    ["docker", "rmi", "-f", image],
                    capture_output=True, text=True, timeout=120,
                )
                if rm.returncode == 0:
                    removed += 1
            except Exception:
                pass
        if removed:
            log.info("Post-retry image cleanup [%s]: removed %d image(s)", task_name, removed)
    except Exception:
        log.debug("Post-retry image cleanup failed for %s", task_name, exc_info=True)


def cleanup_stale_docker_artifacts(spark_root: Path, staging_root: str) -> None:
    """Remove Harbor leftovers under the pipeline staging root.

    Harbor task containers are expected to be short-lived. If any compose
    projects are still attached to the current staging root when a new pipeline
    starts, they are leftovers from an interrupted run and can exhaust Docker
    storage for everyone on the shared host.
    """
    abs_staging_root = _resolve_relative_path(spark_root, staging_root)
    artifacts = _collect_compose_artifacts(staging_root=abs_staging_root)
    _remove_compose_artifacts(artifacts, scope=f"staging-root={abs_staging_root}")

    freed = ""
    try:
        r1 = subprocess.run(
            ["docker", "container", "prune", "-f"],
            capture_output=True, text=True, timeout=120,
        )
        r2 = subprocess.run(
            ["docker", "image", "prune", "-f"],
            capture_output=True, text=True, timeout=120,
        )
        parts = []
        for r in (r1, r2):
            if r.returncode == 0 and r.stdout:
                for ln in r.stdout.strip().split("\n"):
                    if "reclaimed" in ln.lower():
                        parts.append(ln.strip())
        freed = " | ".join(parts) if parts else "nothing to reclaim"
    except Exception:
        log.debug("Global Docker cleanup failed", exc_info=True)
        return
    log.info("Stale Docker cleanup done — %s", freed)


def prefetch_base_images(spark_root: Path, tasks_dir: str = "tasks_no_skills_generate") -> None:
    """Pull frequently-used base images in parallel before task execution.

    Only images referenced by ≥2 tasks are pre-pulled (rare / third-party
    images are left for on-demand pull during build).  Pulls run concurrently
    so the wall-clock cost is roughly one pull, not N.
    """
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor

    td = spark_root / tasks_dir
    if not td.exists():
        return

    counter: Counter[str] = Counter()
    for dockerfile in td.glob("*/environment/Dockerfile"):
        try:
            for line in dockerfile.read_text().splitlines():
                stripped = line.strip()
                # Handle "FROM --platform=... image" syntax
                if stripped.upper().startswith("FROM "):
                    parts = stripped.split()
                    image = parts[1] if not parts[1].startswith("--") else parts[2]
                    if image.lower() != "scratch":
                        counter[image] += 1
                    break
        except OSError:
            continue

    # Only pre-pull images used by ≥2 tasks
    frequent = sorted(img for img, cnt in counter.items() if cnt >= 2)
    if not frequent:
        return

    log.info("Pre-pulling %d frequent base image(s): %s", len(frequent), ", ".join(frequent))

    def _pull(image: str) -> str:
        try:
            subprocess.run(
                ["docker", "pull", "-q", image],
                capture_output=True, text=True, timeout=300,
            )
            return f"ok: {image}"
        except Exception:
            return f"skip: {image}"

    with ThreadPoolExecutor(max_workers=min(len(frequent), 4)) as pool:
        results = list(pool.map(_pull, frequent))
    for r in results:
        log.info("Pre-pull %s", r)
