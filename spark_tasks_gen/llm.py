"""LLM helpers for structured task generation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

import requests

log = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMConfig:
    """Model settings shared across task-generation calls."""

    model: str
    api_base: str | None = None
    api_key: str | None = None
    max_tokens: int = 32000
    temperature: float = 0.2

    @property
    def resolved_model(self) -> str:
        return self.model


def _parse_sse_response(text: str) -> tuple[str, str, dict]:
    """Parse an SSE streaming response into (content, finish_reason, usage).

    Handles the 'data: {...}' line format returned by proxies that ignore stream=false.
    """
    content_parts: list[str] = []
    finish_reason = "unknown"
    usage: dict = {}

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choice = chunk.get("choices", [{}])[0]
        delta = choice.get("delta", {})
        if "content" in delta and delta["content"]:
            content_parts.append(delta["content"])
        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]
        if chunk.get("usage"):
            usage = chunk["usage"]

    return "".join(content_parts), finish_reason, usage


def call_text_llm(system_msg: str, user_msg: str, config: LLMConfig) -> str:
    """Issue a single completion request and return plain text.

    Handles both normal JSON responses and SSE streaming responses
    (for proxies that ignore stream=false).
    """
    api_base = (config.api_base or "https://api.openai.com/v1").rstrip("/")
    url = f"{api_base}/chat/completions"

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "stream": False,
    }

    log.info("LLM request: model=%s url=%s", config.model, url)
    resp = requests.post(url, headers=headers, json=payload, timeout=300)
    resp.raise_for_status()

    # Try normal JSON response first; fall back to SSE parsing
    try:
        data = resp.json()
        finish_reason = data["choices"][0].get("finish_reason", "unknown")
        content = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {})
    except (json.JSONDecodeError, KeyError):
        log.info("Response is SSE stream, parsing chunks...")
        content, finish_reason, usage = _parse_sse_response(resp.text)

    log.info(
        "LLM call complete: model=%s prompt_tokens=%s completion_tokens=%s finish_reason=%s",
        config.model,
        usage.get("prompt_tokens", "?"),
        usage.get("completion_tokens", "?"),
        finish_reason,
    )
    if finish_reason == "length":
        log.warning("LLM response was truncated (finish_reason=length). Consider increasing max_tokens.")
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
    """Extract and parse the first JSON object from a model response.

    Strategy: strip markdown fences, then try json.loads on progressively
    smaller substrings ending at each '}' from the end.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _strip_fence(stripped)

    start = stripped.find("{")
    if start == -1:
        raise ValueError("No JSON object start found")

    candidate = stripped[start:]

    # Try parsing the whole thing first
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Try finding the matching closing brace by attempting
    # json.loads on substrings ending at each '}' from the end
    last = len(candidate)
    while last > 0:
        pos = candidate.rfind("}", 0, last)
        if pos == -1:
            break
        try:
            parsed = json.loads(candidate[: pos + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            last = pos
            continue

    raise ValueError("JSON object was not closed")


def _strip_fence(text: str) -> str:
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return text
