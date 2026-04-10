"""FastAPI + WebSocket dashboard for monitoring SPARK pipeline execution."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

log = logging.getLogger(__name__)

app = FastAPI(title="SPARK Dashboard")

_state: dict[str, Any] = {
    "tasks": {},
    "events": [],
    "config": {},
}
_connections: list[WebSocket] = []
_pipeline_ref: Any = None  # Will hold a reference to SparkPipeline instance


def get_dashboard_html() -> str:
    html_path = Path(__file__).parent / "index.html"
    return html_path.read_text()


@app.get("/", response_class=HTMLResponse)
async def index():
    return get_dashboard_html()


@app.get("/api/state")
async def get_state():
    return _state


@app.get("/api/events")
async def get_events(since: float = 0):
    return [e for e in _state["events"] if e.get("ts", 0) > since]


@app.post("/api/cancel/{task_name}")
async def cancel_task(task_name: str):
    """Request cancellation of a running task."""
    if _pipeline_ref is None:
        return {"ok": False, "error": "Pipeline not available"}
    task = _state["tasks"].get(task_name)
    if not task or task["status"] != "running":
        return {"ok": False, "error": f"Task '{task_name}' is not running"}
    _pipeline_ref.cancel_task(task_name)
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _connections.append(ws)
    try:
        await ws.send_json({"type": "init", "state": _state})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _connections.remove(ws)


async def broadcast(event: dict[str, Any]) -> None:
    _state["events"].append(event)
    task_name = event.get("task")

    if event["type"] == "task_start":
        _state["tasks"][task_name] = {
            "name": task_name,
            "status": "running",
            "attempts": [],
            "memos": [],
            "llm_calls": [],
            "current_attempt": 0,
            "max_retries": event.get("max_retries", 3),
            "exploration_memo": "",
            "skill_content": "",
        }
    elif event["type"] == "attempt_start":
        if task_name in _state["tasks"]:
            _state["tasks"][task_name]["current_attempt"] = event.get("attempt", 0)
    elif event["type"] == "attempt_done":
        if task_name in _state["tasks"]:
            _state["tasks"][task_name]["attempts"].append({
                "attempt": event.get("attempt", 0),
                "status": event.get("status", ""),
                "reward": event.get("reward", 0),
                "error": event.get("error", ""),
                "agent_commands": event.get("agent_commands", ""),
                "test_summary": event.get("test_summary", ""),
                "n_passed": event.get("n_passed", 0),
                "n_tests": event.get("n_tests", 0),
            })
    elif event["type"] == "reflect_done":
        if task_name in _state["tasks"]:
            memo = event.get("memo", "")
            _state["tasks"][task_name]["exploration_memo"] = memo
            _state["tasks"][task_name]["memos"].append({
                "attempt": event.get("attempt", 0),
                "memo": memo,
            })
            llm_call = event.get("llm_call")
            if llm_call:
                _state["tasks"][task_name]["llm_calls"].append({
                    **llm_call,
                    "attempt": event.get("attempt", 0),
                })
    elif event["type"] == "task_cancelled":
        if task_name in _state["tasks"]:
            _state["tasks"][task_name]["status"] = "skipped"
    elif event["type"] == "task_done":
        if task_name in _state["tasks"]:
            if _state["tasks"][task_name]["status"] == "skipped":
                pass  # Keep skipped status, don't overwrite
            else:
                _state["tasks"][task_name]["status"] = "pass" if event.get("success") else "fail"
            _state["tasks"][task_name]["final_reward"] = event.get("final_reward", 0)
    elif event["type"] == "skill_generated":
        if task_name in _state["tasks"]:
            _state["tasks"][task_name]["skill_content"] = event.get("skill_content", "")
            llm_call = event.get("llm_call")
            if llm_call:
                _state["tasks"][task_name]["llm_calls"].append({
                    **llm_call,
                    "attempt": -1,
                })
    elif event["type"] == "pipeline_done":
        _state["pipeline_done"] = True
    elif event["type"] == "pdi_update":
        if task_name in _state["tasks"]:
            task_state = _state["tasks"][task_name]
            if "pdi_history" not in task_state:
                task_state["pdi_history"] = []
            task_state["pdi_history"].append({
                "attempt": event.get("attempt", 0),
                "step": event.get("step", 0),
                "proxy_exec": event.get("proxy_exec", 0),
                "proxy_plan": event.get("proxy_plan", 0),
                "proxy_oss": event.get("proxy_oss", 0),
                "raw_pdi": event.get("raw_pdi", 0),
                "weight": event.get("weight", 0),
                "weighted_pdi": event.get("weighted_pdi", 0),
                "triggered": event.get("triggered", False),
                "level": event.get("level"),
            })

    dead: list[WebSocket] = []
    for ws in _connections:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _connections.remove(ws)


def create_event_listener(loop: asyncio.AbstractEventLoop):
    """Return a sync callback that schedules broadcast on the given event loop."""
    def listener(event: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(broadcast(event), loop)
    return listener


def set_config(config: dict[str, Any]) -> None:
    _state["config"] = config


def set_pipeline(pipeline: Any) -> None:
    """Store a reference to the SparkPipeline so the cancel API can reach it."""
    global _pipeline_ref
    _pipeline_ref = pipeline
