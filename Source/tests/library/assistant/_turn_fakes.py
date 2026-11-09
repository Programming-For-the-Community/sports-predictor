"""
Fakes for one assistant turn, shared by test_agent_core.py and
test_gen_ai_spans.py: a scripted model, guardrails, memory, and Clients
wired to them.
"""
import base64
import json
from datetime import date
from types import SimpleNamespace

from library.assistant import agent_core
from library.assistant.prompts import build_profile
from library.assistant.settings import AgentSettings

SPORTS = ["f1", "nba", "ncaafb", "ncaambb", "nfl", "nhl", "pga"]
SETTINGS = AgentSettings(model_id="anthropic.claude-haiku-5-5", effort="low", max_tokens=4096, max_steps=4, max_calls_per_step=10, parallel_tools=4,
                         max_message_chars=1000, history_turns=10, max_session_turns=30, daily_limit=50)
PUBLIC = build_profile("public", SETTINGS, SPORTS)
ADMIN = build_profile("admin", SETTINGS, SPORTS)



def _token(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"Bearer header.{body}.signature"


USER = _token({"sub": "user-1", "cognito:groups": ["viewers"]})
ADMIN_USER = _token({"sub": "admin-1", "cognito:groups": ["admins"]})
SESSION = "s" * 36


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _call(name, args, call_id="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=call_id)


def _response(*blocks, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


class FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


class FakeGuardrails:
    def __init__(self, block_input=False, block_output=False):
        self.block = {"INPUT": block_input, "OUTPUT": block_output}
        self.calls = []

    def apply_guardrail(self, guardrailIdentifier, guardrailVersion, source, content):
        self.calls.append((source, content))
        if self.block[source]:
            return {"action": "GUARDRAIL_INTERVENED", "outputs": [{"text": f"blocked {source.lower()}"}]}
        return {"action": "NONE", "outputs": []}


class FakeMemory:
    def __init__(self, events=None, records=None):
        self.events = list(events or [])
        self.created = []
        self.records = dict(records or {})  # namespace -> [record]
        self.record_reads = 0
        self.deleted = []

    def list_memory_records(self, memoryId, namespace, maxResults, nextToken=None):
        self.record_reads += 1
        return {"memoryRecordSummaries": self.records.get(namespace, [])}

    def batch_delete_memory_records(self, memoryId, records):
        self.deleted += [r["memoryRecordId"] for r in records]
        for namespace in self.records:
            self.records[namespace] = [r for r in self.records[namespace] if r["memoryRecordId"] not in self.deleted]
        return {"successfulRecords": records, "failedRecords": []}

    def list_events(self, memoryId, actorId, sessionId, includePayloads, maxResults):
        return {"events": [e for e in self.events if e["actor"] == actorId and e["session"] == sessionId]}

    def create_event(self, memoryId, actorId, sessionId, eventTimestamp, payload):
        self.created.append({"actor": actorId, "session": sessionId, "payload": payload})


def make_clients(model, table, guardrails=None, memory=None, tools=None, grounding=False):
    """(Clients, the tool calls made) -- every tool returns tools[name], or
    {"value": 1}."""
    invoked = []

    def invoke_tool(name, args):
        invoked.append((name, args))
        return (tools or {}).get(name, {"ok": True, "result": {"value": 1}})

    clients = agent_core.Clients(
        model=model, guardrails=guardrails or FakeGuardrails(), memory=memory or FakeMemory(), table=table,
        invoke_tool=invoke_tool, sports=lambda: SPORTS, guardrail_id="g", guardrail_version="1", memory_id="m", grounding=grounding,
        today=lambda: date(2026, 11, 6), utc_today=lambda: date(2026, 11, 6),
    )
    return clients, invoked


def run(profile, clients, message="Why is DET favoured?", token=USER, **payload):
    return list(agent_core.handle(profile, clients, {"message": message, **payload}, token, SESSION))
