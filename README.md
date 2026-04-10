# SPARK: Self-evolving Pipelines for Autonomous Runnable Tasks and Skill Generation

SPARK is a research prototype for autonomous runnable task construction and transferable skill generation.

It is built around two decoupled pipelines:

- `spark_tasks_gen`: turns prompt-level task ideas into runnable, oracle-validated Harbor tasks
- `spark_skills_gen`: distills reusable procedural knowledge from validated interaction trajectories into `SKILL.md`

## Pipeline 1: Runnable Task Construction

Given a prompt specification with task goal, tool hints, environment hints, and constraints, SPARK:

1. generates a structured `TaskBlueprint`
2. critiques and repairs the blueprint when needed
3. renders it into a concrete Harbor task directory
4. validates the task by executing the oracle in the target environment

Task generation is treated as a build-and-verify process rather than a single LLM call. Only tasks that pass deterministic oracle validation are kept.

Entry point:

```bash
uv run python run_tasks_gen.py \
  --prompt-file spark_tasks_gen/examples/3d_scan_calc_prompt.json \
  --model gpt-5.4
```

Outputs:

- runnable tasks: `spark_tasks_gen/generated_tasks/<task-id>/`
- generation artifacts: `spark_tasks_gen/generated_tasks/_artifacts/<task-id>/`
- validation runs: `spark-jobs/`

## Pipeline 2: Closed-loop Skill Generation

A stronger teacher model explores each task inside a Docker/Harbor sandbox over multiple attempts.

1. execute the task
2. judge the outcome from verifier outputs and `result.json`
3. on failure, rewrite a structured exploration memo for the next retry
4. on success, distill the successful trajectory together with prior failures into `SKILL.md`
5. save trajectories and final skill artifacts for later transfer

The exploration memo is the compact state carried across retries. It summarizes attempts, key commands, verified facts, the current error pattern, and the next strategy. In the paper's framing, posterior experience gathered during exploration is distilled into a reusable prior for downstream student models.

The current implementation also includes optional PDI-guided retry intervention.

Entry point:

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

- execution logs: `spark-jobs/`
- trajectories and attempt records: `spark_skills_gen/skills_gen_result/<model>/<task-name>/`
- distilled skills: `spark_skills_gen/skills_gen_result/<model>/<task-name>/SKILL.md`

## Requirements

- Python 3.12
- `uv`
- Docker
- Harbor
- access to an OpenAI-compatible API endpoint

Environment variables:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`

Quick setup:

```bash
cp .env_example .env
uv sync
```

## Repository Layout

- `run_tasks_gen.py`: task-construction entry point
- `run_pipeline.py`: skill-generation entry point
- `spark_tasks_gen/`: blueprinting, rendering, and oracle validation
- `spark_skills_gen/`: execution, judging, reflection, distillation, and dashboard
- `spark-jobs/`: Harbor job outputs
