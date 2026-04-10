#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."
source ~/.bashrc
conda activate spark

if [[ -f ".env" ]]; then
  set -a && source .env && set +a
fi

if [[ -z "${OPENAI_API_KEY:-}" && -n "${DASHSCOPE_API_KEY:-}" ]]; then
  export OPENAI_API_KEY="$DASHSCOPE_API_KEY"
fi

if [[ -z "${OPENAI_BASE_URL:-}" && -n "${DASHSCOPE_API_KEY:-}" ]]; then
  export OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
fi

prompt_file="${1:-spark_tasks_gen/examples/3d_scan_calc_prompt.json}"
if [[ $# -gt 0 ]]; then
  shift
fi
model="${MODEL:-${SPARK_TASK_GEN_MODEL:-gpt-5.4}}"

uv run python run_tasks_gen.py \
  --prompt-file "$prompt_file" \
  --model "$model" \
  "$@"
