"""
Every sport's model results side by side, for "which sport is the model best
at" questions: each sport's scorecard (the one the app's performance tab
reads) cut to one row per model.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

Reader = Callable[[str, dict, dict], tuple[int, dict]]

KINDS = {
    "pick": "share of winners called right (higher is better); baseline: always the home team",
    "chance": "share of yes/no calls right, a call being yes above 50% (higher is better); baseline: always no",
    "amount": "average miss in the stat's own units (lower is better); baseline: always the season's actual average",
    "vs_baseline_pct": "how much better than its baseline, in percent (negative is worse)",
}


def _row(model: dict) -> dict:
    season = model.get("season") or {}
    row = {"model": model.get("model_name"), "kind": model.get("kind"), "season": season.get("value"), "graded": season.get("n")}
    if model.get("vs_baseline_pct") is not None:
        row["vs_baseline_pct"] = model["vs_baseline_pct"]
    return row


def _sport(read: Reader, sport: str, model: str | None) -> dict:
    code, body = read(f"/{sport}/model-performance", {}, {})
    if code not in (200, 203):
        return {"sport": sport, "error": f"Results unavailable ({code})."}
    models = [m for m in body.get("models", []) if not model or model.lower() in (m.get("model_name") or "").lower()]
    return {"sport": sport, "season": body.get("season"), "generated_at": body.get("generated_at"),
            "models": [_row(m) for m in models if (m.get("season") or {}).get("n")]}


def overview(read: Reader, sports: list[str], model: str | None) -> dict:
    with ThreadPoolExecutor(max_workers=max(1, min(len(sports), 8))) as pool:
        per_sport = list(pool.map(lambda sport: _sport(read, sport, model), sports))
    return {"kinds": KINDS, "sports": per_sport,
            "note": ("season is the share right for pick and chance models, or the average miss for amount models; graded is how many predictions it covers. "
                     "Rank or compare models only against others of the same kind; a chance model's share right is not a pick accuracy.")}
