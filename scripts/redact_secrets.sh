#!/usr/bin/env bash
# =============================================================================
# redact_secrets.sh
# 批量把 trajectory.jsonl / summary.json / *.md 等文件中的真实 API key 替换成占位符。
#
# 用法:
#   bash scripts/redact_secrets.sh                   # 默认仅扫描预设目录
#   REPO_ROOT=/path/to/repo bash scripts/redact_secrets.sh
#
# SECRETS 通过解析同仓库的 .env 文件自动构建（文件在 .gitignore 中，不会进入提交）。
# 扫描目录扩展到所有已知结果目录；任何新结果目录放进 SCAN_SUBDIRS 即可。
# =============================================================================

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

# 需要扫描的相对目录（存在才扫描）
SCAN_SUBDIRS=(
    "spark_skills_gen/skills_gen_result/all_model"
    "spark_skills_gen/skills_gen_result/all_model_pdi"
    "spark_skills_gen/skills_gen_result/all_model_observe_only"
    "spark_skills_gen/skills_eval_result"
    "spark_skills_gen/skills_eval_result_pdi"
    "spark_skills_gen/skills_eval_result_casestudy"
    "spark_skills_gen/skills_eval_result_casestudy_pdi"
    "spark_skills_gen/skills_eval_result_variants"
    "spark_skills_gen/skills_eval_result_variants_clean_env"
    "spark_skills_gen/skills_eval_result_openai_compatible"
    "spark_skills_gen/skills_gen_result_openai_compatible"
    "spark_skills_gen/case_study_trajectories"
    "analysis_output"
)

# 只对这些扩展名做就地替换，避免误伤二进制。
EXTENSIONS=(jsonl json md txt csv tsv log yml yaml toml)

# 最短 key 长度阈值，低于此长度的 value 忽略（避免把 "1"/"true" 这种值替换掉）。
MIN_KEY_LEN=16

# ─── 从 .env 自动提取密钥 ─────────────────────────────────────────────────────
declare -a SECRETS=()

if [ -f "$ENV_FILE" ]; then
    while IFS= read -r line; do
        # 跳过空行与注释
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        # 仅处理 KEY=VALUE 形式
        [[ "$line" != *=* ]] && continue
        name="${line%%=*}"
        value="${line#*=}"
        # 去除首尾空白与成对引号
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
        # 跳过 base_url 之类的非 key
        [[ "$name" == *BASE_URL* || "$name" == *ENDPOINT* || "$name" == *HOST* ]] && continue
        [[ -z "$value" ]] && continue
        # 长度过短跳过
        [[ ${#value} -lt $MIN_KEY_LEN ]] && continue
        # 生成占位符：按变量名命名
        placeholder="<${name}_REDACTED>"
        SECRETS+=("${value}|${placeholder}")
    done < "$ENV_FILE"
else
    echo "WARNING: .env not found at $ENV_FILE — falling back to empty secret list." >&2
fi

echo "Loaded ${#SECRETS[@]} secrets from $ENV_FILE"
echo "Repo root: $REPO_ROOT"
echo ""

# ─── 收集存在的目标目录 ───────────────────────────────────────────────────────
TARGETS=()
for sub in "${SCAN_SUBDIRS[@]}"; do
    full="$REPO_ROOT/$sub"
    [ -d "$full" ] && TARGETS+=("$full")
done

if [ ${#TARGETS[@]} -eq 0 ]; then
    echo "No target directories found; nothing to do."
    exit 0
fi

echo "Target directories:"
for t in "${TARGETS[@]}"; do echo "  $t"; done
echo ""

# ─── 构造 find 的扩展名过滤参数 ───────────────────────────────────────────────
build_find_args() {
    local first=1
    for ext in "${EXTENSIONS[@]}"; do
        if [ $first -eq 1 ]; then
            echo -n " \\( -name *.$ext"
            first=0
        else
            echo -n " -o -name *.$ext"
        fi
    done
    echo -n " \\)"
}
FIND_EXT_ARGS=$(build_find_args)

# ─── 执行替换 ─────────────────────────────────────────────────────────────────
total_replacements=0
for entry in "${SECRETS[@]}"; do
    secret="${entry%%|*}"
    placeholder="${entry##*|}"
    # 跳过已经是占位符的
    [[ "$secret" == \<*\> ]] && continue

    for dir in "${TARGETS[@]}"; do
        # 用 grep -rl 找命中文件（按扩展名过滤）
        files=$(eval "find '$dir' -type f $FIND_EXT_ARGS" 2>/dev/null | \
                xargs -I{} grep -l -F -- "$secret" "{}" 2>/dev/null || true)
        if [ -n "$files" ]; then
            count=$(echo "$files" | grep -c . || true)
            echo "Replacing (placeholder=$placeholder) in $count files under $(basename "$dir")..."
            while IFS= read -r f; do
                [ -z "$f" ] && continue
                # 用 perl 做字面量替换，避免 sed 对元字符敏感
                perl -pi -e "s/\Q$secret\E/$placeholder/g" "$f"
                total_replacements=$((total_replacements+1))
            done <<< "$files"
        fi
    done
done

echo ""
echo "Done. Modified $total_replacements file-instances."
echo ""

# ─── 残留检测 ─────────────────────────────────────────────────────────────────
echo "Scanning for residual key-shaped strings..."
leaked=0
for dir in "${TARGETS[@]}"; do
    hits=$(grep -rE \
        '(sk-ant-api[0-9][a-zA-Z0-9_-]{30,}|sk-proj-[a-zA-Z0-9_-]{20,}|sk-[a-f0-9]{32,}|sk-reapi-[a-zA-Z0-9_-]{10,}|xai-[a-zA-Z0-9_-]{30,}|AIzaSy[a-zA-Z0-9_-]{30,}|eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_.-]{20,})' \
        "$dir" 2>/dev/null | grep -v REDACTED | head -5 || true)
    if [ -n "$hits" ]; then
        echo "WARN residual in $dir:"
        echo "$hits" | head -5
        leaked=1
    fi
done

if [ "$leaked" -eq 0 ]; then
    echo "OK, no residual secrets detected."
else
    echo ""
    echo "Some key-shaped strings remain. Add them to .env or extend the pattern above." >&2
    exit 1
fi
