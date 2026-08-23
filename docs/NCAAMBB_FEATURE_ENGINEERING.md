# NCAA MBB Feature Engineering

Describes what NCAA MBB's model-training features are, how they're computed, and where each field comes from. Covers the same ground `design/DATA_SCHEMA.md` covers for storage -- this doc is specifically about the derived training data, not the raw DynamoDB/S3 schema it's built from.

**Status: fully built and live (Sub-phase 3B steps 1-8).** Ingest/normalize/schedule-sync/backfill, feature engineering + training, inference (`predict`/`predict-read`), live-scores, and season simulation (both a March Madness bracket and one bracket per conference tournament) are all deployed. Only frontend activation (step 9 -- `sport_config.dart`'s `id` is still the mismatched `ncaa_mbb` and `active: false`) and a final CI/config sweep (step 10) remain -- see the `project-ncaambb-onboarding` memory for build-order status.

Code lives in three places:
- `Source/library/features/ncaambb.py` -- pure feature-computation functions (no AWS calls). `build_event_features`, `build_player_features`, `build_team_week_features`.
- `Source/library/features/common.py` -- sport-agnostic primitives (Elo, rolling averages, streaks, rest days, possessions/efficiency) shared with NBA/NFL/NCAAFB.
- `Source/feature-engineering/ncaambb/build_dataset.py` -- the Fargate entrypoint that walks DynamoDB history (via `FeatureStorage`), calls the functions above once per event/player-game/AP-poll appearance, and writes three Parquet files to S3.

Mirrors NBA's architecture (`library/features/nba.py` + `feature-engineering/nba/build_dataset.py`) wherever ESPN's NCAA MBB data supports it, and diverges where the sport genuinely doesn't share NBA's shape -- each divergence is called out below. **This is not a copy-paste of NBA's feature set**, and shares NCAAFB's poll-driven national-ranking model shape more than NBA's (which has none).

## Pipeline

```
DynamoDB (entities, events, player_game_stats, team_game_stats)
        |  FeatureStorage (read-only)
        v
feature-engineering/ncaambb/build_dataset.py
        |  build_event_dataset()      -> event_features.parquet
        |  build_player_dataset()     -> player_features.parquet
        |  build_ranking_dataset()    -> ranking_features.parquet   (reads raw AP polls from S3 too)
        v
S3: s3://<model-artifacts-bucket>/ncaambb/training-data/*.parquet
        v
model-training/ncaambb/train_*.py (reads the Parquet, trains, versions an artifact)
```

Not scheduled -- run manually via `aws ecs run-task` (`Terraform/ecs-task-ncaambb-feature-engineering.tf`) or as the first step of the training Step Function (`sfn-training-orchestrator.tf`'s `RunFeatureEngineering` state), which always runs it immediately before that sport's training tasks so every retrain works from current data. Safe to re-run any time -- it always rebuilds all three files from whatever's currently in DynamoDB/the raw AP-poll prefix and overwrites the same three S3 keys; there's no incremental state.

## The three datasets

| Dataset | Grain | One row = | Built by |
|---|---|---|---|
| `event_features.parquet` | event | one head-to-head game | `build_event_dataset` + `build_event_features` |
| `player_features.parquet` | player-game | one player's box-score line in one game | `build_player_dataset` + `build_player_features` |
| `ranking_features.parquet` | team-poll | one team's season-to-date state as of one AP poll's release date | `build_ranking_dataset` + `build_team_week_features` |

Unlike NBA's own `build_dataset.py`, there's no exhibition/All-Star-style filter here -- NCAA MBB has no preseason concept to exclude (confirmed live), and NIT/other secondary-postseason-tournament games are deliberately **included** as real training data, not filtered out (a real competitive elimination tournament, unlike an exhibition).

All three are built by walking history in chronological order exactly once, growing each team's (or player's) own history incrementally as the walk proceeds -- an event's features only ever see that team/player's games *before* it, never its own outcome or anything in the future. Same label-leakage discipline `compute_elo_ratings`'s `pre_game_ratings` and every rolling-average function already enforce.

## Shared primitives (`library/features/common.py`)

Same set NBA uses -- Elo ratings (`compute_elo_ratings`, left at the NFL-tuned defaults, same open tuning item every basketball sport currently carries), rolling team scoring averages, rolling player/team stat averages, current streak, rest days, and the Dean Oliver possession/efficiency helpers (`estimate_possessions`, `_efficiency_per_100` -- promoted here from NBA's own copy, per the shared-math convention this project follows for genuinely sport-agnostic formulas).

## `event_features.parquet` fields

One row per completed event, home/away perspective. Field names and derivations are the same as `NBA_FEATURE_ENGINEERING.md`'s own table (`home_elo`/`away_elo`/`elo_diff`, `*_rest_days`, `*_avg_points_scored/allowed`, `*_avg_offensive_rebounds/defensive_rebounds/assists/steals/blocks/turnovers/fouls`, `*_field_goal_pct`/`*_three_point_pct`/`*_free_throw_pct`, `*_offensive_efficiency`/`*_defensive_efficiency`, `*_win_streak`, `*_team_injury_count`, `label_home_won`/`label_home_score`/`label_away_score`), with these genuine differences:

| Field | Difference from NBA |
|---|---|
| `home_avg_rebounds`/`away_avg_rebounds` | Read **directly** off `avg_total_rebounds` -- unlike NBA, ESPN's NCAA MBB box score carries a real combined-rebounds stat (labeled "Total Rebounds"), so there's no need for NBA's offensive+defensive derivation workaround. |
| `is_conference_game` | Replaces NBA's `is_divisional_game` -- ESPN's own `conferenceCompetition` flag (`library/normalize/espn.py`'s `scoreboard_event_to_event_item`), true for both a regular-season conference game and a conference-tournament game. No static conference table needed (real yearly D1 realignment makes a hand-maintained table the wrong tool -- see "Conference membership" below). |
| — | **No `travel_km`/`is_international_game` at all.** Confirmed live: ESPN has no geo-coordinates anywhere for NCAA MBB teams or venues (only city/state text), unlike NCAAFB's CFBD source or NBA's own static 30-team table. Hand-typing coordinates for ~362 D1 teams would repeat, at 12x the scale, the exact "2 of NBA's 30 hand-typed ids were wrong until checked individually" risk already flagged for NBA -- dropped rather than guessed. |

**No QB/RB/WR-style leader sub-features, no coach-tenure features, no `venue_indoor`** -- same reasoning as NBA's own doc (no single dominant position in basketball; no coach data source; every D1 arena is indoor).

## `player_features.parquet` fields

Same shape as NBA's own table (`avg_<stat>`/`games_with_<stat>`/`games_played`/`starts`, `is_home`, `kickoff_hour_utc`, `rest_days`, `own_elo`/`opponent_elo`/`elo_diff`, `is_conference_game` in place of NBA's divisional/international flags, `label_stat_line`, `label_started`). No `season_type`/`week` columns -- NCAA MBB's schedule is date-based, not week-based, same as NBA's.

## `ranking_features.parquet` fields

One row per team-poll -- the input to the National Ranking model, same target shape as NCAAFB's own (predicting AP Top 25 rank), but **poll-centric, not event-centric**: NCAA MBB's AP polls aren't attached to individual events the way CFBD's rank data is, so `build_ranking_dataset` reads raw poll JSON directly from the raw data lake bucket (`ncaambb/rankings/{season}/{season_type}/{week}.json`, written by `data-backfills/ncaambb/backfill.py`'s `seed_rankings` and kept current by daily ingest) and builds one row per (team, poll) the poll actually ranked, rather than one row per (team, event) filtered to ranked-only the way NCAAFB's does. D1's ~362 teams vs. NCAAFB's ~130 FBS teams means the event-centric approach would build a mostly-wasted unranked row for every team at every event (only ~25 of 362 teams are ever ranked by any one poll).

| Field | Description |
|---|---|
| `team_id`, `as_of_date`, `season` | Identifiers -- `as_of_date` is the poll's own release date, not derived from the season/week path components. |
| `elo` | Approximated "as of" the poll date -- Elo only exists as pre-game snapshots, so this anchors to the team's most recent event on/before the poll date (or its earliest event after, for a preseason poll), a known small-lag approximation (see `_resolve_own_elo`'s own docstring). |
| `wins`, `losses`, `games_played` | Season-to-date, strictly before `as_of_date`. |
| `avg_points_scored`, `avg_points_allowed` | Season-to-date scoring average (not a trailing window). |
| `win_streak` | Current streak entering this poll's date. |
| `strength_of_schedule` | Average pre-game Elo of every opponent faced so far this season. |
| `label_current_rank` | This poll's rank for this team -- `train_ranking_model.py` never sees an unranked row at all here (unlike NCAAFB, which filters them out at train time), since only ranked (team, poll) pairs are ever built. |

## Conference membership

Neither NBA's static table nor NCAAFB's per-event CFBD fields fit here (ESPN's generic normalizer carries no conference reference field on an NCAA MBB event). Resolved instead via `library/http/ncaambb_core.py`'s `resolve_conference_membership`, called live once daily by `ncaambb-schedule-sync` (the one NCAA MBB Lambda with ordinary internet egress) and cached to `ncaambb/conference-membership/{season}.json` in the raw bucket -- **not** looked up live by any feature-engineering or inference code, since `ncaambb-predict` is VPC-isolated with no internet route at all (see "Networking constraint" in `design/ARCHITECTURE.md`). `is_conference_game` above doesn't need this cache (ESPN's own flag covers it); the cache instead drives conference standings/bracket seeding in `season_projection.py`.

## `season.type`/postseason classification

Verified live against real ESPN payloads, not assumed by pattern:
- **Conference-tournament games**: `season.type == 2` (same as regular season) **and** `conferenceCompetition == true` **and** a non-empty `notes` array (a regular-season conference game has `conferenceCompetition: true` but empty `notes`).
- **NCAA-tournament (March Madness) games**: `season.type == 3`, `conferenceCompetition == false`, **and** `notes[0].headline` starts with `"Men's Basketball Championship - "`. The type/flag combination alone is **not** sufficient -- the NIT (and likely CBI/CIT) shares the exact same `type=3`/`conferenceCompetition=false` signature; only the `notes` headline text tells them apart. `_is_march_madness_game` (`Source/aws-lambdas/ncaambb/predict/season_projection.py`) filters on the headline explicitly for this reason.

## Player-prop training targets

6 stats, live-verified against ESPN's real NCAA MBB box-score field names (`Terraform/dynamodb-sport-registry.tf`'s `ncaambb_player_prop_stats`) -- the same 6 names NBA uses: `points`, `rebounds`, `assists`, `steals`, `blocks`, `three_pointers_made`. `turnovers` and `free_throws_made` are kept as rolling-average feature inputs only, not trained targets -- same call NBA made, same reasoning (real signal, not worth a dedicated model).

## Models trained on these features

`Source/model-training/ncaambb/` -- same shared plumbing as every other sport (`library/ml/training_common.py`, `library/ml/backtest.py`). Every target's `CANDIDATES` list includes the same 5 algorithm families as NBA/NCAAFB's national-ranking model (XGBoost, logistic regression/random forest/MLP, and LightGBM classifier/regressor pair) -- see `library/ml/model_types.py`.

| Predicted value | Model type(s) trained & evaluated | Training script |
|---|---|---|
| Home team win probability (`label_home_won`) | 5-way classifier tournament on `log_loss` | `train_win_probability_model.py` |
| Game score -- margin, home score, or away score | 5-way regressor tournament on `rmse`, one model per `SCORE_TARGET` (`margin`, `home_score`, `away_score`) | `train_score_model.py` |
| Player stat props (`points`, `rebounds`, `assists`, `steals`, `blocks`, `three_pointers_made`) | 5-way regressor tournament, one model per `TARGET_STAT`. Same `MIN_PRIOR_GAMES_WITH_STAT`/`MIN_AVG_FRACTION_OF_MEDIAN`/`MIN_NON_NULL_FRACTION` filtering as NBA -- NCAA MBB's flat `stat_line` (no category prefixes) needs no NFL-style offensive/defensive column exclusion either | `train_player_prop_model.py` |
| National Ranking (AP Top 25, `label_current_rank`) | 5-way regressor tournament on `rmse`/`mae` -- feeds March Madness/conference-tournament field seeding, not served directly as its own route | `train_ranking_model.py` |

**Hyperparameter search**: every `RandomizedSearchCV`-based candidate (all except logistic regression) runs through `library/ml/model_types.py`'s batched early-stopping wrapper (`_run_randomized_search_with_early_stopping`) rather than one fixed-size search -- stops once a batch's best score stops meaningfully improving (`_EARLY_STOP_PATIENCE_BATCHES` consecutive non-improving batches, `_EARLY_STOP_MIN_RELATIVE_IMPROVEMENT` threshold), instead of always spending the full iteration ceiling. This is what let Random Forest's and LightGBM's ceilings (`_RF_SEARCH_ITERATIONS`/`_LGBM_SEARCH_ITERATIONS`) be raised safely -- real convergence evidence, not just a bigger fixed budget. Sport-agnostic (lives in the shared library), not NCAA MBB-specific, but NCAA MBB's own training runs were what surfaced the convergence data that motivated it.

**Scheduling**: registry-driven, same as every sport onboarded after Phase 4 -- `Terraform/dynamodb-sport-registry.tf`'s `ncaambb_registry` row lists all of the above as `training_targets`, picked up automatically by the training orchestrator's monthly Step Function run. No NCAA MBB-specific scheduler Terraform needed.

## What this makes it possible to predict

**Directly, from these three datasets:**
- **Game outcomes** (win/loss), **game scores** (margin, home score, away score), **individual player stats** (points, rebounds, assists, steals, blocks, three-pointers made), and each team's **current AP-style rank**.

**Simulated, not separately feature-engineered** (`Source/aws-lambdas/ncaambb/predict/season_simulation.py`, `season_projection.py`):
- **Regular-season win/loss projections** and **conference-tournament-championship/NCAA-tournament (First Four through National Championship) probabilities** per team, run forward via Monte Carlo simulation over each team's remaining schedule.
- **One full single-elimination bracket per conference tournament**, seeded off conference-only record + point differential, real-vs-projected reconciled as real games are played -- each bracket's champion becomes that conference's automatic March Madness bid.
- **The March Madness bracket itself**: 68-team field selection (`len(conferences)` automatic bids + at-large teams ranked by the national-ranking model), First Four, 4 S-curve-seeded regions, Final Four, and Championship -- all one flattened, real-vs-projected-reconciled bracket, using the same generic `project_single_elim_bracket` bracket walker the conference brackets use (the one deliberate shared-code exception to this project's per-sport-duplication convention, since both features live inside NCAA MBB alone).
