"""Structured data models for SPARK task generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any


class SchemaError(ValueError):
    """Raised when an LLM payload does not satisfy the blueprint schema."""


def slugify(value: str) -> str:
    """Convert text into a Harbor-friendly task id."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    slug = cleaned.strip("-")
    if not slug:
        raise SchemaError("Could not derive a valid slug from empty text")
    return slug


def _require_dict(data: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SchemaError(f"Expected '{field_name}' to be an object")
    return data


def _require_string(data: Any, field_name: str) -> str:
    if not isinstance(data, str) or not data.strip():
        raise SchemaError(f"Expected '{field_name}' to be a non-empty string")
    return data


def _require_list_of_strings(data: Any, field_name: str) -> list[str]:
    if data is None:
        return []
    if not isinstance(data, list) or any(not isinstance(item, str) for item in data):
        raise SchemaError(f"Expected '{field_name}' to be a list of strings")
    return data


def _require_float(data: Any, field_name: str) -> float:
    if not isinstance(data, int | float):
        raise SchemaError(f"Expected '{field_name}' to be numeric")
    return float(data)


def _require_int(data: Any, field_name: str) -> int:
    if not isinstance(data, int):
        raise SchemaError(f"Expected '{field_name}' to be an integer")
    return data


_VALID_DIFFICULTIES = frozenset({"easy", "medium", "hard"})


def _validate_python_syntax(code: str, field_name: str) -> None:
    try:
        compile(code, f"<{field_name}>", "exec")
    except SyntaxError as exc:
        raise SchemaError(
            f"'{field_name}' contains invalid Python syntax at line {exc.lineno}: {exc.msg}"
        ) from exc


def _validate_relative_path(path_str: str) -> None:
    from pathlib import PurePosixPath

    path = PurePosixPath(path_str)
    if path.is_absolute():
        raise SchemaError(f"Support file path must be relative: {path_str}")
    if ".." in path.parts:
        raise SchemaError(f"Support file path must not traverse upward: {path_str}")


@dataclass(slots=True)
class PromptSpec:
    """Normalized user request for task generation."""

    prompt: str
    task_name_hint: str | None = None
    available_tools: list[str] = field(default_factory=list)
    environment_hints: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    output_dir: str = "spark_tasks_gen/generated_tasks"

    @property
    def task_slug(self) -> str:
        if self.task_name_hint:
            return slugify(self.task_name_hint)
        first_line = self.prompt.strip().splitlines()[0]
        return slugify(first_line[:80])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PromptSpec":
        raw = _require_dict(data, "prompt_spec")
        return cls(
            prompt=_require_string(raw.get("prompt"), "prompt"),
            task_name_hint=raw.get("task_name_hint"),
            available_tools=_require_list_of_strings(raw.get("available_tools"), "available_tools"),
            environment_hints=_require_list_of_strings(raw.get("environment_hints"), "environment_hints"),
            constraints=_require_list_of_strings(raw.get("constraints"), "constraints"),
            output_dir=raw.get("output_dir", "spark_tasks_gen/generated_tasks"),
        )

    @classmethod
    def from_json_text(cls, text: str) -> "PromptSpec":
        return cls.from_dict(json.loads(text))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidenceItem:
    """A concrete piece of evidence supporting blueprint decisions."""

    evidence_id: str
    source_type: str
    source_name: str
    quote: str
    rationale: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceItem":
        raw = _require_dict(data, "evidence_item")
        return cls(
            evidence_id=_require_string(raw.get("evidence_id"), "evidence_id"),
            source_type=_require_string(raw.get("source_type"), "source_type"),
            source_name=_require_string(raw.get("source_name"), "source_name"),
            quote=_require_string(raw.get("quote"), "quote"),
            rationale=_require_string(raw.get("rationale"), "rationale"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EnvironmentSpec:
    """Container and runtime settings for the generated task."""

    base_image: str
    workdir: str
    apt_packages: list[str]
    pip_packages: list[str]
    build_timeout_sec: float
    cpus: int
    memory_mb: int
    storage_mb: int
    allow_internet: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EnvironmentSpec":
        raw = _require_dict(data, "environment")
        allow_internet = raw.get("allow_internet", False)
        if not isinstance(allow_internet, bool):
            raise SchemaError("Expected 'allow_internet' to be a boolean")
        return cls(
            base_image=_require_string(raw.get("base_image"), "base_image"),
            workdir=_require_string(raw.get("workdir"), "workdir"),
            apt_packages=_require_list_of_strings(raw.get("apt_packages"), "apt_packages"),
            pip_packages=_require_list_of_strings(raw.get("pip_packages"), "pip_packages"),
            build_timeout_sec=_require_float(raw.get("build_timeout_sec"), "build_timeout_sec"),
            cpus=_require_int(raw.get("cpus"), "cpus"),
            memory_mb=_require_int(raw.get("memory_mb"), "memory_mb"),
            storage_mb=_require_int(raw.get("storage_mb"), "storage_mb"),
            allow_internet=allow_internet,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SupportFile:
    """A deterministic file copied into the task environment."""

    relative_path: str
    content: str
    purpose: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupportFile":
        raw = _require_dict(data, "support_file")
        relative_path = _require_string(raw.get("relative_path"), "relative_path")
        _validate_relative_path(relative_path)
        return cls(
            relative_path=relative_path,
            content=_require_string(raw.get("content"), "content"),
            purpose=str(raw.get("purpose", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class VerifierSpec:
    """Verifier settings and pytest source code."""

    timeout_sec: float
    pip_packages: list[str]
    test_code: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VerifierSpec":
        raw = _require_dict(data, "verifier")
        return cls(
            timeout_sec=_require_float(raw.get("timeout_sec"), "timeout_sec"),
            pip_packages=_require_list_of_strings(raw.get("pip_packages"), "pip_packages"),
            test_code=_require_string(raw.get("test_code"), "test_code"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaskBlueprint:
    """LLM-authored blueprint rendered into a Harbor task."""

    task_id: str
    title: str
    instruction_md: str
    difficulty: str
    category: str
    tags: list[str]
    output_path: str
    acceptance_criteria: list[str]
    environment: EnvironmentSpec
    support_files: list[SupportFile]
    data_builder_python: str
    oracle_python: str
    verifier: VerifierSpec
    evidence: list[EvidenceItem]
    assumptions: list[str]
    family_hypotheses: list[str]
    reference_tasks: list[str]
    validation_checks: list[str]
    agent_timeout_sec: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskBlueprint":
        raw = _require_dict(data, "task_blueprint")
        support_files_raw = raw.get("support_files", [])
        if not isinstance(support_files_raw, list):
            raise SchemaError("Expected 'support_files' to be a list")
        evidence_raw = raw.get("evidence", [])
        if not isinstance(evidence_raw, list):
            raise SchemaError("Expected 'evidence' to be a list")
        difficulty = _require_string(raw.get("difficulty"), "difficulty")
        if difficulty not in _VALID_DIFFICULTIES:
            raise SchemaError(f"'difficulty' must be one of {sorted(_VALID_DIFFICULTIES)}, got: {difficulty}")

        output_path = _require_string(raw.get("output_path"), "output_path")
        if not output_path.startswith("/"):
            raise SchemaError(f"'output_path' must be an absolute path, got: {output_path}")

        data_builder_python = _require_string(raw.get("data_builder_python"), "data_builder_python")
        _validate_python_syntax(data_builder_python, "data_builder_python")

        oracle_python = _require_string(raw.get("oracle_python"), "oracle_python")
        _validate_python_syntax(oracle_python, "oracle_python")

        agent_timeout_raw = raw.get("agent_timeout_sec")
        agent_timeout_sec = float(agent_timeout_raw) if agent_timeout_raw is not None else None

        return cls(
            task_id=slugify(_require_string(raw.get("task_id"), "task_id")),
            title=_require_string(raw.get("title"), "title"),
            instruction_md=_require_string(raw.get("instruction_md"), "instruction_md"),
            difficulty=difficulty,
            category=_require_string(raw.get("category"), "category"),
            tags=_require_list_of_strings(raw.get("tags"), "tags"),
            output_path=output_path,
            acceptance_criteria=_require_list_of_strings(raw.get("acceptance_criteria"), "acceptance_criteria"),
            environment=EnvironmentSpec.from_dict(raw.get("environment")),
            support_files=[SupportFile.from_dict(item) for item in support_files_raw],
            data_builder_python=data_builder_python,
            oracle_python=oracle_python,
            verifier=VerifierSpec.from_dict(raw.get("verifier")),
            evidence=[EvidenceItem.from_dict(item) for item in evidence_raw],
            assumptions=_require_list_of_strings(raw.get("assumptions"), "assumptions"),
            family_hypotheses=_require_list_of_strings(raw.get("family_hypotheses"), "family_hypotheses"),
            reference_tasks=_require_list_of_strings(raw.get("reference_tasks"), "reference_tasks"),
            validation_checks=_require_list_of_strings(raw.get("validation_checks"), "validation_checks"),
            agent_timeout_sec=agent_timeout_sec,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "task_id": self.task_id,
            "title": self.title,
            "instruction_md": self.instruction_md,
            "difficulty": self.difficulty,
            "category": self.category,
            "tags": self.tags,
            "output_path": self.output_path,
            "acceptance_criteria": self.acceptance_criteria,
            "environment": self.environment.to_dict(),
            "support_files": [item.to_dict() for item in self.support_files],
            "data_builder_python": self.data_builder_python,
            "oracle_python": self.oracle_python,
            "verifier": self.verifier.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
            "assumptions": self.assumptions,
            "family_hypotheses": self.family_hypotheses,
            "reference_tasks": self.reference_tasks,
            "validation_checks": self.validation_checks,
        }
        if self.agent_timeout_sec is not None:
            result["agent_timeout_sec"] = self.agent_timeout_sec
        return result
