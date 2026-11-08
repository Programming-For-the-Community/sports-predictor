"""
One assistant turn, shared by both agents (Source/agents/assistant/agent.py
picks the profile). Every client is passed in, so the whole flow runs in
tests without AWS:

  verify caller -> daily quota -> input guardrail -> history + favourites
  -> model/tool loop (Claude Haiku) -> output guardrail -> memory

It yields small event dicts that the runtime streams to the app as SSE:
  {"type": "progress", "tool": "<name>"}
  {"type": "answer", "text": "...", "sources": [...], "questions_left": N, "favorites": {...}}
  {"type": "status", "questions_left": N, "daily_limit": N, "favorites": {...}}
  {"type": "error", "code": "...", "message": "..."}
"""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Iterator

from library.assistant import gen_ai_spans, guardrail, history, user_store
from library.assistant.prompts import Profile
from library.assistant.tool_specs import FAVORITE_TOOL_NAMES, anthropic_tools
from library.parsing import us_eastern_date

logger = logging.getLogger(__name__)

ADMIN_GROUP = "admins"
QUOTA_MESSAGE = "You've used today's questions. They reset at midnight Eastern."
REFUSAL_MESSAGE = "I can't help with that one."
FAILED_MESSAGE = "Something went wrong answering that. Please try again."
SESSION_FULL_MESSAGE = "This chat is full. Start a new chat to keep going."
TOO_MANY_LOOKUPS = "That needs more lookups than I can do in one answer. Try a narrower question."
STATUS_ACTIONS = ("status", "remove_favorite")
_EVENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _eastern_today() -> date:
    return date.fromisoformat(us_eastern_date(datetime.now(timezone.utc)))


@dataclass
class Clients:
    model: object                      # anthropic.AnthropicBedrock
    guardrails: object                 # boto3 bedrock-runtime
    memory: object                     # boto3 bedrock-agentcore
    table: object                      # boto3 DynamoDB Table (assistant)
    invoke_tool: Callable[[str, dict], dict]
    sports: Callable[[], list[str]]
    guardrail_id: str
    guardrail_version: str
    memory_id: str
    grounding: bool = False
    # Quota days run on US Eastern dates, like the app's game dates.
    today: Callable[[], date] = field(default=_eastern_today)
    tracer: object = gen_ai_spans.NULL_TRACER


@dataclass
class _Turn:
    """One question's working state: the caller, the tools run so far and
    their JSON results, and the turn's span."""
    sub: str
    span: object
    sources: list[str] = field(default_factory=list)
    tool_outputs: list[str] = field(default_factory=list)


def token_claims(authorization: str | None) -> dict:
    """The JWT payload. The runtime's authorizer has already verified the
    signature, issuer, audience and -- for the admin agent -- the group."""
    if not authorization:
        return {}
    token = authorization.split(" ", 1)[-1]
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}


def _error(code: str, message: str) -> dict:
    return {"type": "error", "code": code, "message": message}


def _context_note(profile: Profile, favorites: dict, sports: list[str], sport, event_id) -> str:
    """The app's hints about where the user is, kept only when well-formed."""
    notes = []
    if favorites and profile.keeps_favorites:
        notes.append("The user's saved favourites: " + "; ".join(f"{s}: {', '.join(t)}" for s, t in favorites.items()) + ".")
    if sport in sports:
        good_id = isinstance(event_id, str) and _EVENT_ID.match(event_id)
        notes.append(f"The user is on the {sport} page" + (f", viewing game {event_id}." if good_id else "."))
    return " ".join(notes)


def _favorite_tool(clients: Clients, sub: str, name: str, args: dict) -> dict:
    try:
        if name == "save_favorite":
            teams = user_store.save_favorite(clients.table, sub, args.get("sport", ""), args.get("team", ""), clients.sports())
        else:
            teams = user_store.clear_favorites(clients.table, sub, args.get("sport"))
    except user_store.FavoriteError as error:
        return {"ok": False, "error": str(error)}
    return {"ok": True, "result": {"favorites": teams}}


def _run_tool(profile: Profile, clients: Clients, sub: str, call) -> dict:
    if call.name not in profile.tool_names:
        return {"ok": False, "error": "That tool isn't available."}
    if call.name in FAVORITE_TOOL_NAMES:
        return _favorite_tool(clients, sub, call.name, call.input or {})
    return clients.invoke_tool(call.name, call.input or {})


def _status(profile: Profile, clients: Clients, sub: str, payload: dict) -> dict:
    """The app's no-model calls: questions left today and saved favourites,
    after removing one when asked. Costs no question."""
    favorites = None
    if profile.keeps_favorites:
        if payload.get("action") == "remove_favorite":
            favorites = user_store.remove_favorite(clients.table, sub, str(payload.get("sport", "")), str(payload.get("team", "")))
        else:
            favorites = user_store.get_favorites(clients.table, sub)
    limit = profile.settings.daily_limit
    left = user_store.questions_left(clients.table, sub, profile.name, limit, clients.today())
    return {"type": "status", "questions_left": left, "daily_limit": limit, "favorites": favorites}


def _precheck(profile: Profile, claims: dict, question: str | None, session_id: str | None) -> dict | None:
    """The error event that stops this request before any work, if one does."""
    if not claims.get("sub") or not session_id:
        return _error("unauthorized", "Please sign in again.")
    if profile.admin_only and ADMIN_GROUP not in (claims.get("cognito:groups") or []):
        return _error("forbidden", "This mode is for admins.")
    if question is None:
        return None
    if not question:
        return _error("empty", "Ask a question first.")
    if len(question) > profile.settings.max_message_chars:
        return _error("too_long", f"Please keep questions under {profile.settings.max_message_chars} characters.")
    return None


def _answer_text(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text").strip()


def _run_calls(profile: Profile, clients: Clients, turn: _Turn, calls: list):
    """Runs one round of tool calls. Yields progress events; returns the
    tool_result blocks for the next request."""
    results = []
    for call in calls:
        yield {"type": "progress", "tool": call.name}
        span = gen_ai_spans.start_tool(clients.tracer, turn.span, call)
        outcome = _run_tool(profile, clients, turn.sub, call)
        turn.sources.append(call.name)
        body = json.dumps(outcome.get("result") if outcome.get("ok") else {"error": outcome.get("error")})
        gen_ai_spans.end_tool(span, body, bool(outcome.get("ok")))
        turn.tool_outputs.append(body)
        results.append({"type": "tool_result", "tool_use_id": call.id, "content": body, "is_error": not outcome.get("ok")})
    return results


def _call_model(clients: Clients, turn: _Turn, request: dict):
    span = gen_ai_spans.start_chat(clients.tracer, turn.span, request["model"], request["tools"])
    try:
        response = clients.model.messages.create(**request)
    except Exception:
        span.set_attribute("error.type", "model_error")
        span.end()
        raise
    gen_ai_spans.end_chat(span, response)
    return response


def _model_loop(profile: Profile, clients: Clients, turn: _Turn, messages: list[dict]):
    """Yields progress events; returns the answer text. Tool names and their
    JSON results are recorded on the turn."""
    settings = profile.settings
    request = {
        "model": settings.model_id, "max_tokens": settings.max_tokens,
        "system": [{"type": "text", "text": profile.system, "cache_control": {"type": "ephemeral"}}],
        "tools": anthropic_tools(profile.tool_names, clients.sports()),
        "messages": messages,
    }
    # Effort is left out for models without it (ASSISTANT_EFFORT empty).
    if settings.effort:
        request["output_config"] = {"effort": settings.effort}
    for _ in range(settings.max_tool_calls + 1):
        response = _call_model(clients, turn, request)
        if response.stop_reason == "refusal":
            return REFUSAL_MESSAGE
        calls = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not calls:
            return _answer_text(response)
        if len(turn.sources) + len(calls) > settings.max_tool_calls:
            return TOO_MANY_LOOKUPS
        # Thinking and tool-use blocks go back exactly as they came.
        messages.append({"role": "assistant", "content": response.content})
        results = yield from _run_calls(profile, clients, turn, calls)
        messages.append({"role": "user", "content": results})
    return None


def _guard(clients: Clients, source: str, text: str, grounding: list[str] | None = None, query: str | None = None) -> tuple[bool, str]:
    return guardrail.apply(clients.guardrails, clients.guardrail_id, clients.guardrail_version, source, text, grounding, query)


def _admit(profile: Profile, clients: Clients, sub: str, session_id: str, question: str) -> tuple[dict | None, int, list[dict]]:
    """Session size, daily quota and the input guardrail, in that order.
    Returns (event that ends the turn or None, questions left, past messages)."""
    past = history.load(clients.memory, clients.memory_id, sub, session_id)
    if len(past) >= 2 * profile.settings.max_session_turns:
        return _error("session_full", SESSION_FULL_MESSAGE), 0, past
    allowed, questions_left = user_store.take_question(clients.table, sub, profile.name, profile.settings.daily_limit, clients.today())
    if not allowed:
        return _error("quota", QUOTA_MESSAGE), 0, past
    blocked, text = _guard(clients, "INPUT", question)
    if blocked:
        return {"type": "answer", "text": text, "sources": [], "questions_left": questions_left, "blocked": True}, questions_left, past
    return None, questions_left, past


def _favorites_of(profile: Profile, clients: Clients, sub: str) -> dict | None:
    return user_store.get_favorites(clients.table, sub) if profile.keeps_favorites else None


def handle(profile: Profile, clients: Clients, payload: dict, authorization: str | None, session_id: str | None) -> Iterator[dict]:
    payload = payload or {}
    claims = token_claims(authorization)
    if payload.get("action") in STATUS_ACTIONS:
        yield _precheck(profile, claims, None, session_id) or _status(profile, clients, claims["sub"], payload)
        return
    question = str(payload.get("message") or "").strip()
    stop = _precheck(profile, claims, question, session_id)
    if stop:
        yield stop
        return
    sub = claims["sub"]
    stop, questions_left, past = _admit(profile, clients, sub, session_id, question)
    if stop:
        yield stop
        return

    # Today's date with every question: models only know roughly when their
    # training ended, so "this week" or "last month" needs it.
    hints = _context_note(profile, _favorites_of(profile, clients, sub) or {}, clients.sports(), payload.get("sport"), payload.get("event_id"))
    note = f"Today is {clients.today().isoformat()} (US Eastern). {hints}".strip()
    user_text = f"{question}\n\n<context>{note}</context>"
    messages = past[-2 * profile.settings.history_turns:] + [{"role": "user", "content": user_text}]
    turn = _Turn(sub, gen_ai_spans.start_turn(clients.tracer, f"assistant_{profile.name}", session_id, question))
    try:
        answer = yield from _model_loop(profile, clients, turn, messages)
    except Exception:
        logger.exception("Model loop failed")
        gen_ai_spans.end_turn(turn.span, None)
        yield _error("failed", FAILED_MESSAGE)
        return

    grounding = turn.tool_outputs if (clients.grounding and turn.tool_outputs) else None
    blocked, answer = _guard(clients, "OUTPUT", answer or FAILED_MESSAGE, grounding, question)
    gen_ai_spans.end_turn(turn.span, answer)
    if not blocked:
        history.save(clients.memory, clients.memory_id, sub, session_id, question, answer)
    yield {
        "type": "answer", "text": answer, "sources": sorted(set(turn.sources)), "questions_left": questions_left,
        "blocked": blocked, "favorites": _favorites_of(profile, clients, sub),
    }
