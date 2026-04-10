#!/bin/bash
set -euo pipefail

# Clean up leftover containers to avoid name conflicts
# docker rm -f $(docker ps -aq) 2>/dev/null || true
# docker network prune -f 2>/dev/null || true

cd "$(dirname "$0")/.."
source ~/.bashrc
conda activate spark

if [[ -f ".env" ]]; then
  set -a && source .env && set +a
fi



# ─── Configuration ───
#civ6-adjacency-optimizer/azure-bgp-oscillation-route-leak


model="${MODEL:-deepseek/deepseek-reasoner}"
tasks_dir="${TASKS_DIR:-tasks_no_skills_generate/}"
limit="${LIMIT:-99}"                          # set to empty string to run all tasks
max_retries="${MAX_RETRIES:-7}"              # max attempts per task
parallelism="${PARALLELISM:-8}"              # parallel task workers
dashboard_port="${DASHBOARD_PORT:-8767}"     # web dashboard port
result_dir="${RESULT_DIR:-./spark_skills_gen/skills_gen_result}"
pdi_enabled="${PDI_ENABLED:-false}"

# ── PDI path isolation ──
if [[ "$pdi_enabled" == "true" ]]; then
  result_dir="${RESULT_DIR:-./spark_skills_gen/skills_gen_result_pdi}"
fi
timeout_multiplier="${TIMEOUT_MULTIPLIER:-1.0}"
resume="${RESUME:-true}"                                                                   # re-run only tasks without generated skills
clean="${CLEAN:-true}"                                                                    # remove stale harbor job dirs before running
shuffle="${SHUFFLE:-true}"                                                                # randomize task order before applying limit
shared_result_dir="${SHARED_RESULT_DIR:-./spark_skills_gen/skills_gen_result/all_model}"   # shared result dir across models (empty = per-model)

# ── PDI shared path isolation ──
if [[ "$pdi_enabled" == "true" ]]; then
  shared_result_dir="${SHARED_RESULT_DIR:-./spark_skills_gen/skills_gen_result/all_model_pdi}"
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


token_budgets="${TOKEN_BUDGETS:-./token_budgets.json}"
echo "Using token budgets: $token_budgets"

# ─── Optionally clean stale harbor job dirs (CLEAN=true) ───
if [[ "$clean" == "true" ]]; then
  for job_dir in "./spark-jobs/spark-iter/${model}" "./spark-jobs/full_skill_gen/${model}"; do
    if [[ -d "$job_dir" ]]; then
      echo "Cleaning previous harbor job dirs under $job_dir ..."
      rm -rf "$job_dir"
    fi
  done
fi

# ─── Clean failed task dirs (no SKILL.md) in shared result dir ───
if [[ -n "$shared_result_dir" && -d "$shared_result_dir" ]]; then
  cleaned=0
  for task_dir in "$shared_result_dir"/*/; do
    [[ -d "$task_dir" ]] || continue
    if [[ ! -f "${task_dir}SKILL.md" ]]; then
      rm -rf "$task_dir"
      cleaned=$((cleaned + 1))
    fi
  done
  if [[ $cleaned -gt 0 ]]; then
    echo "Cleaned $cleaned failed task dirs (no SKILL.md) from $shared_result_dir"
  fi
fi

# ─── Derive provider-prefixed model name for LiteLLM / Harbor ───
# Harbor & gemini-cli require "provider/model_name" format.
harbor_model="$model"
summary_model_args=()
case "$model" in
  gemini-*)
    harbor_model="gemini/${model}"
    summary_model_args=(--summary-model "gemini/${model}" --summary-api-key "${GOOGLE_API_KEY:-}")
    ;;
  deepseek/*)
    # already has provider prefix
    ;;
esac

echo "Starting SPARK iterative pipeline (agent=$agent, model=$model, dashboard at http://localhost:$dashboard_port)"

limit_args=()
if [[ -n "$limit" ]]; then
  limit_args=(--limit "$limit")
fi

resume_args=()
if [[ "$resume" == "true" ]]; then
  resume_args=(--resume)
fi

shuffle_args=()
if [[ "$shuffle" == "true" ]]; then
  shuffle_args=(--shuffle)
fi

shared_args=()
if [[ -n "$shared_result_dir" ]]; then
  shared_args=(--shared-result-dir "$shared_result_dir")
fi

pdi_args=()
if [[ "$pdi_enabled" == "true" ]]; then
  pdi_args=(--pdi-enabled)
  pdi_task_file="$(dirname "$0")/../pdi_rerun_tasks.txt"
  if [[ -f "$pdi_task_file" ]]; then
    pdi_task_names=()
    while IFS= read -r line; do
      task_name=$(echo "$line" | sed 's/#.*//' | xargs)
      [[ -n "$task_name" ]] && pdi_task_names+=("$task_name")
    done < "$pdi_task_file"
    if [[ ${#pdi_task_names[@]} -gt 0 ]]; then
      pdi_args+=(--tasks "${pdi_task_names[@]}")
      echo "PDI mode: loaded ${#pdi_task_names[@]} tasks from pdi_rerun_tasks.txt"
    fi
  else
    echo "WARNING: PDI enabled but pdi_rerun_tasks.txt not found at $pdi_task_file"
  fi
fi

# uv run python run_pipeline.py \
#   --agent "$agent" \
#   --model "$harbor_model" \
#   --tasks-dir "$tasks_dir" \
#   --result-dir "$result_dir" \
#   --max-retries "$max_retries" \
#   --parallelism "$parallelism" \
#   --port "$dashboard_port" \
#   --timeout-multiplier "$timeout_multiplier" \
#   --token-budgets "$token_budgets" \
#   "${limit_args[@]}" \
#   "${resume_args[@]}" \
#   "${shuffle_args[@]}" \
#   "${shared_args[@]}" \
#   "${summary_model_args[@]}" \
#   "$@"


# agent list
  # oracle	默认，参考答案
  # nop	空操作
  # claude-code	Anthropic Claude Code
  # cline-cli	Cline CLI
  # terminus / terminus-1 / terminus-2	Terminus 系列
  # aider	Aider
  # codex	OpenAI Codex
  # cursor-cli	Cursor CLI
  # gemini-cli	Google Gemini CLI
  # goose	Goose
  # mini-swe-agent / swe-agent	SWE-Agent
  # opencode	OpenCode
  # openhands	OpenHands
  # qwen-coder	Qwen Coder
