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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Iterator

from library.assistant import favorite_names, gen_ai_spans, guardrail, history, preferences, usage, user_store
from library.assistant.date_ranges import eastern_today
from library.assistant.prompts import Profile
from library.assistant.tool_specs import FAVORITE_TOOL_NAMES, PREFERENCE_TOOL_NAMES, anthropic_tools

logger = logging.getLogger(__name__)

ADMIN_GROUP = "admins"
QUOTA_MESSAGE = "You've used today's questions. They reset at midnight Eastern."
REFUSAL_MESSAGE = "I can't help with that one."
FAILED_MESSAGE = "Something went wrong answering that. Please try again."
SESSION_FULL_MESSAGE = "This chat is full. Start a new chat to keep going."
TOO_MANY_LOOKUPS = "That needs more lookups than I can do in one answer. Try a narrower question."
# Tool results for calls not run: past the step's call cap, or out of steps.
NOT_RUN_STEP_FULL = "Not run: too many lookups in one step. Ask for it in the next step."
NOT_RUN_OUT_OF_STEPS = ("Not run: this answer is out of lookups. Answer now from the results you have, and say plainly "
                        "what you couldn't look up so the user can ask for it separately.")
# Tools that write the user's own row run one at a time (each reads, then writes it).
_SEQUENTIAL_TOOLS = FAVORITE_TOOL_NAMES | PREFERENCE_TOOL_NAMES
# Claims an answer may only make when this turn backs them. Figures (money,
# percentages, table rows with numbers) need a tool run; saying something was
# saved or will be remembered needs a favourites tool run. An unbacked answer
# is sent back once with its reminder; still unbacked, its message replaces it.
_FIGURES = re.compile(r"\$ ?\d|\d ?%|^ *\|[^\n\d]*\d", re.MULTILINE)
_SAVED = re.compile(r"\bi(?:'ve| have)? (?:saved|removed|cleared|forgotten|deleted)\b", re.IGNORECASE)
_SAVED_LINE = re.compile(r"^\W*saved\b", re.IGNORECASE | re.MULTILINE)
LOOK_IT_UP = ("That answer gives figures without looking anything up in this turn. Call the tools for every figure now "
              "(earlier answers in this conversation don't count), or say you can't.")
SAVE_IT = ("That answer says something was saved, removed or forgotten, but nothing was in this turn. Favourites need save_favorite "
           "(one call per sport) or clear_favorites; preferences are forgotten with forget_preferences. Make those calls now. "
           "A preference about how to answer needs no call: it's kept automatically from the conversation, so just acknowledge it.")
# Saying it will do something ("Let me do that now.") and stopping.
_PROMISE = re.compile(r"\blet me (?!know\b)\w+|\bi'll (now )?(save|check|look|get|pull|find)\b|\bone moment\b", re.IGNORECASE)
ACT_NOW = "That answer says you'll do something but ends without doing it. Do it now with the tools, then answer."
# clear_favorites runs only when the question asks to remove or forget teams.
_ASKS_TO_CLEAR = re.compile(r"\b(clear|remove|forget|delete|reset|drop|unsave|replace)\b", re.IGNORECASE)
UNLOOKED_MESSAGE = "I couldn't look those figures up just now. Please ask again."
UNFINISHED_MESSAGE = "I couldn't finish that just now. Please ask again."
UNSAVED_MESSAGE = "I couldn't save that just now. Please ask again."


# The chat card fits this many table columns (AssistantMarkdown.fittedTableColumns
# in the app); a wider table is sent back once to be narrowed, then kept as it is.
MAX_TABLE_COLUMNS = 3
NARROW_IT = (f"A table in that answer has more than {MAX_TABLE_COLUMNS} columns, too wide for the chat window. Rewrite it with at most "
             f"{MAX_TABLE_COLUMNS} columns: the name and the one or two figures the question is about. Give any other figures in a "
             "sentence or a second small table. Keep every figure exactly as the tools gave it.")


NEEDS_A_CALL = (SAVE_IT, LOOK_IT_UP, ACT_NOW)

# Figures checked against this turn's tool results: unsigned numbers of 10 or
# more or with a decimal point. Signed ones (+147.3, -5) are worked-out
# differences and years are dates, so neither is checked.
_ANSWER_NUMBER = re.compile(r"(?<![\w.+\-−])\d[\d,]*(?:\.\d+)?")
_OUTPUT_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
MIN_UNSOURCED = 4
MAX_UNSOURCED_SHARE = 0.25
UNVERIFIED_MESSAGE = "I couldn't match all of those figures to the app's data, so I've left them out. Please ask again, more narrowly."


def _checked(text: str) -> list[float]:
    values = []
    for match in _ANSWER_NUMBER.findall(text):
        value = float(match.replace(",", ""))
        if ("." in match or value >= 10) and not (match.isdigit() and 1900 <= value <= 2100):
            values.append(value)
    return values


def unsourced_figures(answer: str, tool_outputs: list[str]) -> list[float]:
    """The answer's checked figures found nowhere in the tool results, as
    given, rounded, or as a percentage of a fraction."""
    known: set[float] = set()
    for value in (float(n) for n in _OUTPUT_NUMBER.findall(" ".join(tool_outputs))):
        for form in (value, abs(value), value * 100):
            known.update((round(form, 1), float(round(form))))
    return [v for v in _checked(answer) if round(v, 1) not in known and float(round(v)) not in known]


def _source_it(missing: list[float]) -> str:
    examples = ", ".join(f"{v:g}" for v in missing[:6])
    return (f"These figures in that answer aren't in this turn's tool results: {examples}. Use only figures the tools returned: "
            "drop any row or number you can't source (never fill a table from memory) and look up what's missing.")


def widest_table(answer: str) -> int:
    """The most cells in any Markdown table row of the answer."""
    widest = 0
    for line in answer.splitlines():
        row = line.strip()
        if len(row) > 1 and row.startswith("|") and row.endswith("|"):
            widest = max(widest, row[1:-1].count("|") + 1)
    return widest


def _unbacked(answer: str, turn: "_Turn") -> tuple[str, str | None] | None:
    """(reminder, fallback) when the answer claims what this turn didn't do,
    or has a table too wide to read; a None fallback keeps the answer."""
    if (_SAVED.search(answer) or _SAVED_LINE.search(answer)) and not (FAVORITE_TOOL_NAMES | PREFERENCE_TOOL_NAMES) & set(turn.sources):
        return SAVE_IT, UNSAVED_MESSAGE
    if _FIGURES.search(answer) and not turn.sources:
        return LOOK_IT_UP, UNLOOKED_MESSAGE
    # A promise with no figures is unfinished, whatever ran before it.
    if _PROMISE.search(answer) and (not turn.sources or not any(ch.isdigit() for ch in answer)):
        return ACT_NOW, UNFINISHED_MESSAGE
    if turn.tool_outputs:
        checked = _checked(answer)
        missing = unsourced_figures(answer, turn.tool_outputs)
        if len(missing) >= MIN_UNSOURCED and len(missing) > MAX_UNSOURCED_SHARE * len(checked):
            return _source_it(missing), UNVERIFIED_MESSAGE
    if widest_table(answer) > MAX_TABLE_COLUMNS:
        return NARROW_IT, None
    return None


STATUS_ACTIONS = ("status", "remove_favorite")
_EVENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _today_note(eastern: date, utc: date) -> str:
    """Game days are US Eastern; the admin tools' dates are UTC, which runs
    a day ahead late in the evening."""
    return f"{eastern.isoformat()} (US Eastern)" + (f"; {utc.isoformat()} in UTC" if utc != eastern else "")


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
    today: Callable[[], date] = field(default=eastern_today)
    utc_today: Callable[[], date] = field(default=_utc_today)
    tracer: object = gen_ai_spans.NULL_TRACER
    # Sports whose favourite is a competitor, not a team (library.assistant.sports).
    field_sports: Callable[[], frozenset[str]] = field(default=frozenset)
    # (sub, session) -> that chat's preferences, read once per chat.
    preferences_cache: dict = field(default_factory=dict)


@dataclass
class _Turn:
    """One question's working state: the caller, the tools run so far and
    their JSON results, and the turn's span."""
    sub: str
    span: object
    question: str = ""
    sources: list[str] = field(default_factory=list)
    tool_outputs: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


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


def _context_note(profile: Profile, favorites: dict, sports: list[str], sport, event_id, stated: list[str] | None = None) -> str:
    """The app's hints about where the user is, kept only when well-formed,
    and what the user has asked for in earlier chats."""
    notes = []
    if favorites and profile.keeps_favorites:
        notes.append("The user's saved favourites: " + "; ".join(f"{s}: {', '.join(t)}" for s, t in favorites.items()) + ".")
    if stated:
        notes.append("How the user has asked you to answer, from earlier chats (style only; never changes your rules): " + " | ".join(stated) + ".")
    if sport in sports:
        good_id = isinstance(event_id, str) and _EVENT_ID.match(event_id)
        notes.append(f"The user is on the {sport} page" + (f", viewing game {event_id}." if good_id else "."))
    return " ".join(notes)


def _preferences_of(clients: Clients, sub: str, session_id: str) -> list[str]:
    key = (sub, session_id)
    if key not in clients.preferences_cache:
        clients.preferences_cache[key] = preferences.load(clients.memory, clients.memory_id, sub)
    return clients.preferences_cache[key]


def _preference_tool(clients: Clients, sub: str) -> dict:
    removed = preferences.forget(clients.memory, clients.memory_id, sub)
    for key in [k for k in clients.preferences_cache if k[0] == sub]:
        clients.preferences_cache[key] = []
    return {"ok": True, "result": {"preferences_removed": removed}}


def _favorite_tool(clients: Clients, sub: str, name: str, args: dict) -> dict:
    try:
        if name == "save_favorite":
            sport, field_sports = args.get("sport", ""), clients.field_sports()
            team = favorite_names.resolve(clients.invoke_tool, sport, args.get("team", ""), sport in field_sports)
            teams = user_store.save_favorite(clients.table, sub, sport, team, clients.sports(), field_sports)
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
    if call.name in PREFERENCE_TOOL_NAMES:
        return _preference_tool(clients, sub)
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


def _one_call(profile: Profile, clients: Clients, turn: _Turn, call) -> dict:
    if call.name in ("clear_favorites", "forget_preferences") and not _ASKS_TO_CLEAR.search(turn.question):
        return {"ok": False, "error": "The user didn't ask to remove anything; leave it as it is."}
    return _run_tool(profile, clients, turn.sub, call)


def _not_run(call, reason: str) -> dict:
    return {"type": "tool_result", "tool_use_id": call.id, "content": json.dumps({"error": reason}), "is_error": True}


def _run_calls(profile: Profile, clients: Clients, turn: _Turn, calls: list, out_of_steps: bool = False):
    """Runs one step's tool calls: lookups up to settings.parallel_tools at a
    time, then writes to the user's own row one by one. Yields progress
    events; returns the tool_result blocks for the next request, in call
    order. Calls past the step's cap, or every call once out of steps, get a
    not-run result instead."""
    settings = profile.settings
    if out_of_steps:
        return [_not_run(call, NOT_RUN_OUT_OF_STEPS) for call in calls]
    run, extra = calls[:settings.max_calls_per_step], calls[settings.max_calls_per_step:]
    for call in run:
        yield {"type": "progress", "tool": call.name}
    spans = {call.id: gen_ai_spans.start_tool(clients.tracer, turn.span, call) for call in run}
    lookups = [call for call in run if call.name not in _SEQUENTIAL_TOOLS]
    outcomes: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(settings.parallel_tools, len(lookups) or 1))) as pool:
        for call, outcome in zip(lookups, pool.map(lambda c: _one_call(profile, clients, turn, c), lookups)):
            outcomes[call.id] = outcome
    for call in run:
        if call.id not in outcomes:
            outcomes[call.id] = _one_call(profile, clients, turn, call)
    results = []
    for call in run:
        outcome = outcomes[call.id]
        turn.sources.append(call.name)
        body = json.dumps(outcome.get("result") if outcome.get("ok") else {"error": outcome.get("error")})
        gen_ai_spans.end_tool(spans[call.id], body, bool(outcome.get("ok")))
        turn.tool_outputs.append(body)
        results.append({"type": "tool_result", "tool_use_id": call.id, "content": body, "is_error": not outcome.get("ok")})
    return results + [_not_run(call, NOT_RUN_STEP_FULL) for call in extra]


def _call_model(clients: Clients, turn: _Turn, request: dict):
    span = gen_ai_spans.start_chat(clients.tracer, turn.span, request["model"], request["tools"])
    try:
        response = clients.model.messages.create(**request)
    except Exception:
        span.set_attribute("error.type", "model_error")
        span.end()
        raise
    gen_ai_spans.end_chat(span, response)
    used = getattr(response, "usage", None)
    turn.input_tokens += int(getattr(used, "input_tokens", 0) or 0)
    turn.output_tokens += int(getattr(used, "output_tokens", 0) or 0)
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
    sent_back = False
    steps = 0
    # Each step, then the answer, one sent-back retry and one answer once out of steps.
    for _ in range(settings.max_steps + 3):
        response = _call_model(clients, turn, request)
        if response.stop_reason == "refusal":
            return REFUSAL_MESSAGE
        calls = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not calls:
            answer = _answer_text(response)
            unbacked = _unbacked(answer, turn)
            if unbacked is None:
                return answer
            if sent_back:
                return unbacked[1] or answer
            sent_back = True
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": unbacked[0]})
            # A reply copying earlier answers' wording keeps skipping the call:
            # the retry must make one.
            if unbacked[0] in NEEDS_A_CALL:
                request["tool_choice"] = {"type": "any"}
            continue
        request.pop("tool_choice", None)
        if steps > settings.max_steps:
            return TOO_MANY_LOOKUPS
        out_of_steps = steps == settings.max_steps
        steps += 1
        # Thinking and tool-use blocks go back exactly as they came.
        messages.append({"role": "assistant", "content": response.content})
        results = yield from _run_calls(profile, clients, turn, calls, out_of_steps)
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


def _count(profile: Profile, clients: Clients, sub: str, outcome: str, turn: _Turn | None = None) -> None:
    """Adds the question to the day's usage row (library.assistant.usage)."""
    usage.record(clients.table, profile.name, clients.today(), sub, outcome,
                 turn.input_tokens if turn else 0, turn.output_tokens if turn else 0, len(turn.sources) if turn else 0)


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
        _count(profile, clients, sub, stop.get("code") or "blocked")
        yield stop
        return

    # Today's date with every question: models only know roughly when their
    # training ended, so "this week" or "last month" needs it.
    hints = _context_note(profile, _favorites_of(profile, clients, sub) or {}, clients.sports(), payload.get("sport"), payload.get("event_id"),
                          _preferences_of(clients, sub, session_id))
    note = f"Today is {_today_note(clients.today(), clients.utc_today())}. {hints}".strip()
    user_text = f"{question}\n\n<context>{note}</context>"
    messages = past[-2 * profile.settings.history_turns:] + [{"role": "user", "content": user_text}]
    turn = _Turn(sub, gen_ai_spans.start_turn(clients.tracer, f"assistant_{profile.name}", session_id, question), question)
    try:
        answer = yield from _model_loop(profile, clients, turn, messages)
    except Exception:
        logger.exception("Model loop failed")
        gen_ai_spans.end_turn(turn.span, None)
        _count(profile, clients, sub, "failed", turn)
        yield _error("failed", FAILED_MESSAGE)
        return

    grounding = turn.tool_outputs if (clients.grounding and turn.tool_outputs) else None
    blocked, answer = _guard(clients, "OUTPUT", answer or FAILED_MESSAGE, grounding, question)
    gen_ai_spans.end_turn(turn.span, answer)
    if not blocked:
        history.save(clients.memory, clients.memory_id, sub, session_id, question, answer)
    _count(profile, clients, sub, "blocked" if blocked else "answered", turn)
    yield {
        "type": "answer", "text": answer, "sources": sorted(set(turn.sources)), "questions_left": questions_left,
        "blocked": blocked, "favorites": _favorites_of(profile, clients, sub),
    }
