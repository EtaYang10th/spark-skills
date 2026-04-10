"""End-to-end task generation pipeline for SPARK."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
from typing import Any

from spark_tasks_gen.llm import LLMConfig, call_json_llm
from spark_tasks_gen.models import PromptSpec, SchemaError, TaskBlueprint
from spark_tasks_gen.prompts import (
    CRITIQUE_SYSTEM,
    CRITIQUE_USER,
    HARBOR_FORMAT_SPEC,
    REPAIR_SYSTEM,
    TOOLS_BLUEPRINT_SYSTEM,
    TOOLS_BLUEPRINT_USER,
    TOOLS_REPAIR_USER,
)
from spark_tasks_gen.tools_pool import ToolEntry, format_tools_for_prompt, load_tools_catalog
from spark_tasks_gen.renderer import RenderResult, render_task
from spark_tasks_gen.validator import ValidationResult, validate_task


log = logging.getLogger(__name__)


@dataclass(slots=True)
class GenerationConfig:
    """Runtime settings for one task-generation run."""

    spark_root: Path
    llm: LLMConfig
    output_root: Path
    validation_output_dir: Path
    max_revisions: int = 3
    max_critique_only_repairs: int = 1
    schema_retries: int = 2


@dataclass(slots=True)
class GenerationAttempt:
    """One render-and-validate cycle."""

    attempt_index: int
    critique: dict[str, Any]
    render_result: RenderResult | None
    validation_result: ValidationResult | None
    blueprint: TaskBlueprint
    blocking_issues: list[dict[str, Any]] = field(default_factory=list)
    accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_index": self.attempt_index,
            "critique": self.critique,
            "blocking_issues": self.blocking_issues,
            "accepted": self.accepted,
            "render_result": {
                "task_dir": str(self.render_result.task_dir),
                "written_files": self.render_result.written_files,
                "artifact_dir": str(self.render_result.artifact_dir),
                "artifact_files": self.render_result.artifact_files,
            }
            if self.render_result
            else None,
            "validation_result": self.validation_result.to_feedback_dict() if self.validation_result else None,
            "blueprint": self.blueprint.to_dict(),
        }


@dataclass(slots=True)
class TaskGenerationResult:
    """Final result returned to the caller."""

    prompt_spec: PromptSpec
    attempts: list[GenerationAttempt] = field(default_factory=list)
    final_blueprint: TaskBlueprint | None = None
    final_task_dir: Path | None = None
    success: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_spec": self.prompt_spec.to_dict(),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "final_blueprint": self.final_blueprint.to_dict() if self.final_blueprint else None,
            "final_task_dir": str(self.final_task_dir) if self.final_task_dir else None,
            "success": self.success,
        }


def run_generation(prompt_spec: PromptSpec, config: GenerationConfig) -> TaskGenerationResult:
    """Generate a Harbor task, render it, and validate it with Harbor."""
    tools_catalog = load_tools_catalog()
    blueprint = _generate_blueprint_from_tools(prompt_spec, tools_catalog, config)

    result = TaskGenerationResult(prompt_spec=prompt_spec)
    critique_only_repairs = 0

    for attempt_index in range(config.max_revisions + 1):
        critique = _critique_blueprint(prompt_spec, blueprint, config)
        blocking_issues = _blocking_issues(critique)

        if blocking_issues and attempt_index < config.max_revisions and critique_only_repairs < config.max_critique_only_repairs:
            log.info(
                "Attempt %d: %d blocking critique issues, skipping render/validate",
                attempt_index,
                len(blocking_issues),
            )
            result.attempts.append(
                GenerationAttempt(
                    attempt_index=attempt_index,
                    critique=critique,
                    render_result=None,
                    validation_result=None,
                    blueprint=blueprint,
                    blocking_issues=blocking_issues,
                )
            )
            critique_only_repairs += 1
            blueprint = _do_repair(
                prompt_spec=prompt_spec,
                blueprint=blueprint,
                feedback={"critique": critique.get("issues", [])},
                config=config,
                tools_catalog=tools_catalog,
            )
            continue

        provenance = {
            "prompt_spec": prompt_spec.to_dict(),
            "attempt_index": attempt_index,
        }
        render_result = render_task(
            blueprint=blueprint,
            prompt_spec=prompt_spec,
            output_root=config.output_root,
            provenance=provenance,
            overwrite=True,
        )
        validation = validate_task(
            task_dir=render_result.task_dir,
            spark_root=config.spark_root,
            output_dir=config.validation_output_dir,
        )
        generation_attempt = GenerationAttempt(
            attempt_index=attempt_index,
            critique=critique,
            render_result=render_result,
            validation_result=validation,
            blueprint=blueprint,
            blocking_issues=blocking_issues,
        )
        result.attempts.append(generation_attempt)
        result.final_blueprint = blueprint
        result.final_task_dir = render_result.task_dir

        if validation.passed and not blocking_issues:
            result.success = True
            generation_attempt.accepted = True
            _write_trace(render_result.artifact_dir, result)
            return result

        if attempt_index >= config.max_revisions:
            _write_trace(render_result.artifact_dir, result)
            return result

        feedback = {
            "critique": critique.get("issues", []),
            "validation": validation.to_repair_feedback(),
        }
        _write_trace(render_result.artifact_dir, result)
        blueprint = _do_repair(
            prompt_spec=prompt_spec,
            blueprint=blueprint,
            feedback=feedback,
            config=config,
            tools_catalog=tools_catalog,
        )

    return result


def _generate_blueprint_from_tools(
    prompt_spec: PromptSpec,
    tools_catalog: list[ToolEntry],
    config: GenerationConfig,
) -> TaskBlueprint:
    user_msg = TOOLS_BLUEPRINT_USER.format(
        prompt_spec_json=_dump_json(prompt_spec.to_dict()),
        tools_catalog_json=format_tools_for_prompt(tools_catalog),
        format_spec=HARBOR_FORMAT_SPEC,
    )
    return _run_blueprint_call(
        system_msg=TOOLS_BLUEPRINT_SYSTEM,
        user_msg=user_msg,
        config=config,
    )


def _repair_blueprint_from_tools(
    prompt_spec: PromptSpec,
    blueprint: TaskBlueprint,
    tools_catalog: list[ToolEntry],
    feedback: dict[str, Any],
    config: GenerationConfig,
) -> TaskBlueprint:
    user_msg = TOOLS_REPAIR_USER.format(
        prompt_spec_json=_dump_json(prompt_spec.to_dict()),
        blueprint_json=_dump_json(blueprint.to_dict()),
        tools_catalog_json=format_tools_for_prompt(tools_catalog),
        feedback_json=_dump_json(feedback),
    )
    return _run_blueprint_call(
        system_msg=REPAIR_SYSTEM,
        user_msg=user_msg,
        config=config,
    )


def _do_repair(
    prompt_spec: PromptSpec,
    blueprint: TaskBlueprint,
    feedback: dict[str, Any],
    config: GenerationConfig,
    tools_catalog: list[ToolEntry],
) -> TaskBlueprint:
    return _repair_blueprint_from_tools(prompt_spec, blueprint, tools_catalog, feedback, config)


def _critique_blueprint(
    prompt_spec: PromptSpec,
    blueprint: TaskBlueprint,
    config: GenerationConfig,
) -> dict[str, Any]:
    user_msg = CRITIQUE_USER.format(
        prompt_spec_json=_dump_json(prompt_spec.to_dict()),
        blueprint_json=_dump_json(blueprint.to_dict()),
    )
    issues = _deterministic_critique_issues(prompt_spec, blueprint)
    try:
        critique = call_json_llm(CRITIQUE_SYSTEM, user_msg, config.llm)
        llm_issues = critique.get("issues", [])
        if not isinstance(llm_issues, list):
            raise ValueError("Critique response must contain an 'issues' list")
        issues.extend(_normalize_issue(item) for item in llm_issues)
    except Exception as exc:
        log.warning("LLM critique call failed (relying on deterministic checks only): %s", exc)
    return {"issues": issues}


def _run_blueprint_call(system_msg: str, user_msg: str, config: GenerationConfig) -> TaskBlueprint:
    current_user_msg = user_msg
    last_error: Exception | None = None
    for schema_attempt in range(config.schema_retries + 1):
        payload = call_json_llm(system_msg, current_user_msg, config.llm)
        try:
            blueprint = TaskBlueprint.from_dict(payload)
            log.info("Blueprint parsed successfully on schema attempt %d", schema_attempt)
            return blueprint
        except SchemaError as exc:
            last_error = exc
            current_user_msg = (
                f"{user_msg}\n\n"
                "The previous response failed schema validation. "
                f"Return a complete corrected JSON object. Schema error: {exc}"
            )
    raise RuntimeError(f"Could not obtain a valid blueprint JSON: {last_error}")


def _write_trace(artifact_dir: Path, result: TaskGenerationResult) -> None:
    trace_path = artifact_dir / "generation_trace.json"
    trace_path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


def _deterministic_critique_issues(
    prompt_spec: PromptSpec,
    blueprint: TaskBlueprint,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    output_path = blueprint.output_path
    locations = {
        "instruction": blueprint.instruction_md,
        "oracle": blueprint.oracle_python,
        "verifier": blueprint.verifier.test_code,
    }
    for area, content in locations.items():
        if output_path not in content:
            issues.append(
                {
                    "severity": "error",
                    "area": area,
                    "message": f"Output path `{output_path}` is missing from the {area}; instruction, oracle, and verifier must agree exactly.",
                }
            )

    prompt_blob = _prompt_blob(prompt_spec)

    for evidence in blueprint.evidence:
        if evidence.source_type == "prompt":
            if not _quote_matches_blob(prompt_blob, evidence.quote):
                issues.append(
                    {
                        "severity": "error",
                        "area": "evidence",
                        "message": f"Prompt evidence `{evidence.evidence_id}` does not quote a verbatim substring from the prompt specification.",
                    }
                )
            continue

        issues.append(
            {
                "severity": "error",
                "area": "evidence",
                "message": f"Evidence `{evidence.evidence_id}` uses unsupported source type `{evidence.source_type}`.",
            }
        )

    return issues


def _prompt_blob(prompt_spec: PromptSpec) -> str:
    return "\n".join(
        [
            prompt_spec.prompt,
            *prompt_spec.available_tools,
            *prompt_spec.environment_hints,
            *prompt_spec.constraints,
        ]
    )


def _quote_matches_blob(blob: str, quote: str) -> bool:
    if quote in blob:
        return True
    return _normalize_quote_text(quote) in _normalize_quote_text(blob)


def _normalize_quote_text(text: str) -> str:
    return " ".join(text.replace("\\\\", "\\").split())


def _normalize_issue(issue: object) -> dict[str, str]:
    if not isinstance(issue, dict):
        return {
            "severity": "error",
            "area": "critique",
            "message": f"Invalid critique issue payload: {issue!r}",
        }
    severity = str(issue.get("severity", "error")).strip().lower()
    if severity not in {"error", "warning"}:
        severity = "error"
    area = str(issue.get("area", "critique")).strip() or "critique"
    message = str(issue.get("message", "")).strip()
    if not message:
        message = f"Critique issue is missing a message: {issue!r}"
    return {
        "severity": severity,
        "area": area,
        "message": message,
    }


def _blocking_issues(critique: dict[str, Any]) -> list[dict[str, Any]]:
    issues = critique.get("issues", [])
    if not isinstance(issues, list):
        return [
            {
                "severity": "error",
                "area": "critique",
                "message": "Critique payload does not contain a valid issue list.",
            }
        ]
    return [issue for issue in issues if isinstance(issue, dict) and issue.get("severity") == "error"]


def _dump_json(payload: object) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)
