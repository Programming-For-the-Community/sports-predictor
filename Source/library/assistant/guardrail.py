"""
Bedrock Guardrails, applied explicitly with ApplyGuardrail to the user's
question (INPUT) and the final answer (OUTPUT), so every intervention shows
in the trace. Contextual grounding, when on, checks the answer against the
turn's tool results.
"""
from __future__ import annotations

MAX_GROUNDING_CHARS = 20_000
FALLBACK_MESSAGE = "I can't help with that one."


def apply(guardrails, guardrail_id: str, version: str, source: str, text: str,
          grounding: list[str] | None = None, query: str | None = None) -> tuple[bool, str]:
    """(intervened, text to use). On an intervention the guardrail's own
    configured message -- or the masked text -- replaces the original."""
    content = [{"text": {"text": text, "qualifiers": ["guard_content"]}}]
    if grounding:
        content += [{"text": {"text": g[:MAX_GROUNDING_CHARS], "qualifiers": ["grounding_source"]}} for g in grounding]
        content.append({"text": {"text": query or "", "qualifiers": ["query"]}})
    response = guardrails.apply_guardrail(guardrailIdentifier=guardrail_id, guardrailVersion=version, source=source, content=content)
    if response.get("action") != "GUARDRAIL_INTERVENED":
        return False, text
    outputs = response.get("outputs") or []
    return True, outputs[0]["text"] if outputs else FALLBACK_MESSAGE
