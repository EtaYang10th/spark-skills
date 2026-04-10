#!/usr/bin/env python3
"""SPARK Pipeline entry point — run iterative skill generation with web dashboard."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import threading
from pathlib import Path

import uvicorn

from spark_skills_gen.dashboard.app import app, create_event_listener, set_config, set_pipeline
from spark_skills_gen.executor import ExecutionConfig
from spark_skills_gen.pipeline import PipelineConfig, SparkPipeline
from spark_skills_gen.token_budgets import TokenBudgets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("spark")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SPARK — iterative skill generation pipeline")
    p.add_argument("--agent", default="qwen-coder", help="Harbor agent name")
    p.add_argument("--model", default="qwen3-coder-next", help="Model identifier")
    p.add_argument("--tasks-dir", default="tasks_no_skills_generate", help="Tasks directory name")
    p.add_argument("--output-dir", default="./spark-jobs", help="Harbor output directory")
    p.add_argument("--result-dir", default="./skills_gen_result", help="Skill generation result directory")
    p.add_argument("--max-retries", type=int, default=3, help="Max attempts per task")
    p.add_argument("--parallelism", type=int, default=4, help="Number of parallel tasks")
    p.add_argument("--limit", type=int, default=None, help="Limit number of tasks to run")
    p.add_argument("--tasks", nargs="*", default=None, help="Specific task names to run")
    p.add_argument("--timeout-multiplier", type=float, default=1.0, help="Agent timeout multiplier")
    p.add_argument("--port", type=int, default=8765, help="Dashboard web server port")
    p.add_argument("--no-dashboard", action="store_true", help="Disable the web dashboard")
    p.add_argument("--summary-model", default="", help="Model for reflect/skill summarization (defaults to --model)")
    p.add_argument("--summary-api-base", default=None, help="API base for summarization model")
    p.add_argument("--summary-api-key", default=None, help="API key for summarization model")
    p.add_argument("--token-budgets", default=None, help="Path to token budgets JSON file (default: token_budgets.json)")
    p.add_argument("--resume", action="store_true", help="Resume mode: skip tasks that already have generated skills")
    p.add_argument("--shuffle", action="store_true", help="Randomize task order before applying --limit")
    p.add_argument("--shared-result-dir", default="", help="Shared result directory across models; tasks with existing skills here are skipped")
    p.add_argument("--pdi-enabled", action="store_true", help="Enable PDI-guided exploration feedback during skill generation")
    p.add_argument("--pdi-observe-only", action="store_true", help="Compute & display PDI but do not inject interventions into agent prompts")
    p.add_argument("--agent-kwarg", action="append", default=[], metavar="KEY=VALUE",
                   help="Extra agent kwargs passed to Harbor (e.g. version=0.78.0). Repeatable.")
    return p.parse_args()


def _normalize_tasks_dir(
    spark_root: Path, tasks_dir: str, task_names: list[str] | None
) -> tuple[str, list[str] | None]:
    """If *tasks_dir* points directly to a single task, split into parent + task name."""
    if task_names:
        return tasks_dir, task_names
    td = spark_root / tasks_dir
    if td.is_dir() and (td / "instruction.md").exists():
        log.info("tasks-dir %r is itself a task — using parent as tasks root", tasks_dir)
        return str(Path(tasks_dir).parent), [td.name]
    return tasks_dir, task_names


def main() -> None:
    args = parse_args()
    spark_root = Path(__file__).parent.resolve()

    tasks_dir, task_names = _normalize_tasks_dir(spark_root, args.tasks_dir, args.tasks)

    budgets_path = Path(args.token_budgets) if args.token_budgets else spark_root / "token_budgets.json"
    if budgets_path.exists():
        budgets = TokenBudgets.from_json(budgets_path)
        log.info("Loaded token budgets from %s", budgets_path)
    else:
        budgets = TokenBudgets()
        log.info("Token budgets file %s not found, using defaults", budgets_path)

    env_overrides: dict[str, str] = {}
    if os.environ.get("OPENAI_API_KEY"):
        env_overrides["OPENAI_API_KEY"] = os.environ["OPENAI_API_KEY"]
    if os.environ.get("OPENAI_BASE_URL"):
        env_overrides["OPENAI_BASE_URL"] = os.environ["OPENAI_BASE_URL"]

    # Parse --agent-kwarg KEY=VALUE pairs
    agent_kwargs: dict[str, str] = {}
    for kv in getattr(args, "agent_kwarg", []):
        if "=" in kv:
            k, v = kv.split("=", 1)
            agent_kwargs[k] = v

    exec_config = ExecutionConfig(
        agent=args.agent,
        model=args.model,
        tasks_dir=tasks_dir,
        output_dir=args.output_dir,
        parallelism=args.parallelism,
        agent_timeout_multiplier=args.timeout_multiplier,
        env_overrides=env_overrides,
        agent_kwargs=agent_kwargs,
    )

    result_dir = Path(args.result_dir)
    if not result_dir.is_absolute():
        result_dir = spark_root / result_dir

    shared_result_dir = None
    if args.shared_result_dir:
        shared_result_dir = Path(args.shared_result_dir)
        if not shared_result_dir.is_absolute():
            shared_result_dir = spark_root / shared_result_dir

    pipeline_config = PipelineConfig(
        spark_root=spark_root,
        execution=exec_config,
        result_dir=result_dir,
        max_retries=args.max_retries,
        parallelism=args.parallelism,
        task_limit=args.limit,
        task_names=task_names,
        summary_model=args.summary_model or args.model,
        summary_api_base=args.summary_api_base or os.environ.get("OPENAI_BASE_URL"),
        summary_api_key=args.summary_api_key or os.environ.get("OPENAI_API_KEY"),
        dashboard_port=args.port,
        token_budgets=budgets,
        resume=args.resume,
        shuffle=args.shuffle,
        shared_result_dir=shared_result_dir,
        pdi_enabled=args.pdi_enabled or args.pdi_observe_only,
        pdi_observe_only=args.pdi_observe_only,
    )

    set_config({
        "agent": args.agent,
        "model": args.model,
        "tasks_dir": tasks_dir,
        "max_retries": args.max_retries,
        "parallelism": args.parallelism,
        "pdi_enabled": args.pdi_enabled or args.pdi_observe_only,
    })

    pipeline = SparkPipeline(pipeline_config)

    if args.no_dashboard:
        results = pipeline.run()
        _print_summary(results)
        return

    set_pipeline(pipeline)

    loop = asyncio.new_event_loop()

    def run_dashboard():
        asyncio.set_event_loop(loop)
        config = uvicorn.Config(
            app, host="0.0.0.0", port=args.port,
            log_level="warning", loop="asyncio",
        )
        server = uvicorn.Server(config)
        loop.run_until_complete(server.serve())

    dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
    dashboard_thread.start()

    import time
    time.sleep(1)

    listener = create_event_listener(loop)
    pipeline.events.add_listener(listener)

    log.info("Dashboard running at http://localhost:%d", args.port)
    log.info("Starting pipeline...")

    results = pipeline.run()
    _print_summary(results)

    log.info("Pipeline finished. Dashboard remains available. Press Ctrl+C to exit.")
    try:
        dashboard_thread.join()
    except KeyboardInterrupt:
        pass


def _print_summary(results: dict) -> None:
    total = len(results)
    passed = sum(1 for r in results.values() if r["success"])
    failed = total - passed
    print(f"\n{'='*60}")
    print(f"  SPARK Pipeline Summary")
    print(f"{'='*60}")
    print(f"  Total tasks:  {total}")
    print(f"  Passed:       {passed}")
    print(f"  Failed:       {failed}")
    print(f"  Pass rate:    {passed/total*100:.1f}%" if total > 0 else "  Pass rate:    N/A")
    print(f"{'='*60}\n")

    for name, r in sorted(results.items()):
        status_icon = "PASS" if r["success"] else "FAIL"
        print(f"  [{status_icon}] {name} (attempts: {r['attempts']}, reward: {r['final_reward']})")
    print()


if __name__ == "__main__":
    main()
