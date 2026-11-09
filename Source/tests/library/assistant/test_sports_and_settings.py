from library.assistant import sports
from library.assistant.settings import AgentSettings, ProducerSettings, ToolSettings


class FakeRegistry:
    name = "sport-registry"

    def __init__(self, pages):
        self.pages = list(pages)
        self.scans = 0

    def scan(self, **kwargs):
        self.scans += 1
        page = self.pages[self.scans - 1]
        return page


def test_sports_are_read_from_every_registry_page_and_cached():
    sports.clear_cache()
    registry = FakeRegistry([
        {"Items": [{"sport": "nfl"}, {"sport": "nba"}], "LastEvaluatedKey": {"sport_key": "x"}},
        {"Items": [{"sport": "nhl"}, {}]},
    ])

    assert sports.load_sports(registry) == ["nba", "nfl", "nhl"]
    assert sports.load_sports(registry) == ["nba", "nfl", "nhl"]
    assert registry.scans == 2
    sports.clear_cache()


def test_field_sports_come_from_the_registry_event_type():
    sports.clear_cache()
    registry = FakeRegistry([{"Items": [{"sport": "nfl", "event_type": "head_to_head"}, {"sport": "pga", "event_type": "field"},
                                        {"sport": "f1", "event_type": "field"}, {"sport": "nhl"}]}])

    assert sports.load_field_sports(registry) == frozenset({"pga", "f1"})
    assert sports.load_sports(registry) == ["f1", "nfl", "nhl", "pga"]
    assert registry.scans == 1
    sports.clear_cache()


def test_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("ASSISTANT_DAILY_LIMIT", "300")
    monkeypatch.setenv("ASSISTANT_EFFORT", "medium")
    monkeypatch.setenv("ASSISTANT_MAX_ROWS", "10")
    monkeypatch.setenv("TRAFFIC_LOOKBACK_HOURS", "3")

    assert AgentSettings.from_env().daily_limit == 300
    assert AgentSettings.from_env().effort == "medium"
    assert ToolSettings.from_env().max_rows == 10
    assert ProducerSettings.from_env().traffic_lookback_hours == 3


def test_settings_fall_back_to_defaults(monkeypatch):
    for name in ("ASSISTANT_DAILY_LIMIT", "ASSISTANT_MODEL_ID"):
        monkeypatch.delenv(name, raising=False)
    settings = AgentSettings.from_env(default_daily_limit=50)
    assert settings.daily_limit == 50
    assert settings.model_id == "us.anthropic.claude-haiku-5-5"


def test_turn_log_lines_name_the_outcome_and_carry_the_transcript():
    import json

    from library.assistant import turn_log

    answered = json.loads(turn_log.record("public", "u1", "s1", {"message": "Why DET?"},
                                          {"type": "answer", "text": "DET -6.5", "sources": ["list_events"], "questions_left": 49}))
    assert answered["assistant_turn"] is True
    assert answered["outcome"] == "answered"
    assert answered["question"] == "Why DET?"
    assert answered["answer"] == "DET -6.5"
    assert answered["tools"] == ["list_events"]

    assert turn_log.outcome({"type": "answer", "blocked": True}) == "blocked"
    assert turn_log.outcome({"type": "error", "code": "quota"}) == "quota"
    assert turn_log.outcome({"type": "status"}) == "status"
