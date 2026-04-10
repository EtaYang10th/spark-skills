#!/usr/bin/env python3
"""Evaluate whether generated skills improve Harbor task success."""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections import defaultdict
from pathlib import Path

from spark_skills_gen.evaluator import EvaluationConfig, evaluate_generated_skills

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SPARK generated-skill evaluator")
    parser.add_argument("--agent", default="qwen-coder", help="Harbor agent name")
    parser.add_argument("--model", default="qwen3-coder-next", help="Evaluation model identifier")
    parser.add_argument("--tasks-dir", default="tasks_no_skills_generate", help="Source tasks directory name")
    parser.add_argument("--skills-result-dir", default="./spark_skills_gen/skills_gen_result", help="Generated skill result directory")
    parser.add_argument("--skill-source-model", default="", help="Model directory to read generated SKILL.md files from (defaults to --model)")
    parser.add_argument("--output-dir", default="./spark-jobs", help="Harbor output directory")
    parser.add_argument("--eval-result-dir", default="./spark_skills_gen/skills_eval_result", help="Directory for evaluation summaries")
    parser.add_argument("--staging-root", default="./save/.spark-skill-eval", help="Temporary directory for staged tasks with generated skills")
    parser.add_argument("--parallelism", type=int, default=4, help="Number of parallel Harbor workers")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of comparable tasks to evaluate")
    parser.add_argument("--tasks", nargs="*", default=None, help="Specific comparable task names to evaluate")
    parser.add_argument("--timeout-multiplier", type=float, default=0.5, help="Agent timeout multiplier passed to Harbor")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip tasks already present in compatible reports or completed Harbor job outputs",
    )
    parser.add_argument("--human-skills-dir", default="tasks", help="Directory with human-written skills for three-way comparison")
    parser.add_argument(
        "--phases",
        nargs="*",
        default=None,
        choices=["baseline", "generated", "human"],
        help="Which evaluation phases to run (default: all three). E.g. --phases baseline",
    )
    parser.add_argument(
        "--agent-kwarg",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra agent keyword arguments passed to Harbor (e.g. version=0.78.0)",
    )
    parser.add_argument(
        "--ref-eval-result-dir",
        default=None,
        help="Reference eval result dir to cross-load baseline/human results (PDI mode)",
    )
    return parser.parse_args()


def _load_skill_source_models(skills_result_dir: Path, skill_source_model: str, task_names: list[str]) -> dict[str, str]:
    """Read trajectory.jsonl for each task and extract the model that generated the skill."""
    model_dir = skills_result_dir / skill_source_model
    task_to_model: dict[str, str] = {}
    for task_name in task_names:
        traj_path = model_dir / task_name / "trajectory.jsonl"
        if not traj_path.is_file():
            task_to_model[task_name] = "unknown"
            continue
        found = False
        with open(traj_path) as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "task_summary":
                    cfg = obj.get("exploration_config", {})
                    task_to_model[task_name] = cfg.get("model", "unknown")
                    found = True
                    break
        if not found:
            task_to_model[task_name] = "unknown"
    return task_to_model


def main() -> None:
    args = parse_args()
    spark_root = Path(__file__).parent.resolve()

    env_overrides: dict[str, str] = {}
    if os.environ.get("OPENAI_API_KEY"):
        env_overrides["OPENAI_API_KEY"] = os.environ["OPENAI_API_KEY"]
    if os.environ.get("OPENAI_BASE_URL"):
        env_overrides["OPENAI_BASE_URL"] = os.environ["OPENAI_BASE_URL"]

    agent_kwargs: dict[str, str] = {}
    for item in args.agent_kwarg:
        key, _, value = item.partition("=")
        if key:
            agent_kwargs[key] = value

    config = EvaluationConfig(
        spark_root=spark_root,
        agent=args.agent,
        model=args.model,
        tasks_dir=args.tasks_dir,
        skills_result_dir=Path(args.skills_result_dir),
        skill_source_model=args.skill_source_model,
        output_dir=Path(args.output_dir),
        eval_result_dir=Path(args.eval_result_dir),
        staging_root=Path(args.staging_root),
        parallelism=args.parallelism,
        agent_timeout_multiplier=args.timeout_multiplier,
        task_limit=args.limit,
        task_names=args.tasks,
        env_overrides=env_overrides,
        agent_kwargs=agent_kwargs,
        resume=args.resume,
        human_skills_dir=args.human_skills_dir,
        phases=set(args.phases) if args.phases else {"baseline", "generated", "human"},
        ref_eval_result_dir=Path(args.ref_eval_result_dir) if args.ref_eval_result_dir else None,
    )
    report = evaluate_generated_skills(config)

    is_pdi = report.get("pdi_mode", False)

    if is_pdi:
        gen_no_pdi = report["with_generated_skills"]
        gen_pdi = report["with_generated_skills_pdi"]

        # ── Load skill source model for each task ──
        task_to_model = _load_skill_source_models(
            config.skills_result_dir, config.skill_source_model, report["task_names"]
        )

        print(f"\n{'=' * 70}")
        print("  SPARK PDI Skill Evaluation")
        print(f"{'=' * 70}")
        print(f"  Tasks evaluated:         {len(report['task_names'])}")
        if report.get("resumed_from"):
            print(f"  Resumed from:            {report['resumed_from']}")
        print(f"  Generated (no PDI):      {gen_no_pdi['pass_rate'] * 100:.1f}% ({gen_no_pdi['passed']}/{gen_no_pdi['total']})")
        print(f"  Generated (PDI):         {gen_pdi['pass_rate'] * 100:.1f}% ({gen_pdi['passed']}/{gen_pdi['total']})")
        print(f"{'=' * 70}")

        # ── Per-model breakdown ──
        model_groups: dict[str, list[str]] = defaultdict(list)
        for task_name in report["task_names"]:
            model_groups[task_to_model.get(task_name, "unknown")].append(task_name)

        print(f"\n{'─' * 70}")
        print("  PDI improvement by source model")
        print(f"{'─' * 70}")

        for model_name in sorted(model_groups):
            tasks_in_group = model_groups[model_name]
            n = len(tasks_in_group)

            np_rewards = []
            p_rewards = []
            np_pass = 0
            p_pass = 0
            improved = 0
            degraded = 0
            unchanged = 0

            for t in tasks_in_group:
                delta = report["per_task_delta"][t]
                npr = delta["generated_reward"]
                pr = delta["generated_pdi_reward"]
                np_rewards.append(npr)
                p_rewards.append(pr)
                if delta["generated_passed"]:
                    np_pass += 1
                if delta["generated_pdi_passed"]:
                    p_pass += 1
                d = pr - npr
                if d > 1e-9:
                    improved += 1
                elif d < -1e-9:
                    degraded += 1
                else:
                    unchanged += 1

            mean_np = sum(np_rewards) / n if n else 0
            mean_p = sum(p_rewards) / n if n else 0
            mean_delta = mean_p - mean_np

            print(f"\n  [{model_name}]  ({n} tasks)")
            print(f"    No PDI:     mean_reward={mean_np:.3f}  pass={np_pass}/{n}")
            print(f"    With PDI:   mean_reward={mean_p:.3f}  pass={p_pass}/{n}  Δ_reward={mean_delta:+.3f}  Δ_pass={p_pass - np_pass:+d}")
            print(f"    Improved: {improved}  Degraded: {degraded}  Unchanged: {unchanged}")
            print(f"    Tasks: {', '.join(sorted(tasks_in_group))}")

        print(f"\n{'=' * 70}\n")
        return

    baseline = report["baseline"]
    skilled = report["with_generated_skills"]
    human = report.get("with_human_skills")
    has_human = human is not None

    # ── Load skill source model for each task ──
    task_to_model = _load_skill_source_models(
        config.skills_result_dir, config.skill_source_model, report["task_names"]
    )

    # ── Overall summary ──
    print(f"\n{'=' * 70}")
    print("  SPARK Skill Evaluation")
    print(f"{'=' * 70}")
    print(f"  Tasks evaluated:         {len(report['task_names'])}")
    if report.get("resumed_from"):
        print(f"  Resumed from:            {report['resumed_from']}")
    print(f"  Baseline pass rate:      {baseline['pass_rate'] * 100:.1f}% ({baseline['passed']}/{baseline['total']})")
    print(f"  Generated pass rate:     {skilled['pass_rate'] * 100:.1f}% ({skilled['passed']}/{skilled['total']})")
    if has_human:
        print(f"  Human-skill pass rate:   {human['pass_rate'] * 100:.1f}% ({human['passed']}/{human['total']})")
    print(f"{'=' * 70}")

    # ── Per-model breakdown ──
    model_groups: dict[str, list[str]] = defaultdict(list)
    for task_name in report["task_names"]:
        model_groups[task_to_model.get(task_name, "unknown")].append(task_name)

    print(f"\n{'─' * 70}")
    print("  Skills improvement by source model")
    print(f"{'─' * 70}")

    for model_name in sorted(model_groups):
        tasks_in_group = model_groups[model_name]
        n = len(tasks_in_group)

        b_rewards = []
        g_rewards = []
        h_rewards = []
        b_pass = 0
        g_pass = 0
        h_pass = 0
        improved = 0
        degraded = 0
        unchanged = 0

        for t in tasks_in_group:
            delta = report["per_task_delta"][t]
            br = delta["baseline_reward"]
            gr = delta["generated_reward"]
            b_rewards.append(br)
            g_rewards.append(gr)
            if delta["baseline_passed"]:
                b_pass += 1
            if delta["generated_passed"]:
                g_pass += 1
            d = gr - br
            if d > 1e-9:
                improved += 1
            elif d < -1e-9:
                degraded += 1
            else:
                unchanged += 1

            if has_human and "human_reward" in delta:
                h_rewards.append(delta["human_reward"])
                if delta.get("human_passed", False):
                    h_pass += 1

        mean_b = sum(b_rewards) / n if n else 0
        mean_g = sum(g_rewards) / n if n else 0
        mean_delta = mean_g - mean_b

        print(f"\n  [{model_name}]  ({n} tasks)")
        print(f"    Baseline:   mean_reward={mean_b:.3f}  pass={b_pass}/{n}")
        print(f"    Generated:  mean_reward={mean_g:.3f}  pass={g_pass}/{n}  Δ_reward={mean_delta:+.3f}  Δ_pass={g_pass - b_pass:+d}")
        if h_rewards:
            mean_h = sum(h_rewards) / len(h_rewards)
            print(f"    Human:      mean_reward={mean_h:.3f}  pass={h_pass}/{len(h_rewards)}  Δ_reward={mean_h - mean_b:+.3f}  Δ_pass={h_pass - b_pass:+d}")
        print(f"    Improved: {improved}  Degraded: {degraded}  Unchanged: {unchanged}")
        print(f"    Tasks: {', '.join(sorted(tasks_in_group))}")

    print(f"\n{'=' * 70}\n")


if __name__ == "__main__":
    main()
