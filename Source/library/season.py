"""
Whether "today" falls within a sport's season window -- the season-gate
Lambda (aws-lambdas/shared/season-gate/handler.py) calls this from both
orchestrator state machines (Terraform/sfn-ingest-orchestrator.tf,
Terraform/sfn-training-orchestrator.tf) in place of the old, runtime-
mutable `active` flag Terraform kept clobbering on every apply. Season
bounds themselves are static per-sport config (Terraform/dynamodb-sport-
registry.tf's season_start/season_end), so this recomputes year-agnostic
month-day membership on every run instead of ever storing an on/off bit.
"""
from datetime import date, datetime, timezone


def current_month_day(today: date | None = None) -> str:
    """Today's "MM-DD", in UTC unless `today` is given (tests only)."""
    today = today or datetime.now(timezone.utc).date()
    return today.strftime("%m-%d")


def is_in_season(today_month_day: str, season_start: str, season_end: str) -> bool:
    """Whether `today_month_day` ("MM-DD") falls within [season_start,
    season_end], inclusive of both ends. Handles a season that crosses
    the calendar year boundary (season_start > season_end, e.g. NFL's
    "08-01" through "02-28", or NCAAFB's "07-01" through "01-31") as the
    union of [season_start, 12-31] and [01-01, season_end], rather than a
    normal range that would otherwise never match."""
    if season_start <= season_end:
        return season_start <= today_month_day <= season_end
    return today_month_day >= season_start or today_month_day <= season_end
