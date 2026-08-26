"""
PGA (field-event) feature computation. Genuinely different shape from
library.features.common's head-to-head helpers: a golfer's own rolling
history comes directly from events.participants (there's no separate
player_game_stats table for a field-event sport, and no team/opponent
concept at all -- see design/DATA_SCHEMA.md's field-event participants
section), not from a team-vs-team result.
"""

DEFAULT_ROLLING_WINDOW = 5
# Course-fit history is capped by COUNT of past appearances, not recency
# in calendar time, the same as the overall rolling window -- but since a
# given course only recurs roughly once a year (PGA Tour events are
# annual), 5 here means "this golfer's last 5 YEARS at this course," a
# genuinely longer real time span than DEFAULT_ROLLING_WINDOW's "last 5
# STARTS" (a few weeks). Kept as its own named constant rather than
# reusing DEFAULT_ROLLING_WINDOW directly so the two aren't accidentally
# coupled if one is ever tuned independently of the other.
DEFAULT_COURSE_HISTORY_WINDOW = 5

# Raw ESPN category name -> this project's own snake_case feature column
# name, for the 6 season-stat categories golfer_features.parquet carries
# (season_* columns, build_golfer_event_features below). Only the
# categories with no other way for this project to ever compute them
# itself -- driving distance/accuracy, GIR%, putts per hole, birdies per
# round, scoring average -- confirmed live, 2026-08-25, that no
# per-golfer-per-event breakdown of these exists anywhere in ESPN's golf
# API (docs/PGA_FEATURE_ENGINEERING.md), so a season-to-date snapshot
# (aws-lambdas/pga/ingest/handler.py, feature-engineering/pga/
# build_dataset.py) is the only source. officialAmount/cupPoints/wins/
# topTenFinishes/cutsMade are deliberately excluded -- this project
# already computes its own close equivalents from event history
# (avg_earnings, top_10_rate, finish_rate), so ESPN's own season-long
# versions of those would be redundant, not new signal.
SEASON_STAT_CATEGORIES = {
    "yardsPerDrive": "season_driving_distance",
    "driveAccuracyPct": "season_driving_accuracy_pct",
    "greensInRegPct": "season_gir_pct",
    "strokesPerHole": "season_putts_per_hole",
    "birdiesPerRound": "season_birdies_per_round",
    "scoringAverage": "season_scoring_average",
}


def rolling_golfer_averages(golfer_results: list[dict], window: int = DEFAULT_ROLLING_WINDOW) -> dict:
    """golfer_results: a golfer's own past participants[].result dicts,
    most recent first, NOT including the tournament being scored. Each
    dict has the shape leaderboard_event_to_event_item produces
    (finish_position, status, score_to_par, total_strokes, earnings).

    top_10_rate/top_20_rate/finish_rate are all divided by the number of
    STARTS in the window (windowed's own length), not just the finishes --
    missing the cut is a real outcome that counts against making top 10,
    not a row to silently exclude from the rate's denominator. avg_
    score_to_par/avg_finish_position/avg_earnings instead average only
    over the rows that actually have a value (a missed cut still reports
    score_to_par for its rounds played, but has no finish_position -- see
    library/normalize/pga.py's own _parse_score/_parse_finish_position).

    Every value is None (not 0) when the window has no qualifying rows at
    all -- a golfer with no tournament history yet, same "missing, not
    fabricated" rule library.features.common's rolling helpers use.
    """
    windowed = golfer_results[:window]
    starts = len(windowed)

    finished = [r for r in windowed if r.get("finish_position") is not None]
    score_to_par_values = [r["score_to_par"] for r in windowed if r.get("score_to_par") is not None]
    earnings_values = [r["earnings"] for r in windowed if r.get("earnings") is not None]
    finish_positions = [r["finish_position"] for r in finished]

    return {
        "avg_score_to_par": sum(score_to_par_values) / len(score_to_par_values) if score_to_par_values else None,
        "avg_finish_position": sum(finish_positions) / len(finish_positions) if finish_positions else None,
        "best_finish_position": min(finish_positions) if finish_positions else None,
        "top_10_rate": sum(1 for p in finish_positions if p <= 10) / starts if starts else None,
        "top_20_rate": sum(1 for p in finish_positions if p <= 20) / starts if starts else None,
        "finish_rate": len(finished) / starts if starts else None,
        "avg_earnings": sum(earnings_values) / len(earnings_values) if earnings_values else None,
        "events_played": starts,
    }


def build_golfer_event_features(
    event: dict,
    participant: dict,
    prior_results: list[dict],
    window: int = DEFAULT_ROLLING_WINDOW,
    course_results: list[dict] | None = None,
    course_window: int = DEFAULT_COURSE_HISTORY_WINDOW,
    season_stats: dict[str, float | None] | None = None,
) -> dict:
    """One training row: this golfer's rolling form (from prior_results)
    plus this event's own field-strength context (purse, is_major,
    field_size -- all known ahead of the tournament, so equally available
    at live-prediction time, not just training time), a course-fit block
    (from course_results, this golfer's own past results specifically at
    THIS event's course_id -- see rolling_golfer_averages' own docstring
    for why the exact same averaging function works unchanged for either
    input), a season-stats block (from season_stats, this golfer's own
    driving/GIR/putting/scoring numbers as of the most recent snapshot
    before this event -- see SEASON_STAT_CATEGORIES' own docstring), and
    every label this one shared dataset trains toward (top-10, top-5, and
    the continuous score_to_par a "field finish order" prediction ranks
    golfers by at serving time -- library/ml/backtest.py has no rank-loss
    or multi-class support, see docs/PGA_FEATURE_ENGINEERING.md, so none
    of these need a second dataset build or a new task type).

    field_size is this event's own participant count, not a rolling
    average -- a bigger field mechanically lowers everyone's odds of a
    top-10 finish regardless of who's in it, so it's a per-event feature
    like purse/is_major, not something to smooth across a golfer's history.

    course_results/season_stats both default to None (not [] / {}) so a
    caller that hasn't resolved that history yet (e.g. an older call
    site, a course_id genuinely absent on the event, or -- true for every
    single backfilled historical row, since there is no historical source
    for season stats at all -- no qualifying snapshot exists yet) still
    gets every course_*/season_* column as an explicit missing value,
    rather than a row whose column SET differs from every other row's,
    which pandas' later union-of-columns Parquet write would otherwise
    silently paper over instead of failing loudly."""
    result = participant.get("result") or {}
    finish_position = result.get("finish_position")

    row = {
        "event_key": event["event_key"],
        "entity_id": participant["entity_id"],
        "event_date": event["event_date"],
        "purse": event.get("purse"),
        "is_major": bool(event.get("is_major", False)),
        "field_size": len(event.get("participants", [])),
        "label_top_10": 1 if finish_position is not None and finish_position <= 10 else 0,
        "label_top_5": 1 if finish_position is not None and finish_position <= 5 else 0,
        # Continuous label for the projected-score-to-par regression model
        # -- "field finish order" is a serving-time ranking of this
        # model's own predictions across one tournament's field, not a
        # separately trained artifact. None (not a real target) for a row
        # with no recorded score at all (e.g. a withdrawal before playing
        # a single hole) -- filtered out at training time, same as every
        # other regression target in this project handles a null label.
        "label_score_to_par": result.get("score_to_par"),
    }
    row.update(rolling_golfer_averages(prior_results, window))
    row.update({f"course_{key}": value for key, value in rolling_golfer_averages(course_results or [], course_window).items()})
    row.update({
        column_name: (season_stats or {}).get(raw_category)
        for raw_category, column_name in SEASON_STAT_CATEGORIES.items()
    })
    return row


def rolling_round_averages(round_results: list[dict], window: int = DEFAULT_ROLLING_WINDOW) -> dict:
    """round_results: this golfer's own past rounds SPECIFICALLY at the
    SAME round number being scored (e.g. every past "round 1" they've
    played across tournaments), most recent first, NOT including the
    round being scored. Each dict has the shape library/normalize/pga.py's
    _parse_rounds produces (round, score_to_par, total_strokes) -- a
    genuinely different, smaller shape than a tournament-level result
    dict (no finish_position/earnings at the round grain), which is why
    this is its own function rather than a reuse of rolling_golfer_
    averages against round dicts (those two fields would just always
    resolve to None, silently).

    Genuinely different signal from a golfer's overall tournament rolling
    average -- some golfers are consistently fast/slow starters or
    strong/weak closers, a pattern only visible by round number, not from
    their tournament-level average alone."""
    windowed = round_results[:window]
    score_to_par_values = [r["score_to_par"] for r in windowed if r.get("score_to_par") is not None]
    return {
        "avg_score_to_par": sum(score_to_par_values) / len(score_to_par_values) if score_to_par_values else None,
        "rounds_played": len(windowed),
    }


def build_round_event_features(
    event: dict,
    participant: dict,
    round_result: dict,
    prior_overall_results: list[dict],
    prior_same_round_results: list[dict],
    window: int = DEFAULT_ROLLING_WINDOW,
) -> dict:
    """One row per (golfer, tournament, round actually played) -- the
    per-round-score-projection models' own grain, genuinely finer than
    build_golfer_event_features' one-row-per-tournament. round_result is
    one entry from participant["result"]["rounds"] (library/normalize/
    pga.py's _parse_rounds).

    A cut golfer's own `rounds` list naturally has only 2 entries, not 4
    (confirmed live -- see _parse_rounds' own docstring), so this
    function is simply never called for a round 3/4 that didn't happen --
    no conditional cut-checking logic needed here or in the caller that
    walks each participant's rounds list. The "don't project rounds 3-4
    for a golfer projected to miss the cut" behavior the user asked for
    is a SERVING-time concern (which rounds to even bother calling this
    model for on a live, in-progress tournament), not a training-time one
    -- there is no predict Lambda yet (Phase 5 step 4) to implement that
    in.

    prior_overall_results feeds the golfer's usual tournament-level
    rolling averages (rolling_golfer_averages, `overall_`-prefixed);
    prior_same_round_results feeds their round-number-specific history
    (rolling_round_averages, `same_round_`-prefixed) -- deliberately no
    course-fit or season-stats block here, unlike build_golfer_event_
    features, to keep this first version of round-level modeling scoped;
    add them later the same way course fit was added to the tournament-
    level dataset if round-level modeling proves out."""
    row = {
        "event_key": event["event_key"],
        "entity_id": participant["entity_id"],
        "event_date": event["event_date"],
        "round_number": round_result["round"],
        "purse": event.get("purse"),
        "is_major": bool(event.get("is_major", False)),
        "field_size": len(event.get("participants", [])),
        "label_round_score_to_par": round_result.get("score_to_par"),
    }
    row.update({f"overall_{key}": value for key, value in rolling_golfer_averages(prior_overall_results, window).items()})
    row.update({f"same_round_{key}": value for key, value in rolling_round_averages(prior_same_round_results, window).items()})
    return row


def build_cutline_event_features(
    event: dict, prior_course_cut_scores: list[float] | None = None, window: int = DEFAULT_COURSE_HISTORY_WINDOW,
) -> dict:
    """One row per completed Medal-scoring tournament -- the projected-
    cut-line model's own grain, TOURNAMENT-level rather than golfer-level
    (a cut line is a property of the whole field, not any one golfer's
    own result). Includes every tournament, cut or not -- train_cutline_
    model.py filters to cut_count > 0 at TRAIN time (a no-cut tournament
    genuinely reports cut_score/cut_round/cut_count all as a real 0, not
    a missing value -- see design/DATA_SCHEMA.md), same "filter at train
    time, keep the raw dataset complete" convention NCAAFB's own national-
    ranking model already uses for its own not-every-row-is-ranked case.

    prior_course_cut_scores: this SAME course's own past cut_score
    values, most recent first, if a course_id is known -- a course that
    plays hard or easy tends to do so consistently year to year, the one
    rolling signal this dataset carries (no golfer-level history makes
    sense at this grain)."""
    windowed = (prior_course_cut_scores or [])[:window]
    return {
        "event_key": event["event_key"],
        "event_date": event["event_date"],
        "purse": event.get("purse"),
        "is_major": bool(event.get("is_major", False)),
        "field_size": len(event.get("participants", [])),
        "course_avg_cut_score": sum(windowed) / len(windowed) if windowed else None,
        "cut_count": event.get("cut_count"),
        "label_cut_score": event.get("cut_score"),
    }
