"""
The admin agent's cost tool. Reads only the daily snapshots and meta doc
library.assistant.cost_snapshot writes -- Project-tagged spend, nothing else.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from library.assistant import cost_snapshot
from library.assistant.date_ranges import clip, days_between, get_all, history_note, requested_range, today

TOP_GROUPS = 10


def _total(docs: list[dict | None], group_by: str) -> tuple[float, dict[str, float], bool]:
    total, groups, estimated = 0.0, defaultdict(float), False
    for doc in docs:
        if not doc:
            continue
        total += doc["total"]
        estimated = estimated or doc.get("estimated", False)
        for key, value in doc.get(f"by_{group_by}", {}).items():
            groups[key] += value
    return total, groups, estimated


def _daily(s3, days: list[date]) -> list[dict | None]:
    return get_all(s3, [cost_snapshot.daily_key(d) for d in days])


def _ranked(groups: dict[str, float]) -> list[dict]:
    ranked = sorted(groups.items(), key=lambda kv: -kv[1])
    top = [{"name": k, "usd": round(v, 2)} for k, v in ranked[:TOP_GROUPS]]
    rest = sum(v for _, v in ranked[TOP_GROUPS:])
    if rest:
        top.append({"name": "everything else", "usd": round(rest, 2)})
    return top


def _previous(s3, start: date, length: int, data_from: str | None, group_by: str) -> tuple[float | None, float | None]:
    """(previous period total, the same total only when data covers the whole
    previous period -- the basis for a fair change %)."""
    days = [start - timedelta(days=i) for i in range(length, 0, -1)]
    if data_from:
        days = [d for d in days if d >= date.fromisoformat(data_from)]
    total, _, _ = _total(_daily(s3, days), group_by)
    return (round(total, 2) if days else None), (total if days and len(days) == length else None)


def _budget(s3, limit: float | None) -> tuple[float | None, float | None]:
    if not limit:
        return None, None
    now = today()
    month_to_date = round(sum(d["total"] for d in _daily(s3, days_between(now.replace(day=1), now)) if d), 2)
    return month_to_date, round(100 * month_to_date / limit, 1)


def cost_summary(s3, args: dict) -> dict:
    meta = s3.get_json_or_none(cost_snapshot.meta_key()) or {}
    data_from = meta.get("data_from")
    requested_start, end = requested_range(args, 0, default_start=today().replace(day=1))
    start = clip(requested_start, data_from)
    group_by = args.get("group_by", "service")
    days = days_between(start, end) if start <= end else []
    total, groups, estimated = _total(_daily(s3, days), group_by)

    previous_total, comparable = _previous(s3, start, len(days), data_from, group_by)
    previous_start, previous_end = start - timedelta(days=len(days)), start - timedelta(days=1)
    change = round(100 * (total - comparable) / comparable, 1) if comparable else None
    month_to_date, used_pct = _budget(s3, meta.get("budget_limit"))
    return {
        "start_date": start.isoformat(), "end_date": end.isoformat(), **history_note(requested_start, data_from),
        "currency": "USD", "total": round(total, 2), "estimated": estimated, "group_by": group_by, "groups": _ranked(groups),
        "previous_period": {"start_date": previous_start.isoformat(), "end_date": previous_end.isoformat()} if days else None,
        "previous_period_total": previous_total, "change_pct": change,
        "budget_limit": meta.get("budget_limit"), "month_to_date": month_to_date, "budget_used_pct": used_pct,
        "month_forecast": meta.get("month_forecast"),
        "scope": "Only spend tagged Project=sports-predictor (on the resource or on the calling IAM role).",
        "excluded": meta.get("excluded", []),
        "misc_means": "Project spend whose resource has no Sport or Component tag.",
    }
