"""
The admin agent's denied-traffic tools: locations, repeat offenders,
requested endpoints and the trend. They read only the hourly/daily
aggregates library.assistant.traffic writes, never raw log lines.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from library.assistant import traffic
from library.assistant.date_ranges import clip, days_between, first_day, get_all, history_note, requested_range
from library.assistant.settings import ToolSettings


def traffic_rows(s3, start: date, end: date, source: str, hourly: bool = False) -> tuple[list[tuple[str, dict]], str | None]:
    """(bucket label, row) pairs for the range -- labels are days, or hours
    when hourly -- plus the first day any aggregate exists. Every producer
    run rebuilds the day doc of each hour it writes, so today's is current."""
    data_from = first_day(s3, f"{traffic.PREFIX}/day/")
    days = days_between(clip(start, data_from), end)
    labelled = _hourly_rows(s3, days) if hourly else _daily_rows(s3, days)
    if source != "all":
        labelled = [(label, row) for label, row in labelled if row["source"] == source]
    return labelled, data_from


def _hourly_rows(s3, days: list[date]) -> list[tuple[str, dict]]:
    keys = sorted(k for day in days for k in s3.list_keys(f"{traffic.PREFIX}/hour/{day.isoformat()}T"))
    return [(doc["hour"], row) for doc in get_all(s3, keys) if doc for row in doc.get("rows", [])]


def _daily_rows(s3, days: list[date]) -> list[tuple[str, dict]]:
    docs = get_all(s3, [traffic.day_key(d) for d in days])
    return [(day.isoformat(), row) for day, doc in zip(days, docs, strict=True) if doc for row in doc.get("rows", [])]


def _range_info(start: date, end: date, data_from: str | None, source: str) -> dict:
    """The range the answer actually covers: the start is clipped to data_from."""
    return {"start_date": clip(start, data_from).isoformat(), "end_date": end.isoformat(), "source": source, **history_note(start, data_from)}


def _share(part: int, total: int) -> float:
    return round(100 * part / total, 1) if total else 0


def denied_locations(s3, args: dict, settings: ToolSettings) -> dict:
    start, end = requested_range(args, 7)
    source = args.get("source", "all")
    rows, data_from = traffic_rows(s3, start, end, source)
    by_country: dict[str, dict] = defaultdict(lambda: {"blocked": 0, "ips": set(), "rules": defaultdict(int)})
    for _, row in rows:
        bucket = by_country[row["country"] or "unknown"]
        bucket["blocked"] += row["n"]
        if row["ip"]:
            bucket["ips"].add(row["ip"])
        bucket["rules"][row["rule"]] += row["n"]
    total = sum(b["blocked"] for b in by_country.values())
    countries = [
        {"country": c, "blocked": b["blocked"], "share_pct": _share(b["blocked"], total),
         "distinct_ips": len(b["ips"]), "top_rule": max(b["rules"], key=b["rules"].get)}
        for c, b in sorted(by_country.items(), key=lambda kv: -kv[1]["blocked"])
    ]
    return {**_range_info(start, end, data_from, source), "total_blocked": total, "countries": len(countries),
            "by_country": countries[:settings.max_rows],
            "note": "API Gateway access-log denials carry no client location and are counted as 'unknown'."}


def _offender(ip: str, b: dict) -> dict:
    return {
        "ip": ip, "blocked": b["blocked"], "days_active": len(b["days"]), "first_seen": min(b["days"]), "last_seen": max(b["days"]),
        "countries": sorted(b["countries"]), "rules": dict(sorted(b["rules"].items(), key=lambda kv: -kv[1])),
        "top_paths": [p for p, _ in sorted(b["paths"].items(), key=lambda kv: -kv[1])[:3]],
    }


def repeat_offenders(s3, args: dict, settings: ToolSettings) -> dict:
    start, end = requested_range(args, 30)
    source = args.get("source", "all")
    min_blocks = args.get("min_blocks", settings.repeat_min_blocks)
    min_days = args.get("min_days", settings.repeat_min_days)
    rows, data_from = traffic_rows(s3, start, end, source)
    by_ip: dict[str, dict] = defaultdict(lambda: {"blocked": 0, "days": set(), "countries": set(), "rules": defaultdict(int), "paths": defaultdict(int)})
    for day, row in rows:
        if not row["ip"]:
            continue
        b = by_ip[row["ip"]]
        b["blocked"] += row["n"]
        b["days"].add(day)
        if row["country"]:
            b["countries"].add(row["country"])
        b["rules"][row["rule"]] += row["n"]
        b["paths"][row["path"]] += row["n"]
    serious = [_offender(ip, b) for ip, b in by_ip.items() if b["blocked"] >= min_blocks or len(b["days"]) >= min_days]
    serious.sort(key=lambda o: (-o["blocked"], o["ip"]))
    return {**_range_info(start, end, data_from, source), "thresholds": {"min_blocks": min_blocks, "min_days": min_days},
            "distinct_blocked_ips": len(by_ip), "serious_count": len(serious), "serious": serious[:settings.max_rows]}


def denied_endpoints(s3, args: dict, settings: ToolSettings) -> dict:
    start, end = requested_range(args, 7)
    source = args.get("source", "all")
    rows, data_from = traffic_rows(s3, start, end, source)
    by_path: dict[tuple, dict] = defaultdict(lambda: {"blocked": 0, "ips": set()})
    for _, row in rows:
        b = by_path[(row["method"], row["path"], row["kind"])]
        b["blocked"] += row["n"]
        if row["ip"]:
            b["ips"].add(row["ip"])
    total = sum(b["blocked"] for b in by_path.values())
    paths = [
        {"method": k[0], "path": k[1], "kind": k[2], "blocked": b["blocked"], "distinct_ips": len(b["ips"]), "share_pct": _share(b["blocked"], total)}
        for k, b in sorted(by_path.items(), key=lambda kv: -kv[1]["blocked"])
    ]
    return {**_range_info(start, end, data_from, source), "total_blocked": total, "endpoints": paths[:settings.max_rows],
            "note": "Paths are request data written by the client: app routes are shown as templates, anything else as sent (sanitised)."}


def denied_trend(s3, args: dict, settings: ToolSettings) -> dict:
    start, end = requested_range(args, 7)
    source = args.get("source", "all")
    hourly = (end - start).days < settings.hourly_trend_days
    rows, data_from = traffic_rows(s3, start, end, source, hourly=hourly)
    points: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for label, row in rows:
        points[label][row["rule"]] += row["n"]
    series = [{"at": label, "total": sum(r.values()), "by_rule": dict(r)} for label, r in sorted(points.items())]
    return {**_range_info(start, end, data_from, source), "granularity": "hour" if hourly else "day",
            "points": series[-settings.max_trend_points:], "note": "Periods with nothing blocked are left out."}
