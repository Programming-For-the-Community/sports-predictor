from library.assistant.eval_cases import build_cases
from library.assistant.eval_grading import estimate_usd, grade, summarize

SPORTS = ["f1", "nba", "ncaafb", "ncaambb", "nfl", "nhl", "pga"]


def _answer(text, tools=(), blocked=False):
    return [*({"type": "progress", "tool": t} for t in tools), {"type": "answer", "text": text, "sources": list(tools), "blocked": blocked}]


def test_the_set_covers_every_registry_sport_both_agents_and_the_red_team():
    cases = build_cases(SPORTS)
    ids = [c["id"] for c in cases]

    assert len(ids) == len(set(ids))
    assert {c["agent"] for c in cases} == {"public", "admin"}
    assert {f"{sport}-accuracy" for sport in SPORTS} <= set(ids)
    assert sum(c["expect"]["outcome"] == "declined" for c in cases) >= 25
    assert all(r"arn:aws" in c["expect"]["not_match"] for c in cases)
    assert "wnba-picks" in {c["id"] for c in build_cases(["wnba"])}


def _case(case_id):
    return next(c for c in build_cases(SPORTS) if c["id"] == case_id)


def test_an_answer_passes_when_the_right_tool_ran_and_the_text_matches():
    result = grade(_case("nfl-accuracy"), _answer("The NFL win model has picked 64% of games right.", ["get_model_performance"]))
    assert result["passed"] is True


def test_an_answer_fails_without_the_tool_or_the_expected_numbers():
    result = grade(_case("nfl-accuracy"), _answer("It has done well.", []))
    assert result["passed"] is False
    assert any("none of" in r for r in result["reasons"])
    assert any("missing" in r for r in result["reasons"])


def test_a_refusal_passes_a_declined_case_and_betting_advice_fails_it():
    refused = grade(_case("bet-should"), _answer("I can't help with betting decisions, but here's what the model predicted."))
    blocked = grade(_case("bet-should"), _answer("I can only help with this app's predictions.", blocked=True))
    advised = grade(_case("bet-should"), _answer("You should bet DET, it's a lock."))

    assert refused["passed"] is True
    assert blocked["passed"] is True
    assert advised["passed"] is False


def test_leaks_and_errors_fail():
    leaked = grade(_case("cost-month"), _answer("$41.20 so far, account 123456789012.", ["get_cost_summary"]))
    errored = grade(_case("cost-month"), [{"type": "error", "code": "failed", "message": "Something went wrong"}])

    assert any("contains" in r for r in leaked["reasons"])
    assert errored["reasons"][0] == "error: failed"


def test_a_tool_run_fails_a_case_that_should_refuse_without_looking():
    result = grade(_case("off-poem"), _answer("I can't help with that.", ["list_events"]))
    assert result["passed"] is False


def test_the_summary_counts_by_agent_and_lists_failures():
    summary = summarize([
        {"id": "a", "agent": "public", "passed": True, "reasons": []},
        {"id": "b", "agent": "admin", "passed": False, "reasons": ["x"]},
    ])
    assert summary["pass_rate"] == 50.0
    assert summary["by_agent"] == {"public": {"cases": 1, "passed": 1}, "admin": {"cases": 1, "passed": 0}}
    assert summary["failed"] == [{"id": "b", "reasons": ["x"]}]


def test_the_cost_estimate_follows_the_deployed_model():
    assert estimate_usd("us.anthropic.claude-haiku-5-5", 130) == 0.52
    assert estimate_usd("us.anthropic.claude-haiku-4-5-20251001-v1:0", 130) == 3.80
    assert estimate_usd("us.amazon.nova-2-lite-v1:0", 130) == 1.35
    assert estimate_usd("amazon.nova-lite-v1:0", 130) is None
