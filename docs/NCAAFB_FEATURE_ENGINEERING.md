# NCAAFB Feature Engineering

Describes what NCAAFB's model-training features are, how they're computed, and where each field comes from. Covers the same ground `DATA_SCHEMA.md` covers for storage -- this doc is specifically about the derived training data, not the raw DynamoDB/S3 schema it's built from.

Code lives in three places:
- `Source/library/features/ncaafb.py` -- pure feature-computation functions (no AWS calls). `build_event_features`, `build_player_features`, `build_team_week_features`.
- `Source/library/features/common.py` -- sport-agnostic primitives (Elo, rolling averages, streaks, rest days) shared with NFL.
- `Source/feature-engineering/ncaafb/build_dataset.py` -- the Fargate entrypoint that walks DynamoDB history (via `FeatureStorage`), calls the functions above once per event/player-game/team-week, and writes three Parquet files to S3.

Mirrors NFL's architecture (`library/features/nfl.py` + `feature-engineering/nfl/build_dataset.py`) wherever CFBD's data supports it, and diverges where it genuinely doesn't -- each divergence is called out below. **Not every NFL feature has an NCAAFB counterpart** -- where no real CFBD data source exists for something NFL feature-engineers, NCAAFB simply doesn't produce that column at all, rather than carrying it as a permanently-null placeholder for schema parity (see "What's deliberately absent").

## Pipeline

```
DynamoDB (entities, events, player_game_stats, team_game_stats)
        |  FeatureStorage (read-only)
        v
feature-engineering/ncaafb/build_dataset.py
        |  build_event_dataset()      -> event_features.parquet
        |  build_player_dataset()     -> player_features.parquet
        |  build_ranking_dataset()    -> ranking_features.parquet
        v
S3: s3://<model-artifacts-bucket>/ncaafb/training-data/*.parquet
        v
model-training/ncaafb/train_*.py (reads the Parquet, trains, versions an artifact)
```

Not scheduled -- run manually via `aws ecs run-task` (`Terraform/ecs-task-ncaafb-feature-engineering.tf`) or as the first step of the training Step Function (`sfn-training-orchestrator.tf`'s `RunFeatureEngineering` state), which always runs it immediately before that sport's training tasks so every retrain works from current data. Safe to re-run any time -- it always rebuilds all three files from whatever's currently in DynamoDB and overwrites the same three S3 keys; there's no incremental state.

## The three datasets

| Dataset | Grain | One row = | Built by |
|---|---|---|---|
| `event_features.parquet` | event | one head-to-head game | `build_event_dataset` + `build_event_features` |
| `player_features.parquet` | player-game | one player's stat line in one game | `build_player_dataset` + `build_player_features` |
| `ranking_features.parquet` | team-week | one team's state heading into one of its own games | `build_ranking_dataset` + `build_team_week_features` |

All three are built by walking every completed NCAAFB event in chronological order exactly once, growing each team's (or player's, or team-per-season's) own history incrementally as the walk proceeds -- an event's features only ever see that team/player's games *before* it, never its own outcome or anything in the future. This is what prevents label leakage: the same discipline `compute_elo_ratings`'s `pre_game_ratings` and every rolling-average function already enforce.

## Shared primitives (`library/features/common.py`)

- **Elo ratings** (`compute_elo_ratings`) -- one running rating per team, updated after every game, margin-of-victory scaled, regressed 1/3 toward the mean at each season boundary. Every feature row uses each team's *pre-game* rating (`home_pre_rating`/`away_pre_rating`), never the post-game one.
- **Rolling team scoring averages** (`rolling_team_scoring_averages`) -- points scored/allowed, averaged over a team's own last N completed games (N = `ROLLING_WINDOW`, default 5) for the event/player datasets; over a team's full season-to-date for the ranking dataset (see below).
- **Rolling player stat averages** (`rolling_player_stat_averages`) -- for every `stat_line` key that appears in at least one of a player's last N games, the average of that key over the games that have it, plus `games_with_<stat>` (how many of those N games actually recorded it) and `games_played`/`starts`.
- **Current streak** (`current_streak`) -- positive = win streak length, negative = loss streak length, 0 if no history or the last game was a tie.
- **Rest days** (`rest_days`) -- days since a team's/player's previous game.

## `event_features.parquet` fields

One row per completed event, home/away perspective.

| Field | Description |
|---|---|
| `event_key`, `event_date`, `home_entity_id`, `away_entity_id` | Identifiers -- excluded from model inputs by every `train_*.py` script. |
| `week`, `season_type` | From the event itself (`"regular"`/`"postseason"`). |
| `kickoff_hour_utc` | Parsed from `kickoff_time` (`library.features.common.kickoff_hour_utc`). |
| `venue_indoor` | From the home team's own venue (CFBD `location.dome`, attached at ingest time). |
| `home_elo`, `away_elo`, `elo_diff` | Pre-game Elo ratings. |
| `home_rest_days`, `away_rest_days` | Days since each team's own previous game. |
| `home_avg_points_scored/allowed`, `away_avg_points_scored/allowed`, `*_games_played` | Rolling team scoring, last `ROLLING_WINDOW` games. |
| `home_qb_avg_passing_yards/tds/interceptions`, `*_games_played` | Rolling stats of the identified starting QB (see "Leader identification" below), home and away. |
| `home_rb_avg_rushing_yards/tds`, `home_wr_avg_receiving_yards/tds/receptions`, `*_games_played` | Same pattern for the identified lead rusher/receiver. |
| `home_avg_turnovers/total_yards/possession_time_seconds/penalties/penalty_yards`, `home_third_down_pct`, `home_box_games_played` (+ away) | Rolling team box-score stats. **No `red_zone_pct`** -- confirmed live CFBD's team box score has no red-zone category at all (NFL's equivalent field doesn't exist here). |
| `is_conference_game` | CFBD's own `conference_game` flag, passed through as-is (already season-scoped -- see "Conference/bowl/playoff" below). |
| `is_bowl_game`, `is_playoff_game` | Derived from CFBD's own `playoff` field -- see below. |
| `home_travel_km`, `away_travel_km` | See "Travel distance" below. |
| `home_win_streak`, `away_win_streak` | Current streak entering this game. |
| `home_coach_experience`, `home_coach_season_win_pct` (+ away) | From CFBD's `/coaches`, attached at ingest time. **No `career_playoff_win_pct`** -- CFBD folds bowl/playoff results into the same season win-loss total a coach's record already reports, so that figure doesn't exist to surface. |
| `home_current_rank`, `away_current_rank` | That week's AP Top 25 rank if either team was ranked (CFBD `/rankings`, attached at ingest time), else absent/None. |
| `label_home_won`, `label_home_score`, `label_away_score` | Training labels (win-probability + all three score targets share this one dataset). |

**No injury-related columns exist here at all** -- see "What's deliberately absent" below.

## `player_features.parquet` fields

One row per player-game.

| Field | Description |
|---|---|
| `event_key`, `player_key`, `entity_id`, `team_id`, `opponent_id`, `event_date` | Identifiers. |
| `avg_<stat>`, `games_with_<stat>`, `games_played`, `starts` | This player's own rolling stats entering the game (see `rolling_player_stat_averages`) -- column set varies by position, unioned across all rows when written to Parquet. |
| `is_home`, `week`, `season_type`, `kickoff_hour_utc`, `venue_indoor` | Same meaning as the event-level fields, from this player's own game's event. |
| `rest_days` | Since this player's own team's previous game. |
| `own_elo`, `opponent_elo`, `elo_diff` | Pre-game Elo, from the player's own team's perspective. |
| `is_conference_game`, `is_bowl_game`, `is_playoff_game` | Same derivation as the event-level fields. |
| `travel_km` | This player's own team's travel distance for this game. |
| `label_stat_line` | JSON-encoded dict of this game's actual stat line -- the label. `train_player_prop_model.py` picks one key out of it per `TARGET_STAT`. |
| `label_started` | Whether this player started. |

### Leader identification (which player represents "the QB"/"the RB"/"the WR")

Same volume-based approach as NFL, but CFBD's stat names are different, not just relabeled:

| Position | NFL volume stat | NCAAFB volume stat | Why different |
|---|---|---|---|
| QB | `passing_attempts` | `passing_attempts` | Same name, but CFBD reports it as a compound `"C/ATT": "24/38"` string that has to be split first (see `library/normalize/ncaafb.py`'s `_PLAYER_STAT_COMPOUND_SPLITS`) -- unlike ESPN, which reports attempts as its own field. |
| RB | `rushing_attempts` | `rushing_attempts` | CFBD's raw type is `CAR` (carries); `_STAT_TYPE_NAMES` maps it to `"attempts"` so the field name matches NFL's convention. |
| WR | `receiving_targets` | `receiving_receptions` | **CFBD has no targets stat at all.** Receptions is the volume signal instead -- a deliberate divergence, not a bug. |

## `ranking_features.parquet` fields

One row per team-week -- the input to the National Ranking (1-25) model, a genuinely new model shape with no event-level or player-level equivalent. Built by walking events chronologically and emitting one row per side per event (so a home/away pair produces two ranking rows, one per team), using that team's own season-to-date history, not a trailing N-game window.

| Field | Description |
|---|---|
| `event_key`, `team_id`, `event_date`, `season`, `season_type` | Identifiers -- excluded from features (`season`/`season_type` are also non-numeric/non-generalizing, so `train_ranking_model.py` excludes them explicitly). |
| `week` | Kept as a feature (numeric, meaningful -- how far into the season this snapshot falls). |
| `conference` | This team's own conference for the season (from the event's `home_conference`/`away_conference`). Excluded from features -- a raw string, not model-consumable without encoding; strength-of-schedule (below) already captures relative conference quality numerically. |
| `elo` | This team's own pre-game Elo entering this week's game. |
| `wins`, `losses` | Season-to-date record (ties count toward neither). |
| `games_played` | Season-to-date game count. |
| `avg_points_scored`, `avg_points_allowed` | Season-to-date scoring average (not a trailing window). |
| `win_streak` | Current streak entering this week. |
| `strength_of_schedule` | Average pre-game Elo of every opponent faced so far this season. |
| `label_current_rank` | That week's AP Top 25 rank if CFBD's rank enrichment attached one to this team's game, else `None` -- an unranked team-week has no well-defined numeric label, so `train_ranking_model.py` filters these rows out before training (same "missing, not fabricated" rule used everywhere else in this project). |

At serving time (a later phase), every FBS team's predicted rank is computed and sorted; the 25 lowest predicted values become the projected Top 25 -- no separate is-ranked classifier.

## Conference / bowl / playoff derivation

NFL hardcodes a static `TEAM_DIVISIONS` table (stable since 2002). FBS conference membership realigns almost every year, so NCAAFB can't do that -- instead:

- `is_conference_game` reads CFBD's own `conference_game` flag directly, already computed and season-scoped by CFBD itself.
- `is_playoff_game` is derived once, at normalize time (`library/normalize/ncaafb.py`'s `game_to_event_item`), from whether CFBD's own `playoff` field is populated -- confirmed live that it's a real object (`{"competition": "cfp", "round": ...}`) only for actual 12-team CFP games, and `null` for every other game including ordinary bowls. Cleaner and more reliable than string-matching the bowl-name `notes` field.
- `is_bowl_game` is derived in the feature layer: `season_type == "postseason"` and *not* `is_playoff_game`.

## Travel distance

NFL hand-maintains a static `TEAM_COORDINATES` table (`library/features/nfl_teams.py`) since ESPN's data has no coordinate field. CFBD's `/teams` response carries each team's home-stadium `location.latitude`/`location.longitude` directly (confirmed live) -- so NCAAFB sources coordinates dynamically instead of hardcoding a second table that would drift as realignment happens: `team_to_entity` (`library/normalize/ncaafb.py`) stores `latitude`/`longitude` on every team entity, and `build_dataset.py` resolves a `{team_id: (lat, lon)}` dict via one `FeatureStorage.get_entity` call per team encountered (~136 for FBS, trivial, no new IAM needed -- `GetItem` on the entities table was already granted).

`build_event_features`/`build_player_features` take this dict as an explicit `team_coordinates` argument rather than importing a module constant -- the one real signature difference from NFL's equivalent functions.

**Known v1 limitation**: no international/neutral-site venue table exists yet (unlike NFL's `INTERNATIONAL_VENUES`). A true neutral-site CFB game (e.g. a Dublin/Ireland matchup) still computes travel as if the "home" team were at its own market -- a known imprecision, same spirit as weather being deferred to a future paid-tier upgrade, not a silent bug.

## What's deliberately absent

Where no real CFBD (or any other) data source exists, NCAAFB simply doesn't feature-engineer that thing -- no permanently-null placeholder columns kept around just to match NFL's schema shape:

- **No injury fields at all.** No mandated structured injury reporting exists for college football, so `build_event_features` never computes `home_qb_injury_status`/`away_qb_injury_status`/`home_team_injury_count`/`away_team_injury_count` (or any equivalent) -- those columns simply don't exist in `event_features.parquet`, unlike NFL where they're real, populated features.
- **No depth charts.** Live (pre-game) candidate selection for a not-yet-played game isn't built yet (a later phase); when it is, it'll fall back to `rank_by_average_stat`-style historical-volume ranking rather than a depth-chart feed, since none exists for CFB.
- **No weather.** `venue_indoor` is available; `weather_temperature` is not (CFBD's weather data is a paid-tier-only feature) -- omitted entirely, same reasoning as injuries.
- **No `career_playoff_win_pct`** for coaches -- CFBD folds bowl/playoff results into the same season win-loss total a coach's record already reports, so that figure doesn't exist to derive.
- **No `red_zone_pct`** -- confirmed live CFBD's team box score has no red-zone category at all.

## Player-prop training targets

Same 7 stats as NFL, values verified against CFBD's actual field names (`Terraform/dynamodb-sport-registry.tf`'s `ncaafb_player_prop_stats`): `passing_yards`, `passing_touchdowns`, `rushing_yards`, `rushing_touchdowns`, `receiving_yards`, `receiving_touchdowns`, `defensive_sacks`. Unlike NFL/ESPN, CFBD's `passing` category has no `SACKS` type at all -- `defensive_sacks` only ever comes from the `defensive` category, so there's no ambiguity to guard against the way `train_player_prop_model.py`'s `OFFENSIVE_CATEGORIES`/`DEFENSIVE_CATEGORIES` split has to for NFL (CFBD's `DEFENSIVE_CATEGORIES` is just `{"defensive"}`, no separate bare `"interceptions"` category).
