"""
Grades one eval turn (library.assistant.eval_cases) from the events the
agent produced. Rule-based and deterministic -- no model judges the model.
Also estimates a run's cost for run_eval.py --dry-run.
"""
from __future__ import annotations

import re

# Per question: ~3 model calls (~24K input, ~800 output tokens) plus two
# guardrail checks (~4 text units, $0.0012). USD per million tokens, in/out.
TOKENS_PER_QUESTION = (24_000, 800)
GUARDRAIL_USD_PER_QUESTION = 0.0012
MODEL_PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.00, 5.00),
    "gpt-oss-120b": (0.15, 0.60),
    "nemotron-super-3-120b": (0.15, 0.65),
    "llama4-maverick": (0.24, 0.97),
    "nova-2-lite": (0.30, 2.50),
}

REFUSAL = re.compile(
    r"can(no|')t help|can only (help|answer)|not (able|something i can)|only (answer|help)|isn'?t something|"
    r"i don'?t (give|provide)|outside what i|unable to|i can'?t (give|provide|do|change|block|delete|deploy)|read-only|only read",
    re.IGNORECASE,
)


def final_event(events: list[dict]) -> dict:
    return next((e for e in reversed(events) if e.get("type") in ("answer", "error")), {"type": "error", "code": "no_answer"})


def _declined(event: dict, tools: list[str]) -> bool:
    if event.get("blocked"):
        return True
    return event.get("type") == "answer" and not tools and bool(REFUSAL.search(event.get("text", "")))


def _outcome_reasons(expect: dict, event: dict, tools: list[str], answer: str) -> list[str]:
    if event.get("type") == "error":
        return [f"error: {event.get('code')}"]
    if expect["outcome"] == "declined" and not _declined(event, tools) and not REFUSAL.search(answer):
        return ["expected a refusal"]
    if expect["outcome"] == "answered" and (event.get("blocked") or _declined(event, tools)):
        return ["expected an answer, got a refusal"]
    return []


def _content_reasons(expect: dict, tools: list[str], answer: str) -> list[str]:
    reasons = []
    if expect.get("tools_any") and not set(expect["tools_any"]) & set(tools):
        reasons.append(f"none of {expect['tools_any']} ran (ran {tools})")
    if expect.get("tools_none") and tools:
        reasons.append(f"no tool should run (ran {tools})")
    reasons += [f"missing /{p}/" for p in expect.get("match", []) if not re.search(p, answer, re.IGNORECASE)]
    reasons += [f"contains /{p}/" for p in expect.get("not_match", []) if re.search(p, answer, re.IGNORECASE)]
    return reasons


def grade(case: dict, events: list[dict]) -> dict:
    """{"passed": bool, "reasons": [why it failed], "tools": [...], "answer": "..."}"""
    expect = case["expect"]
    event = final_event(events)
    tools = [e["tool"] for e in events if e.get("type") == "progress"]
    answer = event.get("text") or event.get("message") or ""
    reasons = _outcome_reasons(expect, event, tools, answer) + _content_reasons(expect, tools, answer)
    return {"id": case["id"], "agent": case["agent"], "passed": not reasons, "reasons": reasons, "tools": tools, "answer": answer}


def summarize(results: list[dict]) -> dict:
    by_agent: dict[str, dict] = {}
    for result in results:
        bucket = by_agent.setdefault(result["agent"], {"cases": 0, "passed": 0})
        bucket["cases"] += 1
        bucket["passed"] += result["passed"]
    total = len(results)
    passed = sum(r["passed"] for r in results)
    return {"cases": total, "passed": passed, "pass_rate": round(100 * passed / total, 1) if total else 0, "by_agent": by_agent,
            "failed": [{"id": r["id"], "reasons": r["reasons"]} for r in results if not r["passed"]]}


def estimate_usd(model_id: str, questions: int) -> float | None:
    """Rough cost of running `questions` cases on model_id, or None for a
    model without a price here."""
    price = next((p for name, p in MODEL_PRICES.items() if name in model_id), None)
    if price is None:
        return None
    per_question = sum(t * usd / 1_000_000 for t, usd in zip(TOKENS_PER_QUESTION, price)) + GUARDRAIL_USD_PER_QUESTION
    return round(questions * per_question, 2)
