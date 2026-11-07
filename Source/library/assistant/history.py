"""
Conversation history in AgentCore Memory: one event per turn holding the
question and the answer, under actorId = the caller's Cognito sub, so a
session id from another user finds nothing. Events expire on the memory's
own event_expiry_duration (Terraform). Replayed as plain text -- earlier
turns' thinking blocks are never sent back.
"""
from __future__ import annotations

from datetime import datetime, timezone

MAX_EVENTS = 100


def load(memory, memory_id: str, actor: str, session: str) -> list[dict]:
    response = memory.list_events(memoryId=memory_id, actorId=actor, sessionId=session, includePayloads=True, maxResults=MAX_EVENTS)
    messages: list[dict] = []
    for event in sorted(response.get("events", []), key=lambda e: e["eventTimestamp"]):
        for item in event.get("payload", []):
            turn = item.get("conversational")
            if turn:
                messages.append({"role": "user" if turn["role"] == "USER" else "assistant", "content": turn["content"]["text"]})
    return messages


def save(memory, memory_id: str, actor: str, session: str, question: str, answer: str) -> None:
    memory.create_event(
        memoryId=memory_id, actorId=actor, sessionId=session, eventTimestamp=datetime.now(timezone.utc),
        payload=[
            {"conversational": {"content": {"text": question}, "role": "USER"}},
            {"conversational": {"content": {"text": answer}, "role": "ASSISTANT"}},
        ],
    )
