"""LiteLLM helpers for structured task generation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

import litellm


log = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMConfig:
    """Model settings shared across task-generation calls."""

    model: str
    api_base: str | None = None
    api_key: str | None = None
    max_tokens: int = 16000
    temperature: float = 0.2

    @property
    def resolved_model(self) -> str:
        if self.api_base and "/" not in self.model:
            return f"openai/{self.model}"
        return self.model


def call_text_llm(system_msg: str, user_msg: str, config: LLMConfig) -> str:
    """Issue a single completion request and return plain text."""
    kwargs: dict[str, Any] = {
        "model": config.resolved_model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
    }
    if config.api_base:
        kwargs["api_base"] = config.api_base
    if config.api_key:
        kwargs["api_key"] = config.api_key

    response = litellm.completion(**kwargs)
    content = response.choices[0].message.content or ""
    prompt_tokens = getattr(getattr(response, "usage", None), "prompt_tokens", "?")
    completion_tokens = getattr(getattr(response, "usage", None), "completion_tokens", "?")
    log.info(
        "LLM call complete: model=%s prompt_tokens=%s completion_tokens=%s",
        config.model,
        prompt_tokens,
        completion_tokens,
    )
    return content.strip()


def call_json_llm(system_msg: str, user_msg: str, config: LLMConfig) -> dict[str, Any]:
    """Call the LLM and parse the first JSON object in its response."""
    response_text = call_text_llm(system_msg, user_msg, config)
    try:
        return extract_json_object(response_text)
    except ValueError as exc:
        preview = response_text[:1000]
        raise ValueError(f"Failed to parse JSON from LLM response: {exc}\nResponse preview:\n{preview}") from exc


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract and parse the first JSON object from a model response."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _strip_fence(stripped)

    start = stripped.find("{")
    if start == -1:
        raise ValueError("No JSON object start found")

    depth = 0
    in_string = False
    escape = False
    for idx, char in enumerate(stripped[start:], start=start):
        if escape:
            escape = False
            continue
        if char == "\\" and in_string:
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                payload = stripped[start : idx + 1]
                parsed = json.loads(payload)
                if not isinstance(parsed, dict):
                    raise ValueError("Top-level JSON value is not an object")
                return parsed

    raise ValueError("JSON object was not closed")


def _strip_fence(text: str) -> str:
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return text
