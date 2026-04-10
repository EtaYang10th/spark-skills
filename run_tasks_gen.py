#!/usr/bin/env python3
"""CLI entry point for SPARK task generation."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from spark_tasks_gen.llm import LLMConfig
from spark_tasks_gen.models import PromptSpec
from spark_tasks_gen.pipeline import GenerationConfig, run_generation


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SPARK task generation pipeline")
    parser.add_argument("--prompt-file", required=True, help="JSON prompt specification file")
    parser.add_argument(
        "--model",
        default=os.environ.get("SPARK_TASK_GEN_MODEL", "gpt-5.4"),
        help="Model identifier for blueprint generation",
    )
    parser.add_argument("--api-base", default=os.environ.get("OPENAI_BASE_URL"), help="Optional custom API base")
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY"), help="Optional API key")
    parser.add_argument(
        "--reference-tasks-dir",
        default=None,
        help="Optional override for the reference tasks directory",
    )
    parser.add_argument(
        "--top-k-references",
        type=int,
        default=None,
        help="Optional override for how many reference tasks to retrieve",
    )
    parser.add_argument(
        "--exclude-reference-task",
        action="append",
        default=[],
        help="Reference task name to exclude from retrieval; may be passed multiple times",
    )
    parser.add_argument(
        "--output-root",
        default="spark_tasks_gen/generated_tasks",
        help="Directory where generated Harbor tasks are written",
    )
    parser.add_argument(
        "--validation-output-dir",
        default="spark-jobs",
        help="Directory where Harbor oracle validation outputs are written",
    )
    parser.add_argument("--max-tokens", type=int, default=None, help="Override LLM max_tokens (default depends on provider)")
    parser.add_argument("--max-revisions", type=int, default=3, help="Maximum blueprint repair rounds")
    parser.add_argument("--schema-retries", type=int, default=2, help="Retries for invalid JSON/schema responses")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    spark_root = Path(__file__).parent.resolve()
    prompt_path = _resolve_path(args.prompt_file, spark_root)
    prompt_spec = PromptSpec.from_json_text(prompt_path.read_text())
    if args.reference_tasks_dir:
        prompt_spec.reference_tasks_dir = args.reference_tasks_dir
    if args.top_k_references is not None:
        prompt_spec.top_k_references = args.top_k_references
    if args.exclude_reference_task:
        prompt_spec.excluded_reference_tasks = _merge_unique(
            prompt_spec.excluded_reference_tasks,
            args.exclude_reference_task,
        )
    prompt_spec.output_dir = args.output_root

    output_root = Path(prompt_spec.output_dir)
    if not output_root.is_absolute():
        output_root = spark_root / output_root

    validation_output_dir = Path(args.validation_output_dir)
    if not validation_output_dir.is_absolute():
        validation_output_dir = spark_root / validation_output_dir

    llm_config = LLMConfig(
        model=args.model,
        api_base=args.api_base,
        api_key=args.api_key,
    )
    if args.max_tokens is not None:
        llm_config.max_tokens = args.max_tokens

    config = GenerationConfig(
        spark_root=spark_root,
        llm=llm_config,
        output_root=output_root,
        validation_output_dir=validation_output_dir,
        max_revisions=args.max_revisions,
        schema_retries=args.schema_retries,
    )
    result = run_generation(prompt_spec, config)
    _print_summary(result)


def _resolve_path(raw_path: str, spark_root: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return spark_root / path


def _merge_unique(existing: list[str], extra: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*existing, *extra]:
        if value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


def _print_summary(result: object) -> None:
    if not hasattr(result, "attempts"):
        return
    print("\n============================================================")
    print("  SPARK Task Generation Summary")
    print("============================================================")
    print(f"  Success:       {getattr(result, 'success', False)}")
    print(f"  Attempts:      {len(getattr(result, 'attempts', []))}")
    print(f"  Output dir:    {getattr(result, 'final_task_dir', None)}")
    print("============================================================\n")


if __name__ == "__main__":
    main()
