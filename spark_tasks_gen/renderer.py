"""Render a task blueprint into a runnable Harbor task directory."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil

from spark_tasks_gen.models import PromptSpec, SupportFile, TaskBlueprint


@dataclass(slots=True)
class RenderResult:
    """Details about the rendered Harbor task."""

    task_dir: Path
    written_files: list[str]
    artifact_dir: Path
    artifact_files: list[str]


def render_task(
    blueprint: TaskBlueprint,
    prompt_spec: PromptSpec,
    output_root: Path,
    provenance: dict[str, object] | None = None,
    overwrite: bool = True,
) -> RenderResult:
    """Materialize a blueprint as a self-contained Harbor task."""
    task_dir = output_root / blueprint.task_id
    artifact_dir = output_root / "_artifacts" / blueprint.task_id
    if task_dir.exists():
        if not overwrite:
            raise FileExistsError(f"Task directory already exists: {task_dir}")
        shutil.rmtree(task_dir)
    if artifact_dir.exists():
        if not overwrite:
            raise FileExistsError(f"Artifact directory already exists: {artifact_dir}")
        shutil.rmtree(artifact_dir)

    written_files: list[str] = []
    artifact_files: list[str] = []
    _ensure_dir(task_dir / "environment" / "files")
    _ensure_dir(task_dir / "environment" / "scripts")
    _ensure_dir(task_dir / "solution")
    _ensure_dir(task_dir / "tests")
    _ensure_dir(artifact_dir)

    written_files.append(_write_text(task_dir / "instruction.md", blueprint.instruction_md))
    written_files.append(_write_text(task_dir / "task.toml", _render_task_toml(blueprint)))
    written_files.append(_write_text(task_dir / "environment" / "Dockerfile", _render_dockerfile(blueprint)))
    written_files.append(
        _write_text(
            task_dir / "environment" / "scripts" / "build_data.py",
            blueprint.data_builder_python.rstrip() + "\n",
        )
    )
    written_files.append(_write_shell(task_dir / "solution" / "solve.sh", _render_solution_sh(blueprint)))
    written_files.append(_write_text(task_dir / "tests" / "test_outputs.py", _ensure_pytest_functions(blueprint.verifier.test_code).rstrip() + "\n"))
    written_files.append(_write_shell(task_dir / "tests" / "test.sh", _render_test_sh(blueprint)))

    for support_file in blueprint.support_files:
        support_path = task_dir / "environment" / "files" / _sanitize_relative_path(support_file.relative_path)
        _ensure_dir(support_path.parent)
        written_files.append(_write_text(support_path, support_file.content.rstrip() + "\n"))

    report = {
        "task_id": blueprint.task_id,
        "task_dir": str(task_dir),
        "reference_tasks_dir": prompt_spec.reference_tasks_dir,
        "reference_tasks": blueprint.reference_tasks,
        "family_hypotheses": blueprint.family_hypotheses,
        "assumptions": blueprint.assumptions,
        "written_files": written_files,
        "output_path": blueprint.output_path,
    }
    if provenance:
        report["provenance"] = provenance
    artifact_files.append(
        _write_text(artifact_dir / "generation_report.json", json.dumps(report, indent=2, ensure_ascii=False))
    )
    artifact_files.append(
        _write_text(artifact_dir / "task_blueprint.json", json.dumps(blueprint.to_dict(), indent=2, ensure_ascii=False))
    )

    return RenderResult(
        task_dir=task_dir,
        written_files=written_files,
        artifact_dir=artifact_dir,
        artifact_files=artifact_files,
    )


def _render_task_toml(blueprint: TaskBlueprint) -> str:
    env = blueprint.environment
    agent_timeout = blueprint.agent_timeout_sec if blueprint.agent_timeout_sec is not None else blueprint.verifier.timeout_sec
    lines = [
        'version = "1.0"',
        "",
        "[metadata]",
        'author_name = "SPARK Task Generator"',
        'author_email = "spark@example.com"',
        f'difficulty = "{_escape_toml(blueprint.difficulty)}"',
        f'category = "{_escape_toml(blueprint.category)}"',
        f"tags = [{', '.join(_quote_toml(item) for item in blueprint.tags)}]",
        "",
        "[verifier]",
        f"timeout_sec = {blueprint.verifier.timeout_sec:.1f}",
        "",
        "[agent]",
        f"timeout_sec = {agent_timeout:.1f}",
        "",
        "[environment]",
        f"build_timeout_sec = {env.build_timeout_sec:.1f}",
        f"cpus = {env.cpus}",
        f"memory_mb = {env.memory_mb}",
        f"storage_mb = {env.storage_mb}",
    ]
    if env.allow_internet:
        lines.append("allow_internet = true")
    return "\n".join(lines) + "\n"


def _render_dockerfile(blueprint: TaskBlueprint) -> str:
    env = blueprint.environment
    lines = [
        "# syntax=docker/dockerfile:1",
        f"FROM {env.base_image}",
        "ENV DEBIAN_FRONTEND=noninteractive",
        "",
    ]
    if env.apt_packages:
        apt_packages = " ".join(env.apt_packages)
        lines.append(
            "RUN --mount=type=cache,target=/var/cache/apt,sharing=locked "
            "--mount=type=cache,target=/var/lib/apt/lists,sharing=locked "
            f"apt-get update && apt-get install -y {apt_packages}"
        )
        lines.append("")
    if env.pip_packages:
        pip_packages = " ".join(env.pip_packages)
        lines.append(
            "RUN --mount=type=cache,target=/root/.cache/pip "
            f"python3 -m pip install --break-system-packages {pip_packages}"
        )
        lines.append("")
    lines.extend(
        [
            f"WORKDIR {env.workdir}",
            "COPY files/ ./",
            "COPY scripts/build_data.py /tmp/spark_task_build_data.py",
            "RUN python3 /tmp/spark_task_build_data.py && rm /tmp/spark_task_build_data.py",
            "",
        ]
    )
    return "\n".join(lines)


def _render_solution_sh(blueprint: TaskBlueprint) -> str:
    return (
        "#!/bin/bash\n"
        "set -euo pipefail\n\n"
        "python3 <<'SPARK_ORACLE_EOF'\n"
        f"{blueprint.oracle_python.rstrip()}\n"
        "SPARK_ORACLE_EOF\n"
    )


def _render_test_sh(blueprint: TaskBlueprint) -> str:
    packages = _merge_packages(
        ["pytest==8.4.1", "pytest-json-ctrf==0.3.5"],
        blueprint.verifier.pip_packages,
    )
    package_blob = " ".join(packages)
    return (
        "#!/bin/bash\n"
        "set -euo pipefail\n\n"
        f"python3 -m pip install --break-system-packages {package_blob}\n"
        "mkdir -p /logs/verifier\n"
        "if pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA -v; then\n"
        "  echo 1 > /logs/verifier/reward.txt\n"
        "else\n"
        "  echo 0 > /logs/verifier/reward.txt\n"
        "fi\n"
        "exit 0\n"
    )


def _merge_packages(defaults: list[str], extras: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for package in [*defaults, *extras]:
        if package not in seen:
            seen.add(package)
            ordered.append(package)
    return ordered


def _ensure_pytest_functions(test_code: str) -> str:
    """Wrap module-level asserts into a test function if no def test_* exists."""
    if re.search(r"^def test_", test_code, re.MULTILINE):
        return test_code

    lines = test_code.splitlines()
    import_lines: list[str] = []
    body_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not body_lines and (
            stripped.startswith("import ")
            or stripped.startswith("from ")
            or stripped == ""
            or stripped.startswith("#")
        ):
            import_lines.append(line)
        else:
            body_lines.append(line)

    indented_body = "\n".join(f"    {line}" if line.strip() else "" for line in body_lines)
    return "\n".join(import_lines) + "\n\n\ndef test_all():\n" + indented_body + "\n"


def _sanitize_relative_path(relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe support file path: {relative_path}")
    return path


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, content: str) -> str:
    path.write_text(content)
    return str(path)


def _write_shell(path: Path, content: str) -> str:
    path.write_text(content)
    path.chmod(0o755)
    return str(path)


def _escape_toml(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _quote_toml(value: str) -> str:
    return f'"{_escape_toml(value)}"'
