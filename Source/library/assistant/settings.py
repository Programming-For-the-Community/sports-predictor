"""
The assistant's tunable limits, read from environment variables so they can
change without a code release. Terraform sets every one of them explicitly
(locals.assistant_settings in Terraform/agentcore-assistant.tf); the
defaults here are for tests and local runs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


@dataclass(frozen=True)
class AgentSettings:
    model_id: str
    effort: str
    max_tokens: int
    max_tool_calls: int
    max_message_chars: int
    history_turns: int
    max_session_turns: int
    daily_limit: int

    @classmethod
    def from_env(cls, default_daily_limit: int = 50) -> AgentSettings:
        return cls(
            model_id=os.environ.get("ASSISTANT_MODEL_ID", "anthropic.claude-haiku-5-5"),
            effort=os.environ.get("ASSISTANT_EFFORT", "low"),
            max_tokens=_int("ASSISTANT_MAX_TOKENS", 4096),
            max_tool_calls=_int("ASSISTANT_MAX_TOOL_CALLS", 6),
            max_message_chars=_int("ASSISTANT_MAX_MESSAGE_CHARS", 1000),
            history_turns=_int("ASSISTANT_HISTORY_TURNS", 10),
            max_session_turns=_int("ASSISTANT_MAX_SESSION_TURNS", 30),
            daily_limit=_int("ASSISTANT_DAILY_LIMIT", default_daily_limit),
        )


@dataclass(frozen=True)
class ToolSettings:
    max_rows: int
    max_result_chars: int
    max_trend_points: int
    hourly_trend_days: int
    repeat_min_blocks: int
    repeat_min_days: int

    @classmethod
    def from_env(cls) -> ToolSettings:
        return cls(
            max_rows=_int("ASSISTANT_MAX_ROWS", 25),
            max_result_chars=_int("ASSISTANT_MAX_RESULT_CHARS", 12_000),
            max_trend_points=_int("ASSISTANT_MAX_TREND_POINTS", 400),
            hourly_trend_days=_int("ASSISTANT_HOURLY_TREND_DAYS", 7),
            repeat_min_blocks=_int("ASSISTANT_REPEAT_MIN_BLOCKS", 100),
            repeat_min_days=_int("ASSISTANT_REPEAT_MIN_DAYS", 3),
        )


@dataclass(frozen=True)
class ProducerSettings:
    cost_restated_days: int
    cost_backfill_months: int
    cost_month_repull_day: int
    traffic_lookback_hours: int

    @classmethod
    def from_env(cls) -> ProducerSettings:
        return cls(
            cost_restated_days=_int("COST_RESTATED_DAYS", 3),
            cost_backfill_months=_int("COST_BACKFILL_MONTHS", 12),
            cost_month_repull_day=_int("COST_MONTH_REPULL_DAY", 5),
            traffic_lookback_hours=_int("TRAFFIC_LOOKBACK_HOURS", 2),
        )
