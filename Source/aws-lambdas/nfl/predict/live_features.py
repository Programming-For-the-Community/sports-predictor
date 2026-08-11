"""
Assembles a live feature vector for one not-yet-played NFL event or one
player-prop target, using the exact same pure functions
(build_event_features/build_player_features, library/features/nfl.py)
that build the training datasets -- this is the "later" half of the
train/serve-skew guarantee those functions' own docstrings describe.

The difference from feature-engineering's build_dataset.py is entirely
in how history gets gathered: batch feature engineering loads the whole
table once and walks it incrementally (cheap when you're building
thousands of rows); this module looks up exactly one event's worth of
context via FeatureStorage's one-team/one-player/one-event methods
(get_team_events, get_player_game_stats, get_team_game_stats_for_team,
get_event, get_entity) -- cheap when you're only ever building one row
per request. get_team_entities backs presumptive-leader selection with
the team's CURRENT roster rather than box-score history -- see
_fresh_roster's own docstring. Recomputes Elo from the full
completed-events history on every call rather than caching a "current
rating" snapshot -- NFL's ~2,700 completed games is the same cost
build_dataset.py already treats as cheap-enough-to-scan, and this project
has no request volume that would make that recomputation a real cost.

Every function here takes an optional `events` (an already-fetched
get_all_events(sport) result) and threads it through downstream
get_team_events/_live_elo_ratings calls instead of each one re-querying;
omitted, each function fetches its own.
"""
from datetime import date

from library.features.common import DEFAULT_STARTING_RATING, compute_elo_ratings
from library.features.nfl import build_event_features, build_player_features
from library.schema.keys import player_key

DEFAULT_ROLLING_WINDOW = 5

# Matches the severity threshold library/features/nfl.py's
# _TEAM_INJURY_COUNT_STATUSES uses for the injury-count feature (Out AND
# Doubtful, not just Out) -- kept as its own local constant rather than
# importing nfl.py's private one, same reasoning predict/model_loader.py's
# own docstring gives for not importing model_common.py: this module and
# nfl.py are different consumers of the same business rule, not the same
# code reused twice.
_INJURY_EXCLUDED_STATUSES = frozenset({"Out", "Doubtful"})


class EventNotFoundError(Exception):
    pass


class MalformedEventError(Exception):
    """The event exists but is missing a home/away participant role --
    build_event_features/build_player_features both require both sides
    to determine home/away, opponent, and Elo lookups."""


def _home_away_ids(event: dict) -> tuple[str, str]:
    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    away = next((p for p in participants if p.get("role") == "away"), None)
    if home is None or away is None:
        raise MalformedEventError(f"Event {event.get('event_key')} is missing a home or away participant")
    return home["entity_id"], away["entity_id"]


def _live_elo_ratings(
    storage, sport: str, event: dict, home_id: str, away_id: str, current_ratings: dict | None = None,
    events: list[dict] | None = None,
) -> dict:
    """A minimal elo_ratings dict containing just this one (not-yet-played)
    event's key, mapped to each team's CURRENT rating -- the second
    compute_elo_ratings return value, not the first. The first
    (pre_game_ratings) only has entries for events already processed, so
    it has nothing for a future event's own key; build_event_features/
    build_player_features only ever do one lookup
    (elo_ratings.get(event['event_key'])), so a single-entry dict works
    identically to the full training-time one for this purpose.

    current_ratings/events are optional already-computed pass-throughs
    for a caller that's already paid for them (e.g. handler.py's
    _leaderboards, looping over many candidates)."""
    if current_ratings is None:
        completed_events = events if events is not None else storage.get_all_events(sport)
        _, current_ratings = compute_elo_ratings(completed_events, as_of_season=event.get("season"))
    return {
        event["event_key"]: {
            "home_pre_rating": current_ratings.get(home_id, DEFAULT_STARTING_RATING),
            "away_pre_rating": current_ratings.get(away_id, DEFAULT_STARTING_RATING),
        }
    }


def _depth_chart_entries(depth_chart: dict | None, abbreviations: set[str]) -> list[dict]:
    """Every depth_chart entry whose own position.abbreviation is in
    abbreviations, matched on that field rather than assuming a specific
    outer dict key -- library.storage.depth_chart_cache.filter_depth_chart
    does the same match-by-abbreviation rather than relying on ESPN's
    exact key casing/format.

    Single-element abbreviations sets are the common case (one slot each
    for QB/RB/TE), except WR -- ESPN's real depth chart (confirmed live)
    tracks three separate WR slots (wr1/wr2/wr3), each its own full
    backup stack for THAT starting receiver specifically, not one
    combined ranked list of every receiver on the roster. Callers that
    want candidates from more than one position (e.g. _LEADER_POSITIONS'
    own broader nets -- a scrambling QB as a rushing candidate, a
    pass-catching RB or the starting TE as a receiving candidate) pass a
    multi-element set here directly; _depth_chart_entry below is the
    single-abbreviation convenience wrapper."""
    if not depth_chart:
        return []
    return [entry for entry in depth_chart.values() if (entry.get("position") or {}).get("abbreviation") in abbreviations]


def _depth_chart_entry(depth_chart: dict | None, position_abbreviation: str) -> dict | None:
    """The single starter's depth-chart entry for exactly
    position_abbreviation (see _depth_chart_entries) -- deliberately
    narrow, not one of _LEADER_POSITIONS' broader nets, since "the"
    presumptive QB/RB/WR leader for the event-level features means the
    actual player at that slot, not whichever eligible position happens
    to sort first in the merged depth chart (a broader net here could
    e.g. return the starting QB for a "RB leader" lookup). For WR
    specifically, wr1 is exactly ESPN's own "the" WR slot, not an
    arbitrary pick among wr1/wr2/wr3, since it's always listed first
    (confirmed live). _healthy_athletes_across_slots below is the one
    that wants a broader, multi-entry set, for a multi-candidate pick
    (e.g. top receivers) where other slots' own starters matter too."""
    entries = _depth_chart_entries(depth_chart, {position_abbreviation})
    return entries[0] if entries else None


def _healthy_athlete_ids(depth_chart_entry: dict, injuries: list[dict] | None) -> list[str]:
    """Every entity_id in depth_chart_entry's own rank order, minus
    anyone injuries lists as Out or Doubtful (_INJURY_EXCLUDED_STATUSES).
    Rank order is exactly ESPN's own athletes[] ordering -- starter
    first -- so index 0 of the result (when non-empty) is the
    highest-ranked healthy player at this position."""
    injured_ids = {
        injury.get("entity_id") for injury in (injuries or [])
        if injury.get("status") in _INJURY_EXCLUDED_STATUSES
    }
    return [
        str(athlete["id"]) for athlete in depth_chart_entry.get("athletes", [])
        if "id" in athlete and str(athlete["id"]) not in injured_ids
    ]


def _healthy_athletes_across_slots(entries: list[dict], injuries: list[dict] | None, n: int | None) -> list[str]:
    """Up to n healthy athlete ids for a position (every one of them,
    across every entry, when n is None), drawn round-robin across every
    one of entries' own depth-chart slots (each slot's own rank-0 healthy
    athlete first, then each slot's rank-1, ...) rather than n-deep from
    a single slot. Matters specifically whenever entries has more than
    one slot (WR's distinct wr1/wr2/wr3, or a broader _LEADER_POSITIONS
    net pulling in another position's own slot too) -- taking n from a
    single slot would return that slot's own backups instead of every
    slot's own starter. Depth-chart RANK still isn't a reliable proxy for
    actual predicted production across DIFFERENT slots (a WR2's own
    backup can easily outproduce a WR3's starter, a backup RB can easily
    outproduce a scrambling QB) -- n=None callers (see
    _team_leader_candidates) deliberately don't ask this function to
    guess who to cut, they take everyone listed and let the actual
    per-candidate model predictions do the ranking downstream instead.
    A single entry with n given is equivalent to the old n-deep-from-
    one-slot behavior."""
    stacks = [_healthy_athlete_ids(entry, injuries) for entry in entries]
    result: list[str] = []
    for rank in range(max((len(stack) for stack in stacks), default=0)):
        for stack in stacks:
            if n is not None and len(result) >= n:
                return result
            if rank < len(stack) and stack[rank] not in result:
                result.append(stack[rank])
    return result


# Maps each single-leader slot to the roster positions eligible for it,
# ranked by recent volume of the matching stat when depth chart data
# isn't available. Broader than the slot's own name -- a QB scrambling
# for real rushing volume, or an RB catching passes out of the backfield,
# are both common enough that excluding them would wrongly hide a real
# leader, not just a technicality.
_LEADER_POSITIONS = {
    "QB": {"QB"},
    "RB": {"RB", "FB", "QB"},
    "WR": {"WR", "TE", "RB"},
}
_LEADER_VOLUME_STAT = {"QB": "passing_attempts", "RB": "rushing_attempts", "WR": "receiving_targets"}

# Sacks has no depth-chart source (ESPN's depth chart is offense/defense-
# position-based, not a "top pass rushers" list), so it's always ranked
# by recent volume -- restricted to defensive positions specifically
# (unlike QB/RB/WR's stat naturally zeroing out the wrong side of the
# ball) because ranking now includes zero-history candidates: an
# offensive player with 0 sacks would otherwise tie with a genuine
# defensive rookie who also has 0, with roster order arbitrarily
# deciding the winner.
_DEFENSIVE_POSITIONS = {"DE", "DT", "NT", "LB", "ILB", "OLB", "MLB", "CB", "S", "SS", "FS", "DB"}

# Roster sync fetches all 32 teams daily (see ingest/handler.py), so an
# entity actually still on a team gets its team_id_as_of refreshed every
# day. Roster sync only ever upserts who IS on a fetched roster -- nobody
# ever tells the entities table "this player left the league entirely"
# (retired, cut, unsigned), so a departed player's row just sits frozen
# at whatever team_id_as_of it last had. This many days without a
# refresh is treated as "no longer confirmed on this team" rather than
# trusted indefinitely -- generous enough to tolerate a few missed
# ingest days, tight enough to exclude someone retired for years.
_ROSTER_STALENESS_DAYS = 14


def _is_roster_entry_fresh(entity: dict, reference_date: str) -> bool:
    """reference_date is when this prediction is being generated (today),
    NOT the target event's own date -- roster sync only ever writes
    "today's" team_id_as_of, so for any event more than
    _ROSTER_STALENESS_DAYS out, comparing against the event's date instead
    would make every roster entry fail this check by construction, no
    matter how current the roster actually is. Comparing against today
    is also the right question to ask: what matters is whether this
    roster snapshot is still trustworthy right now, not its distance from
    a game that hasn't been played yet."""
    as_of = entity.get("metadata", {}).get("team_id_as_of")
    if as_of is None:
        return False
    try:
        return abs((date.fromisoformat(reference_date) - date.fromisoformat(as_of)).days) <= _ROSTER_STALENESS_DAYS
    except ValueError:
        return False


def _fresh_roster(storage, sport: str, team_id: str, reference_date: str | None = None) -> list[dict]:
    """Every entity CURRENTLY rostered to team_id, per the entities
    table's team-index (kept fresh by roster sync's daily upsert -- see
    normalize/espn.py), filtered to entries roster sync has actually
    confirmed recently (_is_roster_entry_fresh, judged against
    reference_date -- today by default, not the target event's own date;
    see that function's own docstring for why). This is the sole
    eligibility gate for presumptive-leader selection below -- being on
    this list is what makes a just-traded player show up under their new
    team immediately (their entity record's team_id already reflects the
    trade) and is ALL that's required, regardless of stat history: a
    rookie who hasn't played a game yet is still eligible, since nothing
    else about them is any less "on the roster" than a veteran."""
    reference = reference_date or date.today().isoformat()
    return [entity for entity in storage.get_team_entities(sport, team_id) if _is_roster_entry_fresh(entity, reference)]


def _eligible_entity_ids(roster: list[dict], positions: set[str]) -> list[str]:
    return [entity["entity_id"] for entity in roster if entity.get("metadata", {}).get("position") in positions]


def _recent_volume(storage, entity_id: str, stat: str, before_date: str, window: int) -> tuple[float, list[dict]]:
    """(sum of stat over a candidate's most recent `window` games, that
    same game list) for one candidate -- bounded to `window`, the same
    rolling window every other feature in this module uses, so ranking
    reuses exactly the query the winning candidate's own rolling
    features need anyway (one query per candidate either way, not two).
    A candidate with no games at all (a rookie, or a team's first event
    for a just-traded player) sums to 0 rather than being excluded --
    _leader_history/_top_n_by_recent_volume both rank on this pair's
    first element, so a 0 still competes on equal footing with anyone
    else, it just never wins over a candidate with real volume."""
    games = storage.get_player_game_stats(entity_id, before_date=before_date, limit=window)
    total = sum(game.get("stat_line", {}).get(stat, 0) for game in games)
    return total, games


def _leader_history(storage, entity_ids: list[str], stat: str, before_date: str, window: int) -> list[dict]:
    """The single top candidate among entity_ids by recent (last `window`
    games) volume of stat, returning that same game list -- [] if
    entity_ids is empty. Ties (including every candidate having zero
    history, e.g. a team with an all-rookie room at this position) fall
    back to roster order rather than excluding anyone -- being on the
    roster is what makes a candidate valid here, not stat history."""
    if not entity_ids:
        return []
    volumes = {entity_id: _recent_volume(storage, entity_id, stat, before_date, window) for entity_id in entity_ids}
    winner = max(volumes, key=lambda entity_id: volumes[entity_id][0])
    return volumes[winner][1]


def _presumptive_leader_and_history(
    storage, sport: str, team_id: str, before_date: str, window: int,
    depth_chart: dict | None = None, injuries: list[dict] | None = None, position_abbreviation: str | None = None,
    team_roster: list[dict] | None = None,
) -> list[dict]:
    """A future game has no player_game_stats of its own yet to identify
    a starting QB/lead rusher/lead receiver from. Three checks, in order:

    1. Depth chart (position_abbreviation's rank order, filtered to
       exclude anyone Out/Doubtful) -- preferred whenever depth_chart/
       injuries data is available, since it beats recent-volume ranking
       in exactly the case that matters most: a starter is ruled out and
       a genuine backup who simply hadn't played much yet takes over,
       someone volume-based selection would never surface. If the depth
       chart is known but everyone at this position is currently
       Out/Doubtful, that's a real "no leader available" result (empty
       list), not a reason to fall through to recent-volume ranking --
       that would risk re-selecting the player the depth chart just
       ruled out.
    2. Currently on the roster (_fresh_roster/team_roster), filtered to
       positions eligible for position_abbreviation (_LEADER_POSITIONS)
       -- this alone is the eligibility gate; a rookie with zero recorded
       games is just as eligible as a 10-year veteran.
    3. Ranked by recent (last `window` games) volume of the position's
       stat (_leader_history) when more than one candidate is eligible.

    team_roster is an optional pre-fetched _fresh_roster result (build_
    live_event_features calls that once per team and threads it through
    all three of this function's QB/RB/WR calls for that team, instead of
    each one separately re-querying the same roster); fetched here
    directly when not given, same optional-pass-through pattern
    current_ratings/team_last_event_dates use elsewhere in this module.

    Empty list (not None) whenever no leader is found -- including when
    roster data isn't available for this team at all. Deliberately does
    NOT fall back to "whoever held this role last game": that read a
    player's team_id off their own most recent game log, which is
    exactly the stale-team-association bug roster-based selection exists
    to fix -- a team with no fresh roster data should show no candidate,
    not risk showing a wrong one. Matches build_event_features' own
    home_qb_games=None-or-list contract."""
    if position_abbreviation is not None:
        entry = _depth_chart_entry(depth_chart, position_abbreviation)
        if entry is not None:
            healthy_ids = _healthy_athlete_ids(entry, injuries)
            if not healthy_ids:
                return []
            return storage.get_player_game_stats(healthy_ids[0], before_date=before_date, limit=window)

    positions = _LEADER_POSITIONS.get(position_abbreviation)
    if positions is None:
        return []
    roster = team_roster if team_roster is not None else _fresh_roster(storage, sport, team_id)
    eligible_ids = _eligible_entity_ids(roster, positions)
    return _leader_history(storage, eligible_ids, _LEADER_VOLUME_STAT[position_abbreviation], before_date, window)


def build_live_event_features(
    storage, sport: str, event_key: str, window: int = DEFAULT_ROLLING_WINDOW,
    events: list[dict] | None = None,
) -> dict:
    """One event-level feature row for event_key, in the exact shape
    train_win_probability_model.py/train_score_model.py were trained on (label_* fields
    will be None/absent for an unplayed game -- callers only read the
    feature columns off the result, never the labels).

    `events`/team_game_stats are fetched once here and reused across this function's own
    downstream calls."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)
    events = events if events is not None else storage.get_all_events(sport)
    team_game_stats = storage.get_all_team_game_stats(sport)

    home_events = storage.get_team_events(sport, home_id, before_date=event["event_date"], limit=window, events=events)
    away_events = storage.get_team_events(sport, away_id, before_date=event["event_date"], limit=window, events=events)
    home_box = storage.get_team_game_stats_for_team(sport, home_id, before_date=event["event_date"], limit=window, team_game_stats=team_game_stats)
    away_box = storage.get_team_game_stats_for_team(sport, away_id, before_date=event["event_date"], limit=window, team_game_stats=team_game_stats)

    # Depth chart/injuries are the event's own ingest-time snapshot (see
    # library/normalize/espn.py) -- absent for any event ingested before
    # this shipped, in which case every _presumptive_leader_and_history
    # call below transparently falls through to its own roster-based
    # selection (see that function's docstring).
    home_depth_chart = event.get("home_depth_chart")
    away_depth_chart = event.get("away_depth_chart")
    home_injuries = event.get("home_injuries")
    away_injuries = event.get("away_injuries")

    # Computed once per team, threaded through all three (QB/RB/WR) of
    # that team's own _presumptive_leader_and_history calls below --
    # otherwise each one would separately re-query the same roster (same
    # redundant-recompute problem _live_elo_ratings' own current_ratings
    # threading already solves elsewhere in this module).
    home_roster = _fresh_roster(storage, sport, home_id)
    away_roster = _fresh_roster(storage, sport, away_id)

    return build_event_features(
        event,
        _live_elo_ratings(storage, sport, event, home_id, away_id, events=events),
        home_events,
        away_events,
        window,
        home_qb_games=_presumptive_leader_and_history(
            storage, sport, home_id, event["event_date"], window,
            home_depth_chart, home_injuries, "QB", home_roster),
        away_qb_games=_presumptive_leader_and_history(
            storage, sport, away_id, event["event_date"], window,
            away_depth_chart, away_injuries, "QB", away_roster),
        home_rb_games=_presumptive_leader_and_history(
            storage, sport, home_id, event["event_date"], window,
            home_depth_chart, home_injuries, "RB", home_roster),
        away_rb_games=_presumptive_leader_and_history(
            storage, sport, away_id, event["event_date"], window,
            away_depth_chart, away_injuries, "RB", away_roster),
        home_wr_games=_presumptive_leader_and_history(
            storage, sport, home_id, event["event_date"], window,
            home_depth_chart, home_injuries, "WR", home_roster),
        away_wr_games=_presumptive_leader_and_history(
            storage, sport, away_id, event["event_date"], window,
            away_depth_chart, away_injuries, "WR", away_roster),
        home_team_box_stats=home_box,
        away_team_box_stats=away_box,
    )


def _build_player_feature_row(
    storage, sport: str, event: dict, home_id: str, away_id: str, entity_id: str, team_id: str,
    prior_games: list[dict], window: int,
    current_ratings: dict | None = None, team_last_event_dates: dict[str, str] | None = None,
    events: list[dict] | None = None,
) -> dict:
    """Shared by build_live_player_features (one specific requested
    player) and build_live_event_leader_candidates (every candidate
    likely to lead a team in some category) -- both already know
    team_id and (for the leader case) may have already fetched
    prior_games while identifying the candidate in the first place, so
    this takes them as arguments rather than re-deriving them itself.

    team_last_event_dates, like current_ratings, is an optional
    already-computed lookup (handler.py's _leaderboards builds one team's
    worth of "last completed game date" once from the same completed-events
    fetch _season_standings_inputs already does) -- when given, this skips
    its own storage.get_team_events call, the other per-candidate query
    this function would otherwise repeat once per player per stat."""
    if team_last_event_dates is not None:
        own_previous_event_date = team_last_event_dates.get(team_id)
    else:
        own_previous_events = storage.get_team_events(sport, team_id, before_date=event["event_date"], limit=1, events=events)
        own_previous_event_date = own_previous_events[0]["event_date"] if own_previous_events else None

    player_game = {
        "event_key": event["event_key"],
        "player_key": player_key(sport, entity_id),
        "entity_id": entity_id,
        "team_id": team_id,
        "event_date": event["event_date"],
        "stat_line": {},  # unknown -- this is what's being predicted, not labeled
    }

    return build_player_features(
        player_game,
        prior_games,
        event,
        _live_elo_ratings(storage, sport, event, home_id, away_id, current_ratings, events),
        own_previous_event_date,
        window,
    )


def build_live_player_features(
    storage, sport: str, event_key: str, entity_id: str, window: int = DEFAULT_ROLLING_WINDOW,
    current_ratings: dict | None = None, team_last_event_dates: dict[str, str] | None = None,
    events: list[dict] | None = None,
) -> dict:
    """One player-prop feature row for entity_id's performance in
    event_key, in the exact shape train_player_prop_model.py was trained
    on. team_id comes from the player's own entity record (metadata.team_id,
    kept current by every normalize.py upsert), not from the event or
    their own last game log, since a just-traded player's most recent
    game log would still show their old team.

    current_ratings/team_last_event_dates/events are optional already-computed
    pass-throughs for a caller looping over many players (handler.py's season leaderboards)."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)

    entity = storage.get_entity(sport, entity_id)
    if entity is None:
        raise EventNotFoundError(f"No entity found for {entity_id}")
    team_id = entity.get("metadata", {}).get("team_id")

    prior_games = storage.get_player_game_stats(entity_id, before_date=event["event_date"], limit=window)
    return _build_player_feature_row(
        storage, sport, event, home_id, away_id, entity_id, team_id, prior_games, window,
        current_ratings, team_last_event_dates, events,
    )


def build_live_event_leader_candidates(
    storage, sport: str, event_key: str, window: int = DEFAULT_ROLLING_WINDOW,
    events: list[dict] | None = None,
) -> dict:
    """One feature row per candidate likely to lead each team in
    passing/receiving/rushing/sacks for event_key, grouped
    {"home": {"passing": [row], "receiving": [row, row, row],
    "rushing": [row, row], "sacks": [row, row, row]}, "away": {...}} --
    each row is build_player_features' usual output (carries entity_id),
    same as build_live_player_features. Deliberately doesn't touch S3 or
    load any model -- scoring these against the right player-prop model
    per category is the caller's job (handler.py), same separation
    build_live_player_features/model_loader.py already have.

    Computes current_ratings once here and threads it through every candidate (up to ~15
    per event) instead of each one recomputing it."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)

    events = events if events is not None else storage.get_all_events(sport)
    _, current_ratings = compute_elo_ratings(events, as_of_season=event.get("season"))

    return {
        "home": _team_leader_candidates(
            storage, sport, event, home_id, away_id, home_id, window, current_ratings,
            event.get("home_depth_chart"), event.get("home_injuries"), events=events,
        ),
        "away": _team_leader_candidates(
            storage, sport, event, home_id, away_id, away_id, window, current_ratings,
            event.get("away_depth_chart"), event.get("away_injuries"), events=events,
        ),
    }


def _top_n_by_recent_volume(
    storage, entity_ids: list[str], stat: str, before_date: str, window: int, n: int,
) -> dict[str, list]:
    """entity_id -> most recent `window` games, for the top n of
    entity_ids ranked by that same window's volume of stat -- the multi-
    candidate counterpart to _leader_history, reusing the same
    fetch-once-use-for-both approach (_recent_volume) so ranking and the
    returned feature history share one query per candidate. {} if
    entity_ids is empty."""
    if not entity_ids:
        return {}
    volumes = {entity_id: _recent_volume(storage, entity_id, stat, before_date, window) for entity_id in entity_ids}
    ranked = sorted(volumes, key=lambda entity_id: volumes[entity_id][0], reverse=True)[:n]
    return {entity_id: volumes[entity_id][1] for entity_id in ranked}


def _team_leader_candidates(
    storage, sport: str, event: dict, home_id: str, away_id: str, team_id: str, window: int,
    current_ratings: dict | None = None, depth_chart: dict | None = None, injuries: list[dict] | None = None,
    events: list[dict] | None = None,
) -> dict:
    before_date = event["event_date"]
    last_events = storage.get_team_events(sport, team_id, before_date=before_date, limit=1, events=events)
    # This team's own previous-game date, when known -- reusing it here
    # (instead of letting each candidate's own _build_player_feature_row
    # call separately re-derive it via its own storage.get_team_events
    # call) is the second half of this function's own perf fix, alongside
    # current_ratings. None (not a missing key) when the team has no
    # prior game yet -- _build_player_feature_row falls back to its own
    # per-candidate lookup in that case.
    team_last_event_dates = {team_id: last_events[0]["event_date"]} if last_events else None

    roster = _fresh_roster(storage, sport, team_id)

    def rows_for(histories: dict[str, list]) -> list[dict]:
        return [
            _build_player_feature_row(
                storage, sport, event, home_id, away_id, entity_id, team_id, prior_games, window,
                current_ratings, team_last_event_dates, events,
            )
            for entity_id, prior_games in histories.items()
        ]

    def candidates_for(position_abbreviation: str, n: int) -> dict[str, list]:
        # Broader than a single slot -- same net _LEADER_POSITIONS already
        # uses for roster-fallback eligibility (a scrambling QB competing
        # for rushing candidacy, a pass-catching RB or the starting TE
        # competing for receiving candidacy), now applied to the
        # depth-chart path too so it isn't only the roster-fallback that
        # considers them.
        entries = _depth_chart_entries(depth_chart, _LEADER_POSITIONS[position_abbreviation])
        if entries:
            # WR/receiving takes everyone listed across every eligible
            # slot rather than capping at n -- depth-chart rank isn't a
            # reliable cross-slot ranking (see _healthy_athletes_across_
            # slots' own docstring), so this leaves the actual per-
            # candidate model predictions to do that ranking downstream
            # instead of guessing who to cut before they're even scored.
            cap = None if position_abbreviation == "WR" else n
            ids = _healthy_athletes_across_slots(entries, injuries, cap)
            return {eid: storage.get_player_game_stats(eid, before_date=before_date, limit=window) for eid in ids}
        eligible_ids = _eligible_entity_ids(roster, _LEADER_POSITIONS[position_abbreviation])
        return _top_n_by_recent_volume(
            storage, eligible_ids, _LEADER_VOLUME_STAT[position_abbreviation], before_date, window, n,
        )

    passing_rows = rows_for(candidates_for("QB", 1))
    receiving_rows = rows_for(candidates_for("WR", 3))
    rushing_rows = rows_for(candidates_for("RB", 2))
    # Sacks has no depth-chart source (ESPN's depth chart is
    # offense/defense-position-based, not a "top pass rushers" list) --
    # restricted to defensive positions specifically (see
    # _DEFENSIVE_POSITIONS' own comment for why).
    sacks_ids = _eligible_entity_ids(roster, _DEFENSIVE_POSITIONS)
    sacks_rows = rows_for(_top_n_by_recent_volume(storage, sacks_ids, "defensive_sacks", before_date, window, 3))

    return {"passing": passing_rows, "receiving": receiving_rows, "rushing": rushing_rows, "sacks": sacks_rows}
