# SPARK: Self-evolving Pipelines for Autonomous Runnable Tasks and Skill Generation

SPARK is a research prototype for turning task specifications into runnable Harbor tasks, then distilling reusable `SKILL.md` files from agent trajectories.

This repository is organized around two core pipelines:

- `spark_tasks_gen`: prompt-to-task generation
- `spark_skills_gen`: execution-to-skill generation

## Pipeline 1: Task Generation

The task-generation pipeline converts a prompt specification into a validated Harbor task.

1. Read a prompt spec with the task goal, tool hints, environment hints, and constraints.
2. Retrieve reference tasks and tool metadata.
3. Generate a structured `TaskBlueprint`.
4. Critique and repair the blueprint when schema or design issues are detected.
5. Render a complete Harbor task package.
6. Run oracle validation in Harbor and only keep tasks that pass validation.

Main entry point:

```bash
uv run python run_tasks_gen.py \
  --prompt-file spark_tasks_gen/examples/3d_scan_calc_prompt.json \
  --model gpt-5.4
```

Outputs:

- validated tasks: `spark_tasks_gen/generated_tasks/<task-id>/`
- generation traces: `spark_tasks_gen/generated_tasks/_artifacts/<task-id>/`
- Harbor validation runs: `spark-jobs/`

## Pipeline 2: Skill Generation

The skill-generation pipeline turns repeated task execution into reusable skills.

1. Execute an agent on a task inside Harbor.
2. Judge the attempt from verifier outputs and `result.json`.
3. On failure, summarize the attempt into a compact exploration memo for the next retry.
4. On success, distill the successful trajectory into `SKILL.md`.
5. Save trajectories, attempt records, and final skill artifacts.

The current implementation also supports optional PDI-guided retry feedback.

Main entry point:

```bash
uv run python run_pipeline.py \
  --agent qwen-coder \
  --model qwen3-coder-next \
  --tasks-dir tasks_no_skills_generate \
  --result-dir spark_skills_gen/skills_gen_result \
  --max-retries 3 \
  --parallelism 4
```

Use `--no-dashboard` if you only want CLI output.

Outputs:

- Harbor execution logs: `spark-jobs/`
- trajectories and attempt records: `spark_skills_gen/skills_gen_result/<model>/<task-name>/`
- distilled skills: `spark_skills_gen/skills_gen_result/<model>/<task-name>/SKILL.md`

## Requirements

- Python 3.12
- `uv`
- Docker
- Harbor
- Access to an OpenAI-compatible API endpoint

Environment variables:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`

Quick setup:

```bash
cp .env_example .env
uv sync
```

## Repository Layout

- `run_tasks_gen.py`: task-generation entry point
- `run_pipeline.py`: skill-generation entry point
- `spark_tasks_gen/`: blueprinting, rendering, and validation
- `spark_skills_gen/`: execution, judging, reflection, summarization, and dashboard
- `spark-jobs/`: Harbor job outputs
- `save/`: local temporary artifacts
