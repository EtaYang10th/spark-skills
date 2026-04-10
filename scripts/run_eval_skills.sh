#!/bin/bash
set -euo pipefail

# # Clean up leftover containers to avoid name conflicts
# docker rm -f $(docker ps -aq) 2>/dev/null || true
# docker network prune -f 2>/dev/null || true

cd "$(dirname "$0")/.."
source ~/.bashrc
conda activate spark

if [[ -f ".env" ]]; then
  set -a && source .env && set +a
fi

model="${MODEL:-glm-4.7-flashx}"
tasks_dir="${TASKS_DIR:-tasks_no_skills_generate}"
skills_result_dir="${SKILLS_RESULT_DIR:-./spark_skills_gen/skills_gen_result/}"
skill_source_model="${SKILL_SOURCE_MODEL:-all_model}"
output_dir="${OUTPUT_DIR:-./spark-jobs}"
eval_result_dir="${EVAL_RESULT_DIR:-./spark_skills_gen/skills_eval_result}"
staging_root="${STAGING_ROOT:-./save/.spark-skill-eval}"
parallelism="${PARALLELISM:-8}"
timeout_multiplier="${TIMEOUT_MULTIPLIER:-0.8}"
limit="${LIMIT:-}"
human_skills_dir="${HUMAN_SKILLS_DIR:-tasks}"
pdi_enabled="${PDI_ENABLED:-true}"

# ── PDI path isolation ──
# When PDI is enabled:
#   - skill_source_model → all_model_pdi (PDI-generated skills)
#   - eval_result_dir    → skills_eval_result_pdi (isolated output)
#   - phases             → generated only (baseline & human reuse existing results)
#   - tasks              → auto-loaded from pdi_rerun_tasks.txt, filtered by SKILL.md
#   - ref_eval_result_dir → original skills_eval_result (for baseline/human cross-ref)
ref_eval_result_dir=""
if [[ "$pdi_enabled" == "true" ]]; then
  ref_eval_result_dir="${REF_EVAL_RESULT_DIR:-./spark_skills_gen/skills_eval_result}"
  skill_source_model="${SKILL_SOURCE_MODEL:-all_model_pdi}"
  eval_result_dir="${EVAL_RESULT_DIR:-./spark_skills_gen/skills_eval_result_pdi}"
  staging_root="${STAGING_ROOT:-./save/.spark-skill-eval-pdi}"
fi

# ─── Auto-detect agent & API endpoint from model name ───
# Override with AGENT= to force a specific agent.
if [[ -n "${AGENT:-}" ]]; then
  agent="$AGENT"
else
  case "$model" in
    gpt-*)                agent="codex"       ;;
    claude-*)             agent="claude-code"  ;;
    qwen*|qwq*)           agent="qwen-coder"  ;;
    gemini-*)             agent="gemini-cli"   ;;
    deepseek/*)           agent="opencode"     ;;
    glm-*|zhipu-*)        agent="qwen-coder"   ;;
    *)                    agent="codex"        ;;
  esac
fi

# Route API credentials based on model provider.
case "$model" in
  gpt-*|o1-*|o3-*|o4-*)
    ;;
  qwen*|qwq*)
    if [[ -n "${DASHSCOPE_API_KEY:-}" ]]; then
      export OPENAI_API_KEY="$DASHSCOPE_API_KEY"
      export OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
    fi
    ;;
  deepseek/*)
    if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
      export OPENAI_API_KEY="$DEEPSEEK_API_KEY"
      export OPENAI_BASE_URL="https://api.deepseek.com/v1"
    fi
    ;;
  glm-*|zhipu-*)
    if [[ -n "${ZHIPU_API_KEY:-}" ]]; then
      export OPENAI_API_KEY="$ZHIPU_API_KEY"
      export OPENAI_BASE_URL="https://open.bigmodel.cn/api/paas/v4"
    fi
    ;;
esac

limit_args=()
if [[ -n "$limit" ]]; then
  limit_args=(--limit "$limit")
fi

task_args=()
if [[ -n "${TASKS:-}" ]]; then
  read -r -a requested_tasks <<< "$TASKS"
  task_args=(--tasks "${requested_tasks[@]}")
fi

phases_args=()
if [[ -n "${PHASES:-}" ]]; then
  read -r -a requested_phases <<< "${PHASES}"
  phases_args=(--phases "${requested_phases[@]}")
fi

# ── PDI mode: auto-load tasks & force --phases generated ──
if [[ "$pdi_enabled" == "true" ]]; then
  # Force generated-only evaluation (baseline & human already in skills_eval_result)
  phases_args=(--phases generated)

  # Auto-load task list from pdi_rerun_tasks.txt (unless TASKS was explicitly set)
  if [[ -z "${TASKS:-}" ]]; then
    pdi_task_file="$(dirname "$0")/../pdi_rerun_tasks.txt"
    if [[ -f "$pdi_task_file" ]]; then
      pdi_task_names=()
      while IFS= read -r line; do
        task_name=$(echo "$line" | sed 's/#.*//' | xargs)
        [[ -n "$task_name" ]] || continue
        # Only include tasks that have a generated SKILL.md
        if [[ -f "${skills_result_dir}/${skill_source_model}/${task_name}/SKILL.md" ]]; then
          pdi_task_names+=("$task_name")
        else
          echo "PDI eval: skipping $task_name (no SKILL.md)"
        fi
      done < "$pdi_task_file"
      if [[ ${#pdi_task_names[@]} -gt 0 ]]; then
        task_args=(--tasks "${pdi_task_names[@]}")
        echo "PDI eval: ${#pdi_task_names[@]} tasks with SKILL.md loaded from pdi_rerun_tasks.txt"
      else
        echo "ERROR: No PDI tasks have generated SKILL.md. Nothing to evaluate." >&2
        exit 1
      fi
    else
      echo "ERROR: PDI enabled but pdi_rerun_tasks.txt not found at $pdi_task_file" >&2
      exit 1
    fi
  fi

  echo "PDI eval mode: --phases generated | skill_source=$skill_source_model | eval_dir=$eval_result_dir"
fi



ref_eval_args=()
if [[ -n "$ref_eval_result_dir" ]]; then
  ref_eval_args=(--ref-eval-result-dir "$ref_eval_result_dir")
fi

uv run python run_eval_skills.py \
  --agent "$agent" \
  --model "$model" \
  --tasks-dir "$tasks_dir" \
  --skills-result-dir "$skills_result_dir" \
  --skill-source-model "$skill_source_model" \
  --output-dir "$output_dir" \
  --eval-result-dir "$eval_result_dir" \
  --staging-root "$staging_root" \
  --parallelism "$parallelism" \
  --timeout-multiplier "$timeout_multiplier" \
  --human-skills-dir "$human_skills_dir" \
  --resume \
  "${limit_args[@]}" \
  "${task_args[@]}" \
  "${phases_args[@]}" \
  "${ref_eval_args[@]}" \
  "$@"
