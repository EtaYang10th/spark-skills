#!/bin/bash
# Single-shot LLM bare test — no retries, no exploration memo, no skill injection.
# Results: ./spark-jobs/bare-test/<model>/<job-name>/
set -euo pipefail

cd "$(dirname "$0")/.."
source ~/.bashrc
conda activate spark

if [[ -f ".env" ]]; then
  set -a && source .env && set +a
fi

# ─── Configuration ───
model="${MODEL:-gemini-3-flash-preview}"
tasks_dir="${TASKS_DIR:-tasks_no_skills_generate}"
output_dir="${OUTPUT_DIR:-./spark-jobs}"
parallelism="${PARALLELISM:-8}"
timeout_multiplier="${TIMEOUT_MULTIPLIER:-1.0}"
limit="${LIMIT:-}"

# ─── Auto-detect agent ───
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

# ─── Route API credentials ───
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

job_name="bare-test/${model}"
echo "═══════════════════════════════════════════════════════════"
echo "  Bare LLM Test (no retries, no skills, single shot)"
echo "  Model:  $model"
echo "  Agent:  $agent"
echo "  Tasks:  $tasks_dir"
echo "  Output: $output_dir/$job_name"
echo "═══════════════════════════════════════════════════════════"

# ─── Build task selection args ───
task_args=()
if [[ -n "${TASKS:-}" ]]; then
  read -r -a requested_tasks <<< "$TASKS"
  for t in "${requested_tasks[@]}"; do
    task_args+=(-t "$t")
  done
fi

limit_args=()
if [[ -n "$limit" ]]; then
  limit_args=(--limit "$limit")
fi

# ─── Clean previous run for this model ───
if [[ -d "$output_dir/$job_name" ]]; then
  echo "Removing previous bare-test results at $output_dir/$job_name"
  rm -rf "$output_dir/$job_name"
fi

# ─── Run harbor directly — single shot ───
uv run harbor run \
  -p "$tasks_dir" \
  -a "$agent" \
  -m "$model" \
  -o "$output_dir" \
  --job-name "$job_name" \
  -n "$parallelism" \
  --agent-timeout-multiplier "$timeout_multiplier" \
  "${task_args[@]}" \
  "${limit_args[@]}" \
  "$@"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Done. Results at:"
echo "  $output_dir/$job_name/"
echo ""
echo "  Overall result.json:"
echo "  $output_dir/$job_name/result.json"
echo "═══════════════════════════════════════════════════════════"
