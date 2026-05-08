#!/usr/bin/env python3
"""
检查 skills_eval_result 下每个模型的 summary.json，
对每个 task 的 trial_dir 做验证，区分：
  - PASS: 任务成功
  - LEGIT_FAIL: LLM能力不足导致的正常失败（测试没全过、超时等）
  - INFRA_FAIL: 意外失败（环境崩溃、Docker失败、trial_dir丢失等）
"""
import json, os, glob
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).resolve().parent.parent
RESULT_DIR = BASE / "spark_skills_gen" / "skills_eval_result"

# 这些 exception 类型视为基础设施/意外失败
INFRA_EXCEPTIONS = {"RuntimeError", "CancelledError", "OSError", "DockerError", "FileNotFoundError"}
# 这些视为 LLM 能力问题（超时 = agent 太慢/卡住）
LLM_EXCEPTIONS = {"AgentTimeoutError", "TimeoutError"}

# 在 result.json / verifier 里搜索这些关键词来判断是否是基础设施问题
INFRA_KEYWORDS = [
    "docker compose command failed",
    "docker daemon",
    "connection refused",
    "no space left on device",
    "permission denied",
    "oom",
    "out of memory",
    "cannot connect",
    "network unreachable",
    "disk quota exceeded",
    "segmentation fault",
    "killed by signal",
    "environment setup failed",
    "image pull",
]


def classify_task(task_info, trial_dir_path):
    """返回 (classification, reason)"""
    status = task_info.get("status", "")
    has_exc = task_info.get("has_exception", False)
    exc_type = task_info.get("exception_type", "")
    exc_msg = task_info.get("exception_message", "")
    reward = task_info.get("reward", 0)

    # 1) 成功
    if status == "PASS" or reward == 1.0:
        return "PASS", "测试全部通过"

    # 2) 有 exception 的情况
    if has_exc:
        # 基础设施异常
        if exc_type in INFRA_EXCEPTIONS:
            return "INFRA_FAIL", f"基础设施异常: {exc_type} - {exc_msg[:120]}"
        # 检查 exception message 里有没有基础设施关键词
        msg_lower = exc_msg.lower()
        for kw in INFRA_KEYWORDS:
            if kw in msg_lower:
                return "INFRA_FAIL", f"基础设施关键词匹配 [{kw}]: {exc_type} - {exc_msg[:120]}"
        # Agent 超时 -> LLM 能力问题
        if exc_type in LLM_EXCEPTIONS:
            return "LEGIT_FAIL", f"Agent超时: {exc_msg[:120]}"
        # 其他未知 exception，尝试从 trial_dir 进一步判断
        # 先标记为 INFRA_FAIL（保守策略）
        return "INFRA_FAIL", f"未知异常: {exc_type} - {exc_msg[:120]}"

    # 3) 没有 exception，trial_dir 存在吗？
    trial_dir = Path(trial_dir_path) if trial_dir_path else None
    if not trial_dir or not trial_dir.exists():
        return "MISSING", f"trial_dir 不存在"

    # 4) 检查 result.json
    result_file = trial_dir / "result.json"
    if result_file.exists():
        try:
            with open(result_file) as f:
                result = json.load(f)
            exc_info = result.get("exception_info")
            if exc_info and exc_info.get("exception_type"):
                et = exc_info["exception_type"]
                em = exc_info.get("exception_message", "")
                em_lower = em.lower()
                # 检查基础设施关键词
                for kw in INFRA_KEYWORDS:
                    if kw in em_lower:
                        return "INFRA_FAIL", f"result.json 基础设施关键词 [{kw}]: {et} - {em[:120]}"
                if et in INFRA_EXCEPTIONS:
                    return "INFRA_FAIL", f"result.json 基础设施异常: {et} - {em[:120]}"
                if et in LLM_EXCEPTIONS:
                    return "LEGIT_FAIL", f"result.json Agent超时: {em[:120]}"
        except Exception:
            pass

    # 5) 检查 verifier/ctrf.json - 如果测试跑了，说明是 LLM 能力问题
    ctrf_file = trial_dir / "verifier" / "ctrf.json"
    if ctrf_file.exists():
        try:
            with open(ctrf_file) as f:
                ctrf = json.load(f)
            summary = ctrf.get("results", {}).get("summary", {})
            total = summary.get("tests", 0)
            passed = summary.get("passed", 0)
            failed = summary.get("failed", 0)
            if total > 0:
                return "LEGIT_FAIL", f"测试跑了但没全过: {passed}/{total} passed, {failed} failed"
        except Exception:
            pass

    # 6) 检查 verifier/reward.txt
    reward_file = trial_dir / "verifier" / "reward.txt"
    if reward_file.exists():
        try:
            r = float(open(reward_file).read().strip())
            if r > 0:
                return "LEGIT_FAIL", f"部分得分 reward={r}"
        except Exception:
            pass

    # 7) 检查 verifier/test-stdout.txt 有没有内容（说明测试至少跑了）
    test_stdout = trial_dir / "verifier" / "test-stdout.txt"
    if test_stdout.exists():
        try:
            content = open(test_stdout).read()
            if len(content.strip()) > 50:  # 有实质内容
                return "LEGIT_FAIL", f"测试有输出但失败 (stdout {len(content)} chars)"
        except Exception:
            pass

    # 8) 检查 agent/trajectory.json 存在 -> agent 至少跑了
    traj_file = trial_dir / "agent" / "trajectory.json"
    if traj_file.exists():
        return "LEGIT_FAIL", "Agent 执行了但测试失败"

    # 9) 啥都没有，可能是基础设施问题
    return "INFRA_FAIL", f"trial_dir 存在但缺少关键文件"


def process_model(model_dir):
    summary_file = model_dir / "summary.json"
    if not summary_file.exists():
        print(f"  [跳过] {model_dir.name}: 没有 summary.json")
        return None

    with open(summary_file) as f:
        data = json.load(f)

    model_name = data.get("model", model_dir.name)
    results = {}

    # 遍历 baseline / with_generated_skills / with_human_skills
    for label_key in ["baseline", "with_generated_skills", "with_human_skills"]:
        section = data.get(label_key)
        if not section or "tasks" not in section:
            continue

        label = section.get("label", label_key)
        stats = {"PASS": 0, "LEGIT_FAIL": 0, "INFRA_FAIL": 0, "MISSING": 0, "details": []}

        for task_name, task_info in section["tasks"].items():
            trial_dir = task_info.get("trial_dir", "")
            cls, reason = classify_task(task_info, trial_dir)
            stats[cls] += 1
            if cls == "INFRA_FAIL":
                stats["details"].append((task_name, cls, reason))

        results[label] = stats

    return model_name, results


def main():
    model_dirs = sorted([p for p in RESULT_DIR.iterdir() if p.is_dir()])
    print(f"找到 {len(model_dirs)} 个模型目录: {[d.name for d in model_dirs]}\n")

    all_results = {}
    for model_dir in model_dirs:
        print(f"处理: {model_dir.name}")
        ret = process_model(model_dir)
        if ret:
            model_name, results = ret
            all_results[model_name] = results

    # 打印汇总
    print("\n" + "=" * 80)
    print("验证结果汇总")
    print("=" * 80)

    for model, labels in all_results.items():
        print(f"\n{'─' * 60}")
        print(f"模型: {model}")
        print(f"{'─' * 60}")
        for label, stats in labels.items():
            total = stats["PASS"] + stats["LEGIT_FAIL"] + stats["INFRA_FAIL"] + stats["MISSING"]
            print(f"\n  [{label}] 共 {total} 个任务")
            print(f"    ✅ PASS (成功):        {stats['PASS']}")
            print(f"    ❌ LEGIT_FAIL (LLM能力不足): {stats['LEGIT_FAIL']}")
            print(f"    ⚠️  INFRA_FAIL (意外失败):   {stats['INFRA_FAIL']}")
            print(f"    📁 MISSING (记录丢失):      {stats['MISSING']}")

            if stats["details"]:
                print(f"\n    意外失败详情:")
                for task_name, cls, reason in stats["details"]:
                    print(f"      - {task_name}: {reason}")

    # 输出 JSON 格式的结果
    output_file = RESULT_DIR / "validation_report.json"
    report = {}
    for model, labels in all_results.items():
        report[model] = {}
        for label, stats in labels.items():
            total = stats["PASS"] + stats["LEGIT_FAIL"] + stats["INFRA_FAIL"] + stats["MISSING"]
            report[model][label] = {
                "total": total,
                "pass": stats["PASS"],
                "legit_fail": stats["LEGIT_FAIL"],
                "infra_fail": stats["INFRA_FAIL"],
                "missing": stats["MISSING"],
                "infra_fail_tasks": [
                    {"task": t, "reason": r} for t, c, r in stats["details"]
                ],
            }

    with open(output_file, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n\n详细报告已保存到: {output_file}")


if __name__ == "__main__":
    main()
