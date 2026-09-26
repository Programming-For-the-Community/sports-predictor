"""
Graded samples for the head-to-head sports (NFL, NCAAFB, NBA, NCAAMBB): turns
each completed event's pre-kickoff prediction rows plus what actually happened
into the samples library.performance.scorecard scores. Pure functions -- the
model-performance Lambda does the fetching.

Models graded, per sport, are whichever are currently promoted (their model
cards say which): the win-probability pick, the margin / home-score /
away-score amounts, and each player-prop stat.
"""
import re
from datetime import date, timedelta

from library.performance import scorecard
from library.performance.scorecard import AmountSample, Period, PickSample
from library.serving.common import SCORE_MODELS, WIN_PROBABILITY_MODEL, _actual_result, latest_matching_row

FOOTBALL = {"nfl", "ncaafb"}

_PLAYER_PROP_ROW = re.compile(r"^MODEL#player-prop-([a-z-]+)#v\d+#PLAYER#(.+)$")
# NFL stores ESPN's numeric season type (2 = regular season); NCAAFB stores
# CFBD's "regular"/"postseason". A missing season_type counts as regular.
_REGULAR_SEASON_TYPES = {"2", "regular"}


def _is_regular_season(event: dict) -> bool:
    season_type = event.get("season_type")
    return season_type is None or str(season_type) in _REGULAR_SEASON_TYPES


def period_for(sport: str, event: dict) -> Period:
    """The reporting period an event falls in: a numbered week for football
    (all postseason games share one "Playoffs" period, after every regular
    week), the Monday-to-Sunday week of the event's date for basketball."""
    if sport in FOOTBALL and event.get("week") is not None:
        if not _is_regular_season(event):
            return Period("2-postseason", "Playoffs")
        week = int(event["week"])
        return Period(f"1-{week:02d}", f"Wk {week}")
    day = date.fromisoformat(event["event_date"])
    monday = day - timedelta(days=day.weekday())
    return Period(monday.isoformat(), f"{monday.strftime('%b')} {monday.day}")


def collect_samples(sport: str, events: list[dict], rows_by_event: dict[str, list[dict]], stats_by_event: dict[str, dict[str, dict]]) -> dict[str, list]:
    """{model_name: [samples]} across every completed event that has both a
    pre-kickoff prediction and a final result."""
    samples: dict[str, list] = {}

    def add(model_name: str, sample) -> None:
        samples.setdefault(model_name, []).append(sample)

    for event in events:
        actual = _actual_result(event)
        rows = rows_by_event.get(event["event_key"], [])
        if actual is None or not rows:
            continue
        period = period_for(sport, event)

        win_row = latest_matching_row(rows, WIN_PROBABILITY_MODEL)
        if win_row is not None:
            home_win_probability = win_row["predicted_value"]["home_win_probability"]
            add(WIN_PROBABILITY_MODEL, PickSample(
                period=period,
                correct=(home_win_probability >= 0.5) == actual["home_won"],
                edge=abs(home_win_probability - 0.5),
                baseline_correct=actual["home_won"],
            ))

        actual_values = {
            "margin": actual["home_score"] - actual["away_score"],
            "home_score": actual["home_score"],
            "away_score": actual["away_score"],
        }
        for key, model_name in SCORE_MODELS.items():
            row = latest_matching_row(rows, model_name)
            if row is not None:
                add(model_name, AmountSample(period, row["predicted_value"]["value"], actual_values[key]))

        _collect_player_props(add, period, rows, stats_by_event.get(event["event_key"], {}))
    return samples


def _collect_player_props(add, period: Period, rows: list[dict], actual_by_entity: dict[str, dict]) -> None:
    """One sample per (player, stat) predicted for the event whose player has a
    stat line -- a predicted player who never played has no result to grade. A
    stat missing from a real stat line means the player recorded none of it."""
    latest: dict[tuple[str, str], dict] = {}
    for row in rows:
        match = _PLAYER_PROP_ROW.match(row["model_key"])
        if match is None:
            continue
        key = (match.group(1), match.group(2))
        if key not in latest or row.get("generated_at", "") >= latest[key].get("generated_at", ""):
            latest[key] = row

    for (stat_slug, entity_id), row in latest.items():
        stat_line = actual_by_entity.get(entity_id)
        if not stat_line:
            continue
        actual_value = stat_line.get(stat_slug.replace("-", "_"), 0)
        add(f"player-prop-{stat_slug}", AmountSample(period, row["predicted_value"]["value"], actual_value))


def build_records(samples_by_model: dict[str, list], model_cards: list[dict]) -> list[dict]:
    """One record per currently-promoted model -- a promoted model with no
    graded predictions yet still gets one (the UI shows "no graded games yet")."""
    records = []
    for card in sorted(model_cards, key=lambda c: c["model_name"]):
        name = card["model_name"]
        samples = samples_by_model.get(name, [])
        if name == WIN_PROBABILITY_MODEL:
            records.append(scorecard.pick_record(name, card.get("version"), samples, card.get("accuracy")))
        else:
            mae = card.get("mae")
            records.append(scorecard.amount_record(name, card.get("version"), samples, mae, mae))
    return records


def build_head_to_head_scorecard(
    sport: str, season: int | None, events: list[dict], rows_by_event: dict[str, list[dict]],
    stats_by_event: dict[str, dict[str, dict]], model_cards: list[dict],
) -> dict:
    samples = collect_samples(sport, events, rows_by_event, stats_by_event)
    return scorecard.build_scorecard(sport, season, "week", build_records(samples, model_cards))
