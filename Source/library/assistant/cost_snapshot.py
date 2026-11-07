"""
Daily cost snapshot the admin assistant answers cost questions from
(aws-lambdas/shared/assistant-cost-snapshot). Only spend carrying this
project's tag is ever requested: PROJECT_FILTER goes on every Cost Explorer
call and is not a parameter, so nothing outside the project can enter a
snapshot.

S3 layout under the model-artifacts bucket:
  assistant/admin/cost/daily/<yyyy-mm-dd>.json   one day, every breakdown
  assistant/admin/cost/monthly/<yyyy-mm>.json    rolled up from the days
  assistant/admin/cost/meta.json                 data_from, budget, forecast

Project spend with no Sport or Component value is grouped as MISC so totals
add up.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from library.assistant.settings import ProducerSettings

logger = logging.getLogger(__name__)

PROJECT = "sports-predictor"
PREFIX = "assistant/admin/cost"
MISC = "Misc"
METRIC = "UnblendedCost"

# The project tag on resources, or on the IAM principal that made the call
# (Bedrock model spend is attributed to the calling role).
PROJECT_FILTER = {
    "Or": [
        {"Tags": {"Key": "Project", "Values": [PROJECT], "MatchOptions": ["EQUALS"]}},
        {"Tags": {"Key": "iamPrincipal/Project", "Values": [PROJECT], "MatchOptions": ["EQUALS"]}},
    ]
}

_SERVICE_BY_SPORT = [{"Type": "DIMENSION", "Key": "SERVICE"}, {"Type": "TAG", "Key": "Sport"}]
_COMPONENT_BY_SPORT = [{"Type": "TAG", "Key": "Component"}, {"Type": "TAG", "Key": "Sport"}]


def daily_key(day: date) -> str:
    return f"{PREFIX}/daily/{day.isoformat()}.json"


def monthly_key(year: int, month: int) -> str:
    return f"{PREFIX}/monthly/{year:04d}-{month:02d}.json"


def meta_key() -> str:
    return f"{PREFIX}/meta.json"


def _tag_value(key: str) -> str:
    # Cost Explorer returns tag groups as "Sport$nfl", and "Sport$" when untagged.
    value = key.split("$", 1)[1] if "$" in key else key
    return value or MISC


def _cost_and_usage(ce, start: date, end: date, group_by: list[dict]) -> list[dict]:
    results: list[dict] = []
    token = None
    while True:
        kwargs = {
            "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
            "Granularity": "DAILY",
            "Metrics": [METRIC],
            "Filter": PROJECT_FILTER,
            "GroupBy": group_by,
        }
        if token:
            kwargs["NextPageToken"] = token
        response = ce.get_cost_and_usage(**kwargs)
        results.extend(response["ResultsByTime"])
        token = response.get("NextPageToken")
        if not token:
            return results


def _amount(group: dict) -> float:
    return float(group["Metrics"][METRIC]["Amount"])


def build_days(service_by_sport: list[dict], component_by_sport: list[dict], retrieved_at: str) -> dict[str, dict]:
    """One document per day from the two grouped responses."""
    days: dict[str, dict] = {}
    for period in service_by_sport:
        day = period["TimePeriod"]["Start"]
        doc = days.setdefault(day, _empty_day(day, retrieved_at))
        doc["estimated"] = doc["estimated"] or bool(period.get("Estimated"))
        for group in period.get("Groups", []):
            service, sport = group["Keys"][0], _tag_value(group["Keys"][1])
            amount = _amount(group)
            doc["total"] += amount
            doc["by_service"][service] += amount
            doc["by_sport"][sport] += amount
    for period in component_by_sport:
        day = period["TimePeriod"]["Start"]
        doc = days.setdefault(day, _empty_day(day, retrieved_at))
        for group in period.get("Groups", []):
            component, sport = _tag_value(group["Keys"][0]), _tag_value(group["Keys"][1])
            amount = _amount(group)
            doc["by_component"][component] += amount
            doc["by_sport_component"][f"{sport}/{component}"] += amount
    return {day: _finish(doc) for day, doc in days.items()}


def _empty_day(day: str, retrieved_at: str) -> dict:
    return {
        "date": day,
        "currency": "USD",
        "metric": METRIC,
        "estimated": False,
        "total": 0.0,
        "by_service": defaultdict(float),
        "by_sport": defaultdict(float),
        "by_component": defaultdict(float),
        "by_sport_component": defaultdict(float),
        "retrieved_at": retrieved_at,
    }


def _finish(doc: dict) -> dict:
    out = dict(doc)
    out["total"] = round(doc["total"], 4)
    for field in ("by_service", "by_sport", "by_component", "by_sport_component"):
        out[field] = {k: round(v, 4) for k, v in sorted(doc[field].items(), key=lambda kv: -kv[1]) if round(v, 4) != 0}
    return out


def rollup_month(days: list[dict]) -> dict:
    total = 0.0
    sums = {field: defaultdict(float) for field in ("by_service", "by_sport", "by_component", "by_sport_component")}
    for day in days:
        total += day["total"]
        for field, bucket in sums.items():
            for key, value in day.get(field, {}).items():
                bucket[key] += value
    return {
        "month": days[0]["date"][:7] if days else None,
        "days": len(days),
        "estimated": any(d.get("estimated") for d in days),
        "total": round(total, 4),
        **{field: {k: round(v, 4) for k, v in sorted(bucket.items(), key=lambda kv: -kv[1])} for field, bucket in sums.items()},
    }


def _forecast(ce, today: date) -> float | None:
    end = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    try:
        response = ce.get_cost_forecast(
            TimePeriod={"Start": today.isoformat(), "End": end.isoformat()},
            Metric="UNBLENDED_COST",
            Granularity="MONTHLY",
            Filter=PROJECT_FILTER,
        )
    except Exception:
        # Cost Explorer refuses a forecast until it has enough history.
        logger.warning("No cost forecast available", exc_info=True)
        return None
    return round(float(response["Total"]["Amount"]), 2)


def _budget_limit(budgets, account_id: str, budget_name: str) -> float | None:
    try:
        response = budgets.describe_budget(AccountId=account_id, BudgetName=budget_name)
    except Exception:
        logger.warning("Could not read budget %s", budget_name, exc_info=True)
        return None
    return float(response["Budget"]["BudgetLimit"]["Amount"])


def _month_start(day: date, months_back: int = 0) -> date:
    year, month = day.year, day.month - months_back
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def windows(today: date, backfill: bool, settings: ProducerSettings) -> list[tuple[date, date]]:
    """[start, end) windows to pull this run: the last few days Cost Explorer
    may still revise, plus the whole previous month on the re-pull day."""
    if backfill:
        return [(_month_start(today, settings.cost_backfill_months), today)]
    found = [(today - timedelta(days=settings.cost_restated_days), today)]
    if today.day == settings.cost_month_repull_day:
        found.append((_month_start(today, 1), _month_start(today)))
    return found


def _pull(ce, today: date, backfill: bool, settings: ProducerSettings, retrieved_at: str) -> dict[str, dict]:
    days: dict[str, dict] = {}
    for start, end in windows(today, backfill, settings):
        if start < end:
            days.update(build_days(
                _cost_and_usage(ce, start, end, _SERVICE_BY_SPORT),
                _cost_and_usage(ce, start, end, _COMPONENT_BY_SPORT),
                retrieved_at,
            ))
    return days


def _roll_months(s3, months: list[str]) -> None:
    for month in months:
        keys = sorted(k for k in s3.list_keys(f"{PREFIX}/daily/{month}-") if k.endswith(".json"))
        s3.put_json(monthly_key(int(month[:4]), int(month[5:])), rollup_month([s3.get_json(k) for k in keys]))


def _first_spend(days: dict[str, dict], known: str | None) -> str | None:
    spent = [d for d in sorted(days) if days[d]["total"] > 0]
    if spent and (known is None or spent[0] < known):
        return spent[0]
    return known


def run(ce, budgets, s3, account_id: str, budget_name: str, today: date, settings: ProducerSettings, backfill: bool = False) -> dict:
    """Pulls, writes the daily docs, re-rolls each touched month and the meta doc."""
    meta = s3.get_json_or_none(meta_key()) or {}
    backfill = backfill or not meta
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    days = _pull(ce, today, backfill, settings, retrieved_at)
    for day, doc in days.items():
        s3.put_json(daily_key(date.fromisoformat(day)), doc)
    months = sorted({day[:7] for day in days})
    _roll_months(s3, months)
    data_from = _first_spend(days, meta.get("data_from"))

    s3.put_json(meta_key(), {
        "project": PROJECT,
        "data_from": data_from,
        "tags": ["Project", "iamPrincipal/Project", "Sport", "Component"],
        "budget_name": budget_name,
        "budget_limit": _budget_limit(budgets, account_id, budget_name),
        "month_forecast": _forecast(ce, today),
        "updated_at": retrieved_at,
        "excluded": ["Cost Explorer API requests", "shared ECR repository", "tax", "support"],
    })
    return {"days_written": len(days), "months_rolled": months, "backfill": backfill, "data_from": data_from}
