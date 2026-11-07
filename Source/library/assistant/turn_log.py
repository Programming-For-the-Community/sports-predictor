"""
One JSON log line per finished turn, written by the agent to its runtime log
group (kept 14 days -- Terraform/agentcore-assistant.tf). The assistant
dashboard's Logs Insights widgets read these lines; they are also the
turn transcripts, so they carry the question and answer text.
"""
from __future__ import annotations

import json

TERMINAL = ("answer", "error", "status")


def outcome(event: dict) -> str:
    if event["type"] == "error":
        return event.get("code", "error")
    if event["type"] == "status":
        return "status"
    return "blocked" if event.get("blocked") else "answered"


def record(agent: str, sub: str | None, session_id: str | None, payload: dict, event: dict) -> str:
    return json.dumps({
        "assistant_turn": True,
        "agent": agent,
        "user_sub": sub,
        "session_id": session_id,
        "outcome": outcome(event),
        "tools": event.get("sources", []),
        "questions_left": event.get("questions_left"),
        "question": str(payload.get("message") or "")[:2000],
        "answer": str(event.get("text") or event.get("message") or "")[:4000],
    })
