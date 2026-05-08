"""LLM calls: Reflect (rewrite exploration memo) and Skill Generation."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import litellm
import requests

from spark_skills_gen.prompts import (
    REFLECT_SYSTEM,
    REFLECT_USER,
    SKILL_GENERATION_SYSTEM,
    SKILL_GENERATION_USER,
)
from spark_skills_gen.skill_evidence import SkillEvidence
from spark_skills_gen.token_budgets import TokenBudgets, truncate_head, truncate_tail
from spark_skills_gen.trajectory import LLMUsage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMCallRecord:
    """Captures prompts and response for dashboard display."""

    label: str
    system_prompt: str
    user_prompt: str
    response: str
    usage: LLMUsage = LLMUsage()


def reflect(
    task_name: str,
    attempt: int,
    status: str,
    reward: float,
    n_passed: int,
    n_tests: int,
    agent_commands: str,
    test_summary: str,
    exploration_memo: str = "",
    model: str = "qwen3-coder-next",
    api_base: str | None = None,
    api_key: str | None = None,
    budgets: TokenBudgets | None = None,
) -> tuple[str, LLMCallRecord]:
    """Rewrite the exploration memo after a failed attempt."""
    b = budgets or TokenBudgets()
    ic = b.input_chars

    user_msg = REFLECT_USER.format(
        task_name=task_name,
        exploration_memo=truncate_tail(
            exploration_memo or "(first failure — no memo yet)",
            ic.exploration_memo,
            f"reflect/memo[{task_name}]",
        ),
        status=status,
        attempt_number=attempt + 1,
        reward=reward,
        n_passed=n_passed,
        n_tests=n_tests,
        agent_commands=truncate_tail(
            agent_commands or "(no commands captured)",
            ic.agent_commands,
            f"reflect/commands[{task_name}]",
        ),
        test_summary=truncate_tail(
            test_summary or "(no test output)",
            ic.test_summary,
            f"reflect/tests[{task_name}]",
        ),
    )

    response, usage = _call_llm(
        REFLECT_SYSTEM, user_msg, model, api_base, api_key,
        max_tokens=b.output_tokens.reflect,
    )
    record = LLMCallRecord(
        label="reflect",
        system_prompt=REFLECT_SYSTEM,
        user_prompt=user_msg,
        response=response,
        usage=usage,
    )
    return response, record


def generate_skill(
    evidence: SkillEvidence,
    model: str = "qwen3-coder-next",
    api_base: str | None = None,
    api_key: str | None = None,
    budgets: TokenBudgets | None = None,
) -> tuple[str, LLMCallRecord]:
    """Generate a SKILL.md from a successful trajectory + exploration lessons."""
    b = budgets or TokenBudgets()
    ic = b.input_chars

    user_msg = SKILL_GENERATION_USER.format(
        task_name=evidence.task_name,
        task_pattern=truncate_head(
            evidence.task_pattern,
            ic.instruction,
            f"skill_gen/task_pattern[{evidence.task_name}]",
        ),
        success_execution_chain=truncate_head(
            evidence.success_execution_chain or "(no success execution chain available)",
            ic.agent_stdout_for_skill,
            f"skill_gen/success_execution_chain[{evidence.task_name}]",
        ),
        success_verification_signals=truncate_head(
            evidence.success_verification_signals or "(no verification signals available)",
            ic.test_summary,
            f"skill_gen/success_verification[{evidence.task_name}]",
        ),
        lessons_from_all_attempts=truncate_head(
            evidence.lessons_from_all_attempts or "(no pre-success lessons available)",
            ic.exploration_memo,
            f"skill_gen/lessons[{evidence.task_name}]",
        ),
        environment_affordances=truncate_head(
            evidence.environment_affordances or "(no environment affordances recorded)",
            ic.environment_info,
            f"skill_gen/environment[{evidence.task_name}]",
        ),
        raw_support_tail=truncate_tail(
            evidence.raw_support_tail or "(no raw support tail included)",
            max(256, ic.agent_stdout_for_skill // 3),
            f"skill_gen/raw_support[{evidence.task_name}]",
        ),
    )

    response, usage = _call_llm(
        SKILL_GENERATION_SYSTEM, user_msg, model, api_base, api_key,
        max_tokens=b.output_tokens.skill_generation,
    )
    record = LLMCallRecord(
        label="skill_generation",
        system_prompt=SKILL_GENERATION_SYSTEM,
        user_prompt=user_msg,
        response=response,
        usage=usage,
    )
    return response, record


def _strip_markdown_fence(text: str) -> str:
    """Strip wrapping ```markdown ... ``` fences that LLMs sometimes emit.

    This ensures the SKILL.md starts with a clean ``---`` YAML frontmatter
    delimiter, which is required by agents like Codex for skill discovery.
    """
    from spark_skills_gen._utils import strip_markdown_fence
    return strip_markdown_fence(text)


def save_skill_result(
    result_base: Path,
    model_name: str,
    task_name: str,
    skill_content: str | None,
    context_dict: dict,
    success: bool,
) -> Path:
    """Save generated skill and attempt records to result_base/model/task/."""
    result_dir = result_base / model_name / task_name
    result_dir.mkdir(parents=True, exist_ok=True)

    if success and skill_content:
        (result_dir / "SKILL.md").write_text(_strip_markdown_fence(skill_content))

    (result_dir / "attempts.json").write_text(
        json.dumps(context_dict, indent=2, ensure_ascii=False)
    )

    if not success:
        error_lines = ["# Error Report\n"]
        for attempt in context_dict.get("attempts", []):
            error_lines.append(
                f"## Attempt {attempt['attempt'] + 1} — {attempt['status']}\n"
            )
            error_lines.append(
                f"Commands: {attempt.get('agent_commands', '(none)')[:500]}\n"
            )
            error_lines.append("")
        memo = context_dict.get("exploration_memo", "")
        if memo:
            error_lines.append("## Final Exploration Memo\n")
            error_lines.append(memo)
        (result_dir / "error_report.md").write_text("\n".join(error_lines))

    return result_dir


def _call_llm(
    system_msg: str,
    user_msg: str,
    model: str,
    api_base: str | None,
    api_key: str | None,
    max_tokens: int = 2000,
) -> tuple[str, LLMUsage]:
    """Generic LLM call via litellm, with SSE fallback for proxies that
    always return ``text/event-stream`` regardless of the ``stream`` flag."""
    llm_model = model
    if api_base and "/" not in model:
        llm_model = f"openai/{model}"

    kwargs: dict = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    if api_base:
        kwargs["api_base"] = api_base
    if api_key:
        kwargs["api_key"] = api_key

    t0 = time.time()
    try:
        response = litellm.completion(**kwargs)
        latency = time.time() - t0
        content = response.choices[0].message.content or ""
        usage = LLMUsage(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            latency_s=round(latency, 3),
        )
        log.info(
            "LLM call (%s) — tokens in=%s out=%s latency=%.1fs",
            model,
            usage.input_tokens,
            usage.output_tokens,
            usage.latency_s,
        )
        return content.strip(), usage
    except Exception as e:
        latency = time.time() - t0
        err_preview = str(e)[:300]
        # If the error contains SSE chunks, the proxy returned streaming data
        # that litellm couldn't parse.  Fall back to manual SSE parsing.
        if "event-stream" in err_preview or "chat.completion.chunk" in str(e)[:2000]:
            log.warning(
                "LiteLLM SSE parse failed for %s, falling back to manual SSE parsing",
                model,
            )
            try:
                content, usage = _call_llm_sse_fallback(
                    system_msg, user_msg, model, api_base, api_key, max_tokens,
                )
                return content, usage
            except Exception as e2:
                log.error("SSE fallback also failed: %s", str(e2)[:500])
                return "", LLMUsage(latency_s=round(time.time() - t0, 3))
        else:
            log.error("LLM call failed: %s", err_preview)
            return "", LLMUsage(latency_s=round(latency, 3))


def _strip_thinking_tags(text: str) -> str:
    """Remove <think>...</think> reasoning tokens from model output."""
    import re
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _call_llm_sse_fallback(
    system_msg: str,
    user_msg: str,
    model: str,
    api_base: str | None,
    api_key: str | None,
    max_tokens: int,
) -> tuple[str, LLMUsage]:
    """Direct HTTP call with manual SSE chunk reassembly."""
    base = api_base or os.environ.get("OPENAI_BASE_URL", "")
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    url = base.rstrip("/") + "/chat/completions"

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "stream": False,
    }

    t0 = time.time()
    resp = requests.post(url, headers=headers, json=payload, timeout=300)
    resp.raise_for_status()
    latency = time.time() - t0

    ct = resp.headers.get("content-type", "")

    # If the proxy returned proper JSON, parse it directly
    if "application/json" in ct:
        data = resp.json()
        content = data["choices"][0]["message"]["content"] or ""
        usage = LLMUsage(
            input_tokens=data.get("usage", {}).get("prompt_tokens", 0),
            output_tokens=data.get("usage", {}).get("completion_tokens", 0),
            latency_s=round(latency, 3),
        )
        log.info("LLM SSE-fallback (%s) — JSON response, tokens in=%s out=%s",
                 model, usage.input_tokens, usage.output_tokens)
        return content.strip(), usage

    # Otherwise reassemble SSE chunks
    content_parts: list[str] = []
    input_tokens = 0
    output_tokens = 0

    for line in resp.text.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        data_str = line[len("data: "):]
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            if "content" in delta and delta["content"]:
                content_parts.append(delta["content"])
            # Usage info is typically in the last chunk
            if "usage" in chunk:
                input_tokens = chunk["usage"].get("prompt_tokens", input_tokens)
                output_tokens = chunk["usage"].get("completion_tokens", output_tokens)
        except json.JSONDecodeError:
            continue

    content = "".join(content_parts)
    # Strip <think>...</think> reasoning tokens (e.g. from GPT-5.5, DeepSeek)
    content = _strip_thinking_tags(content)
    usage = LLMUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_s=round(latency, 3),
    )
    log.info(
        "LLM SSE-fallback (%s) — reassembled %d chunks, %d chars, tokens in=%s out=%s latency=%.1fs",
        model, len(content_parts), len(content),
        usage.input_tokens, usage.output_tokens, usage.latency_s,
    )
    if not content.strip():
        log.error("SSE fallback produced empty content after reassembly")
    return content.strip(), usage
