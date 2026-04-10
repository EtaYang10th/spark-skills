"""Build high-signal evidence blocks for skill generation."""

from __future__ import annotations

import ast
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from spark_skills_gen.context import AttemptRecord
from spark_skills_gen.judge import JudgmentResult
from spark_skills_gen.token_budgets import TokenBudgets, truncate_head, truncate_tail

log = logging.getLogger(__name__)

_TASK_PATTERN_SOFT_CAP = 16000
_EXECUTION_CHAIN_SOFT_CAP = 24000
_VERIFICATION_SOFT_CAP = 4000
_LESSONS_SOFT_CAP = 8000
_ENVIRONMENT_SOFT_CAP = 8000
_RAW_SUPPORT_SOFT_CAP = 4000
_BULLET_RE = re.compile(r"^\s*[-*]\s+")
_FAILED_RE = re.compile(r"^\s*(?:✗|FAILED\b)\s*(.+)$", re.IGNORECASE)
_PASSED_RE = re.compile(r"^\s*(?:✓|PASSED\b)\s*(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class SkillEvidence:
    task_name: str
    task_pattern: str
    success_execution_chain: str
    success_verification_signals: str
    lessons_from_all_attempts: str
    environment_affordances: str
    raw_support_tail: str


def build_skill_evidence(
    *,
    task_name: str,
    instruction: str,
    judgment: JudgmentResult,
    attempts: list[AttemptRecord],
    exploration_memo: str,
    memo_history: list[str],
    environment_info: str,
    trial_dir: Path | None,
    budgets: TokenBudgets | None = None,
) -> SkillEvidence:
    """Assemble the six evidence blocks used for skill generation."""
    b = budgets or TokenBudgets()
    evidence = SkillEvidence(
        task_name=task_name,
        task_pattern=_build_task_pattern(task_name, instruction, b),
        success_execution_chain=_build_success_execution_chain(
            task_name, judgment, trial_dir, b
        ),
        success_verification_signals=_build_success_verification_signals(
            task_name, judgment, b
        ),
        lessons_from_all_attempts=_build_lessons_from_all_attempts(
            task_name, attempts, exploration_memo, memo_history, b
        ),
        environment_affordances=_build_environment_affordances(
            task_name, environment_info, b
        ),
        raw_support_tail=_build_raw_support_tail(task_name, judgment, b),
    )
    _log_evidence_lengths(evidence)
    return evidence


def _build_task_pattern(task_name: str, instruction: str, budgets: TokenBudgets) -> str:
    text = instruction.strip() or "(no task instruction available)"
    text = re.sub(r"\n{3,}", "\n\n", text)
    return truncate_head(
        text,
        min(_TASK_PATTERN_SOFT_CAP, budgets.input_chars.instruction),
        f"skill_evidence/task_pattern[{task_name}]",
    )


def _build_success_execution_chain(
    task_name: str,
    judgment: JudgmentResult,
    trial_dir: Path | None,
    budgets: TokenBudgets,
) -> str:
    notes = _extract_agent_notes_from_trajectory(trial_dir)
    commands = _extract_structured_commands_from_trajectory(trial_dir)
    if not commands:
        commands = _extract_commands_from_text(judgment.agent_commands)
    commands = _select_key_commands(commands)

    parts: list[str] = []
    if notes:
        parts.append("### Strategy Notes")
        parts.extend(f"- {note}" for note in notes[:12])
    if commands:
        parts.append("### Key Actions")
        parts.extend(f"- {command}" for command in commands[:30])
    if not parts:
        fallback = judgment.agent_stdout_full.strip() or judgment.agent_stdout.strip()
        if fallback:
            parts.append("### Fallback Summary")
            parts.append(f"- {truncate_tail(fallback, 400, f'skill_evidence/fallback[{task_name}]')}")
        else:
            parts.append("- No structured success trajectory was captured.")

    text = "\n".join(parts)
    limit = min(
        _EXECUTION_CHAIN_SOFT_CAP,
        budgets.input_chars.agent_commands + budgets.input_chars.agent_stdout_for_skill // 2,
    )
    return truncate_head(
        text,
        limit,
        f"skill_evidence/success_execution_chain[{task_name}]",
    )


def _build_success_verification_signals(
    task_name: str,
    judgment: JudgmentResult,
    budgets: TokenBudgets,
) -> str:
    lines = [
        f"- Final reward: {judgment.reward:.4f}",
        f"- Tests passed: {judgment.n_passed}/{judgment.n_tests}",
    ]
    if judgment.has_exception:
        lines.append(
            f"- Warning: reward reached pass threshold but run reported {judgment.exception_type or 'an exception'}."
        )

    passed_checks = _extract_checks(judgment.test_summary, passed=True)
    if passed_checks:
        lines.append("- Passed checks:")
        lines.extend(f"  - {item}" for item in passed_checks[:8])

    if judgment.result_json_raw.get("trial_name"):
        lines.append(
            f"- Trial artifact recorded as `{judgment.result_json_raw['trial_name']}`."
        )

    text = "\n".join(lines)
    return truncate_head(
        text,
        min(_VERIFICATION_SOFT_CAP, budgets.input_chars.test_summary),
        f"skill_evidence/success_verification_signals[{task_name}]",
    )


def _build_lessons_from_all_attempts(
    task_name: str,
    attempts: list[AttemptRecord],
    exploration_memo: str,
    memo_history: list[str],
    budgets: TokenBudgets,
) -> str:
    failed_attempts = [attempt for attempt in attempts if attempt.status != "PASS"]
    if not failed_attempts:
        text = (
            "### Attempt Timeline\n"
            "- Succeeded on the first attempt; no failed attempts were recorded.\n\n"
            "### Repeated Failure Patterns\n"
            "- None observed.\n\n"
            "### Confirmed Cautions\n"
            "- No pre-success failure patterns were recorded.\n\n"
            "### Breakthrough\n"
            "- The first attempt already satisfied the verifier. Use the successful execution chain above as the reference path."
        )
        return truncate_head(
            text,
            min(_LESSONS_SOFT_CAP, budgets.input_chars.exploration_memo),
            f"skill_evidence/lessons[{task_name}]",
        )

    timeline = [f"- #{idx + 1}: {_summarize_attempt(attempt)}" for idx, attempt in enumerate(failed_attempts)]
    repeated = _build_repeated_failure_patterns(failed_attempts)
    cautions = _build_confirmed_cautions(memo_history + ([exploration_memo] if exploration_memo else []))

    breakthrough_lines = [
        "- The eventual successful run removed the prior failure signature and satisfied the verifier.",
        "- Prefer the successful execution chain above over earlier speculative or repeated failed routes.",
    ]

    sections = [
        "### Attempt Timeline",
        *(timeline or ["- No failed attempts were captured."]),
        "",
        "### Repeated Failure Patterns",
        *(repeated or ["- No repeated failure signature was strong enough to summarize."]),
        "",
        "### Confirmed Cautions",
        *(cautions or ["- Keep the final verifier contract in mind; earlier attempts repeatedly violated it."]),
        "",
        "### Breakthrough",
        *breakthrough_lines,
    ]
    text = "\n".join(sections)
    return truncate_head(
        text,
        min(_LESSONS_SOFT_CAP, budgets.input_chars.exploration_memo),
        f"skill_evidence/lessons[{task_name}]",
    )


def _build_environment_affordances(
    task_name: str,
    environment_info: str,
    budgets: TokenBudgets,
) -> str:
    if not environment_info.strip():
        return "(no additional environment context)"

    lines: list[str] = []
    dockerfile = _extract_dockerfile_block(environment_info)
    if dockerfile:
        base_image = _extract_base_image(dockerfile)
        if base_image:
            lines.append(f"- Base image/runtime: {base_image}")

        python_packages = _extract_python_packages(dockerfile)
        if python_packages:
            lines.append(
                "- Installed Python packages: "
                + ", ".join(python_packages[:10])
                + (" ..." if len(python_packages) > 10 else "")
            )

        sdk_installs = _extract_sdk_installs(dockerfile)
        if sdk_installs:
            lines.append("- SDK installs: " + ", ".join(sdk_installs))

        apt_packages = _extract_apt_packages(dockerfile)
        if apt_packages:
            lines.append(
                "- System tools: "
                + ", ".join(apt_packages[:10])
                + (" ..." if len(apt_packages) > 10 else "")
            )

    skill_dirs = _extract_skill_dirs(environment_info)
    if skill_dirs:
        lines.append("- Task-local skill directories: " + ", ".join(skill_dirs))

    if not lines:
        lines.append(environment_info.strip())

    text = "\n".join(lines)
    return truncate_head(
        text,
        min(_ENVIRONMENT_SOFT_CAP, budgets.input_chars.environment_info),
        f"skill_evidence/environment[{task_name}]",
    )


def _build_raw_support_tail(
    task_name: str,
    judgment: JudgmentResult,
    budgets: TokenBudgets,
) -> str:
    raw = judgment.agent_stdout_full.strip()
    if raw and raw.lower() not in {"done.", "done"}:
        return truncate_tail(
            raw,
            min(_RAW_SUPPORT_SOFT_CAP, budgets.input_chars.agent_stdout_for_skill // 3),
            f"skill_evidence/raw_support[{task_name}]",
        )
    return "(structured evidence above was sufficient; no extra raw tail included)"


def _extract_agent_notes_from_trajectory(trial_dir: Path | None) -> list[str]:
    path = _trajectory_path(trial_dir)
    if path is None or not path.is_file():
        return []

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.debug("Failed to parse agent trajectory %s: %s", path, exc)
        return []

    notes: list[str] = []
    seen: set[str] = set()
    for step in data.get("steps", []):
        if step.get("source") != "agent":
            continue
        message = _normalize_whitespace(step.get("message", ""))
        if not _is_useful_agent_note(message):
            continue
        if message not in seen:
            seen.add(message)
            notes.append(_truncate_inline(message, 240))
    return notes


def _extract_structured_commands_from_trajectory(trial_dir: Path | None) -> list[str]:
    path = _trajectory_path(trial_dir)
    if path is None or not path.is_file():
        return []

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.debug("Failed to parse agent trajectory %s: %s", path, exc)
        return []

    commands: list[str] = []
    seen: set[str] = set()
    for step in data.get("steps", []):
        if step.get("source") != "agent":
            continue
        for call in step.get("tool_calls") or []:
            arguments = call.get("arguments") or {}
            cmd = arguments.get("cmd")
            if not isinstance(cmd, str) or not cmd.strip():
                continue
            summary = _summarize_command(cmd)
            if summary not in seen:
                seen.add(summary)
                commands.append(summary)
    return commands


def _extract_commands_from_text(agent_commands: str) -> list[str]:
    if not agent_commands:
        return []
    commands: list[str] = []
    for line in agent_commands.splitlines():
        text = line.strip()
        if not text or text == "[Agent Output]":
            continue
        commands.append(_summarize_command(text))
    return commands


def _select_key_commands(commands: list[str]) -> list[str]:
    scored = [(_command_score(command), idx, command) for idx, command in enumerate(commands)]
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = sorted(scored[:12], key=lambda item: item[1])
    result: list[str] = []
    seen: set[str] = set()
    for _, _, command in selected:
        if command not in seen and not _is_low_signal_command(command):
            seen.add(command)
            result.append(command)
    if result:
        return result
    return commands[:8]


def _summarize_command(cmd: str) -> str:
    text = cmd.strip()
    if "\n" in text:
        summary = _summarize_multiline_command(text)
    else:
        summary = _normalize_whitespace(text)

    category = _infer_command_category(text, summary)
    summary = _truncate_inline(summary, 180)
    return f"{category}: {summary}" if category else summary


def _summarize_multiline_command(cmd: str) -> str:
    compact = _normalize_whitespace(cmd)
    lowered = compact.lower()

    if "load_workbook" in compact and "save(" in compact:
        return "use openpyxl to write workbook formulas or values and save the file"
    if "load_workbook" in compact:
        return "inspect workbook structure and target cells with openpyxl"
    if "recalc.py" in compact:
        return "recalculate workbook formulas and scan for spreadsheet errors"
    if "pddlreader" in lowered and "oneshotplanner" in lowered and "write_plan" in lowered:
        return "parse PDDL problems, solve them with pyperplan, and write plan files"
    if "pddlreader" in lowered and "oneshotplanner" in lowered:
        return "parse PDDL problems and validate planner output"
    if "requests.get" in compact and ("crossref" in lowered or "openalex" in lowered):
        return "query bibliographic APIs to verify citations"
    if "ground_truth.json" in compact or "test_outputs.py" in compact:
        return "inspect verifier expectations and reference data"
    if "json.dump" in compact and ".json" in compact:
        return "write the final JSON output in the required schema"
    first_line = next((line.strip() for line in cmd.splitlines() if line.strip()), "")
    if not first_line:
        return compact
    return first_line + " ..."


def _infer_command_category(raw: str, summary: str) -> str:
    text = f"{raw}\n{summary}".lower()
    if any(token in text for token in ("pytest", "mvn test", "mvn clean compile", "recalc.py", "validate", "verify", "check_")):
        return "Verify"
    if any(token in text for token in ("apply_patch", "write the final json", "save(", "json.dump", "write_plan", "fix_", "update_", "edit workbook")):
        return "Implement"
    if any(token in text for token in ("skill.md", "test_outputs.py", "ground_truth.json", "load_workbook", "inspect", "read ", "sed -n", "rg ", "find ", "ls -la")):
        return "Inspect"
    if any(token in text for token in ("pip install", "apt-get install", "sdk install", "python3 -v", "python -v")):
        return "Prepare"
    return "Action"


def _command_score(command: str) -> int:
    text = command.lower()
    score = 0
    for token, weight in (
        ("verify:", 6),
        ("implement:", 5),
        ("inspect:", 4),
        ("recalc.py", 5),
        ("test_outputs.py", 5),
        ("ground_truth.json", 5),
        ("crossref", 5),
        ("openpyxl", 4),
        ("pddl", 4),
        ("answer.json", 4),
        ("fraud_report.json", 4),
        ("skill.md", 3),
    ):
        if token in text:
            score += weight
    return score


def _is_low_signal_command(command: str) -> bool:
    text = command.lower()
    low_signal_prefixes = (
        "inspect: ls -la",
        "inspect: pwd",
        "prepare: python3 -v",
        "prepare: python -v",
        "action: sleep ",
        "action: echo ",
    )
    return text.startswith(low_signal_prefixes)


def _build_repeated_failure_patterns(attempts: list[AttemptRecord]) -> list[str]:
    patterns = Counter()
    for attempt in attempts:
        key = _normalize_failure_signature(attempt.test_summary, attempt.status)
        if key:
            patterns[key] += 1
    return [f"- {pattern} (seen {count} times)" for pattern, count in patterns.items() if count > 1]


def _build_confirmed_cautions(memos: list[str]) -> list[str]:
    caution_lines: list[str] = []
    seen: set[str] = set()
    for memo in memos:
        sections = _parse_memo_sections(memo)
        for section_name in ("Verified Facts", "Current Error Pattern", "Next Strategy"):
            for line in sections.get(section_name, []):
                text = _clean_bullet(line)
                if not text:
                    continue
                if not _looks_like_caution(text):
                    continue
                if text not in seen:
                    seen.add(text)
                    caution_lines.append(f"- {_truncate_inline(text, 220)}")
    return caution_lines[:8]


def _summarize_attempt(attempt: AttemptRecord) -> str:
    failure = _first_failed_check(attempt.test_summary)
    action = _first_command_hint(attempt.agent_commands)
    pieces: list[str] = []
    if action:
        pieces.append(f"tried {action}")
    if failure:
        pieces.append(f"resulted in {failure}")
    else:
        pieces.append(f"ended with status {attempt.status}")
    return "; ".join(pieces)


def _first_command_hint(agent_commands: str) -> str:
    commands = _extract_commands_from_text(agent_commands)
    return commands[0] if commands else ""


def _first_failed_check(test_summary: str) -> str:
    failed = _extract_checks(test_summary, passed=False)
    return failed[0] if failed else ""


def _normalize_failure_signature(test_summary: str, status: str) -> str:
    failure = _first_failed_check(test_summary)
    if failure:
        signature = failure.split(" - ", 1)[0]
        signature = re.split(r":\s+", signature, maxsplit=1)[0]
    else:
        signature = status
    signature = _normalize_whitespace(signature).lower()
    return signature


def _extract_checks(test_summary: str, *, passed: bool) -> list[str]:
    checks: list[str] = []
    for raw_line in test_summary.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("PASSED (") or line.startswith("FAILED ("):
            continue
        if passed and line.startswith("✓ "):
            checks.append(_truncate_inline(line[2:].strip(), 180))
            continue
        if (not passed) and line.startswith("✗ "):
            checks.append(_truncate_inline(line[2:].strip(), 180))
            continue
        matcher = _PASSED_RE if passed else _FAILED_RE
        match = matcher.match(line)
        if match:
            checks.append(_truncate_inline(match.group(1), 180))
    return checks


def _parse_memo_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("### "):
            current = line[4:].strip()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def _looks_like_caution(text: str) -> bool:
    lowered = text.lower()
    keywords = (
        "do not",
        "does not",
        "did not",
        "failed",
        "wrong",
        "incorrect",
        "must",
        "need to",
        "required",
        "exact",
        "recalculate",
        "validate",
        "null",
        "never",
        "not ",
    )
    return any(keyword in lowered for keyword in keywords)


def _extract_dockerfile_block(environment_info: str) -> str:
    marker = "### Dockerfile:\n"
    if marker not in environment_info:
        return ""
    tail = environment_info.split(marker, 1)[1]
    if "\n### Available skill directories:" in tail:
        tail = tail.split("\n### Available skill directories:", 1)[0]
    return tail.strip()


def _extract_base_image(dockerfile: str) -> str:
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if stripped.startswith("FROM "):
            return stripped[5:].strip()
    return ""


def _extract_python_packages(dockerfile: str) -> list[str]:
    packages = re.findall(r"\b([A-Za-z0-9_.+-]+==[A-Za-z0-9_.+-]+)\b", dockerfile)
    seen: set[str] = set()
    result: list[str] = []
    for package in packages:
        if package not in seen:
            seen.add(package)
            result.append(package)
    return result


def _extract_sdk_installs(dockerfile: str) -> list[str]:
    installs = re.findall(r"\bsdk install ([^\s\\&]+)", dockerfile)
    seen: set[str] = set()
    result: list[str] = []
    for install in installs:
        if install not in seen:
            seen.add(install)
            result.append(install)
    return result


def _extract_apt_packages(dockerfile: str) -> list[str]:
    packages: list[str] = []
    lines = dockerfile.splitlines()
    capturing = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "apt-get install" in line:
            capturing = True
            install_part = line.split("apt-get install", 1)[1]
            packages.extend(_tokenize_install_line(install_part))
        elif capturing:
            packages.extend(_tokenize_install_line(line))
        if capturing and not line.endswith("\\"):
            capturing = False
    seen: set[str] = set()
    result: list[str] = []
    for package in packages:
        if package not in seen:
            seen.add(package)
            result.append(package)
    return result


def _extract_skill_dirs(environment_info: str) -> list[str]:
    prefix = "### Available skill directories:"
    for line in environment_info.splitlines():
        if line.startswith(prefix):
            raw = line.split(":", 1)[1].strip()
            try:
                parsed = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                return []
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
    return []


def _tokenize_install_line(line: str) -> list[str]:
    cleaned = (
        line.replace("\\", " ")
        .replace("&&", " ")
        .replace("||", " ")
        .replace("RUN", " ")
    )
    tokens = re.split(r"\s+", cleaned)
    blacklist = {
        "",
        "-y",
        "--no-cache-dir",
        "apt-get",
        "install",
        "pip",
        "pip3",
        "update",
        "rm",
        "-rf",
        "/var/lib/apt/lists/*",
    }
    return [token for token in tokens if token not in blacklist and not token.startswith("$")]


def _trajectory_path(trial_dir: Path | None) -> Path | None:
    if trial_dir is None:
        return None
    return trial_dir / "agent" / "trajectory.json"


def _is_useful_agent_note(message: str) -> bool:
    if not message or len(message) < 20:
        return False
    lowered = message.lower()
    if lowered in {"done.", "done"}:
        return False
    if lowered.startswith("executed "):
        return False
    return True


def _clean_bullet(text: str) -> str:
    line = text.strip()
    line = _BULLET_RE.sub("", line)
    return _normalize_whitespace(line)


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _truncate_inline(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _log_evidence_lengths(evidence: SkillEvidence) -> None:
    log.info(
        "Skill evidence [%s] chars — task=%d exec=%d verify=%d lessons=%d env=%d raw=%d",
        evidence.task_name,
        len(evidence.task_pattern),
        len(evidence.success_execution_chain),
        len(evidence.success_verification_signals),
        len(evidence.lessons_from_all_attempts),
        len(evidence.environment_affordances),
        len(evidence.raw_support_tail),
    )
