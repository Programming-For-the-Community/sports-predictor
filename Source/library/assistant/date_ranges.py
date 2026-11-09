"""Date-range and S3 helpers shared by the admin tools."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from library.parsing import us_eastern_date


def today() -> date:
    return datetime.now(timezone.utc).date()


def eastern_today() -> date:
    """Today on U.S. Eastern dates, the app's game dates."""
    return date.fromisoformat(us_eastern_date(datetime.now(timezone.utc)))


def requested_range(args: dict, default_days_back: int, default_start: date | None = None) -> tuple[date, date]:
    """(start, end) from a tool's start_date/end_date, end capped at today."""
    end = date.fromisoformat(args["end_date"]) if args.get("end_date") else today()
    if args.get("start_date"):
        start = date.fromisoformat(args["start_date"])
    else:
        start = default_start or end - timedelta(days=default_days_back)
    if start > today():
        raise ValueError("start_date is in the future")
    if start > end:
        raise ValueError("start_date is after end_date")
    return start, min(end, today())


def days_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def get_all(s3, keys: list[str]) -> list[dict | None]:
    with ThreadPoolExecutor(max_workers=16) as pool:
        return list(pool.map(s3.get_json_or_none, keys))


def first_day(s3, prefix: str) -> str | None:
    """The date in the earliest key under prefix, e.g. ".../day/2026-10-01.json"."""
    keys = sorted(s3.list_keys(prefix))
    return keys[0].rsplit("/", 1)[1].removesuffix(".json")[:10] if keys else None


def clip(start: date, data_from: str | None) -> date:
    return max(start, date.fromisoformat(data_from)) if data_from else start


def history_note(requested_start: date, data_from: str | None) -> dict:
    """{"history_starts", "note"} only when the data begins after the start
    asked for, so the agent mentions the shortened range only then."""
    if not data_from or date.fromisoformat(data_from) <= requested_start:
        return {}
    return {"history_starts": data_from,
            "note": f"Data only goes back to {data_from}, so the range starts then instead of {requested_start.isoformat()}."}
