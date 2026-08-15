# NBA Feature Engineering

Describes what NBA's model-training features are, how they're computed, and where each field comes from. Covers the same ground `DATA_SCHEMA.md` covers for storage -- this doc is specifically about the derived training data, not the raw DynamoDB/S3 schema it's built from.

**Status: feature engineering + training built (Sub-phase 3A step 5), inference not live yet (step 6).** These datasets and training scripts are fully built and runnable via `aws ecs run-task`, but no model has been trained against real backfilled data yet, and the live prediction Lambdas (`Source/aws-lambdas/nba/predict/`) are still a placeholder stub -- see `design/PROJECT_PLAN.md`'s Sub-phase 3A and the `project-nba-onboarding` memory for build-order status.

Code lives in three places:
- `Source/library/features/nba.py` -- pure feature-computation functions (no AWS calls). `build_event_features`, `build_player_features`, `estimate_possessions`.
- `Source/library/features/nba_teams.py` -- static team reference data (divisions, arena coordinates, international venues) and the `is_divisional_game`/`is_international_game`/`travel_distances_km` wrappers around `library/features/geo.py`'s sport-agnostic mechanism.
- `Source/library/features/common.py` -- sport-agnostic primitives (Elo, rolling averages, streaks, rest days) shared with NFL/NCAAFB.
- `Source/feature-engineering/nba/build_dataset.py` -- the Fargate entrypoint that walks DynamoDB history (via `FeatureStorage`), calls the functions above once per event/player-game, and writes two Parquet files to S3.

Mirrors NFL's architecture (`library/features/nfl.py` + `feature-engineering/nfl/build_dataset.py`) wherever basketball's data supports it, and diverges where the sport genuinely doesn't have an NFL-shaped concept -- each divergence is called out below. **This is not a copy-paste of NFL's feature set** -- basketball has no position-leader concept the way football has a QB/RB/WR, and gains a possession/efficiency concept football has no equivalent of at all.

## Pipeline

```
DynamoDB (entities, events, player_game_stats, team_game_stats)
        |  FeatureStorage (read-only)
        v
feature-engineering/nba/build_dataset.py
        |  build_event_dataset()      -> event_features.parquet
        |  build_player_dataset()     -> player_features.parquet
        v
S3: s3://<model-artifacts-bucket>/nba/training-data/*.parquet
        v
model-training/nba/train_*.py (reads the Parquet, trains, versions an artifact)
```

Not scheduled -- run manually via `aws ecs run-task` (`Terraform/ecs-task-nba-feature-engineering.tf`) or as the first step of the training Step Function (`sfn-training-orchestrator.tf`'s `RunFeatureEngineering` state) once NBA is wired into it, which always runs it immediately before that sport's training tasks so every retrain works from current data. Safe to re-run any time -- it always rebuilds both files from whatever's currently in DynamoDB and overwrites the same two S3 keys; there's no incremental state.

## The two datasets

No third (ranking) dataset -- NBA has no in-season poll the way NCAAFB/NCAA MBB do (no `national-ranking` training target in `Terraform/dynamodb-sport-registry.tf`'s `nba_registry`), same asymmetry NCAAFB already has relative to NFL, just the other direction.

| Dataset | Grain | One row = | Built by |
|---|---|---|---|
| `event_features.parquet` | event | one head-to-head game | `build_event_dataset` + `build_event_features` |
| `player_features.parquet` | player-game | one player's box-score line in one game | `build_player_dataset` + `build_player_features` |

Both are built by walking every completed NBA event in chronological order exactly once, growing each team's (or player's) own history incrementally as the walk proceeds -- an event's features only ever see that team/player's games *before* it, never its own outcome or anything in the future. Same label-leakage discipline `compute_elo_ratings`'s `pre_game_ratings` and every rolling-average function already enforce.

## Shared primitives (`library/features/common.py`)

- **Elo ratings** (`compute_elo_ratings`) -- one running rating per team, updated after every game, margin-of-victory scaled, regressed toward the mean at each season boundary. Every feature row uses each team's *pre-game* rating. **Left at `library.features.common`'s NFL-tuned defaults** (starting rating 1500, K-factor 20, home advantage 55, MOV base/divisor 2.2/0.001) -- same as NCAAFB's own precedent (its `build_dataset.py` doesn't override them either). `design/PROJECT_PLAN.md` calls for NBA-specific tuning (higher-scoring games, different margin-of-victory behavior) eventually, but that's an empirical exercise against a real trained model's backtest results, not something to guess up front -- still an open item.
- **Rolling team scoring averages** (`rolling_team_scoring_averages`) -- points scored/allowed, averaged over a team's own last N completed games (N = `ROLLING_WINDOW`, default 5).
- **Rolling player/team stat averages** (`rolling_player_stat_averages`) -- for every `stat_line` key that appears in at least one of the last N games, the average of that key over the games that have it, plus `games_with_<stat>` and `games_played`/`starts`. Used for both player-level rolling stats and team-level rolling box-score stats (a `team_game_stats` row's `stat_line` is the same generic shape a player-game row's is).
- **Current streak** (`current_streak`) -- positive = win streak length, negative = loss streak length, 0 if no history or the last game was a tie.
- **Rest days** (`rest_days`) -- days since a team's/player's previous game.

## `event_features.parquet` fields

One row per completed event, home/away perspective.

| Field | Description |
|---|---|
| `event_key`, `event_date`, `home_entity_id`, `away_entity_id` | Identifiers -- excluded from model inputs by every `train_*.py` script. |
| `kickoff_hour_utc` | Parsed from `kickoff_time` (`library.features.common.kickoff_hour_utc`). |
| `home_elo`, `away_elo`, `elo_diff` | Pre-game Elo ratings. |
| `home_rest_days`, `away_rest_days` | Days since each team's own previous game -- more load-bearing for NBA than football, given back-to-backs are common in an 82-game season. |
| `home_avg_points_scored/allowed`, `away_avg_points_scored/allowed`, `*_games_played` | Rolling team scoring, last `ROLLING_WINDOW` games. |
| `home_avg_rebounds`, `home_avg_offensive_rebounds`, `home_avg_defensive_rebounds`, `home_avg_assists`, `home_avg_steals`, `home_avg_blocks`, `home_avg_turnovers`, `home_avg_fouls` (+ away) | Rolling team box-score averages -- direct numeric fields off `team_game_stats.stat_line` (see `library/normalize/espn.py`'s `boxscore_to_team_game_stats`). |
| `home_field_goal_pct`, `home_three_point_pct`, `home_free_throw_pct` (+ away) | Makes over attempts across the rolling window (not an average of per-game percentages) -- from the `_COMPOUND_KEY_SPLITS`-derived `*_made`/`*_attempts` pairs (`Source/aws-lambdas/nba/normalize/handler.py`). |
| `home_offensive_efficiency`, `home_defensive_efficiency` (+ away) | Points scored/allowed per 100 possessions -- **derived, not raw fields**, see "Possessions and efficiency" below. |
| `home_box_games_played` (+ away) | How much team box-score history backs the averages/percentages above. |
| `is_divisional_game` | Whether both teams are in the same division (`library/features/nba_teams.py`'s `TEAM_DIVISIONS`). |
| `is_international_game` | Whether the game was played at one of `nba_teams.INTERNATIONAL_VENUES` (Mexico City, Paris) rather than either team's home arena. |
| `home_travel_km`, `away_travel_km` | Distance each team travels to the game -- see "Travel distance" below. |
| `home_win_streak`, `away_win_streak` | Current streak entering this game. |
| `label_home_won`, `label_home_score`, `label_away_score` | Training labels (win-probability + all three score targets share this one dataset). |

**No QB/RB/WR-style leader-tracking sub-features** -- see "No position-leader tracking" below. **No coach, injury, weather, or `venue_indoor` columns** -- see "What's deliberately absent" below.

## `player_features.parquet` fields

One row per player-game.

| Field | Description |
|---|---|
| `event_key`, `player_key`, `entity_id`, `team_id`, `opponent_id`, `event_date` | Identifiers. |
| `avg_<stat>`, `games_with_<stat>`, `games_played`, `starts` | This player's own rolling stats entering the game (see `rolling_player_stat_averages`). Unlike NFL, every NBA player's `stat_line` shares the same flat key set (`points`, `rebounds`, `assists`, `steals`, `blocks`, `turnovers`, `field_goals_made`/`field_goal_attempts`, etc.) regardless of position -- there's no per-position column sparsity to union across the way a QB's passing keys and a kicker's field-goal keys never overlap for NFL. |
| `is_home`, `kickoff_hour_utc` | Same meaning as the event-level fields, from this player's own game's event. No `week`/`season_type` -- ESPN's NBA schedule has no week numbering (`Source/data-backfills/nba/backfill.py` walks calendar dates, not weeks), so there's nothing to carry through. |
| `rest_days` | Since this player's own team's previous game. |
| `own_elo`, `opponent_elo`, `elo_diff` | Pre-game Elo, from the player's own team's perspective. |
| `is_divisional_game`, `is_international_game` | Same derivation as the event-level fields. |
| `travel_km` | This player's own team's travel distance for this game. |
| `label_stat_line` | JSON-encoded dict of this game's actual stat line -- the label. `train_player_prop_model.py` picks one key out of it per `TARGET_STAT`. |
| `label_started` | Whether this player started. |

## No position-leader tracking

NFL's/NCAAFB's own `build_event_features` track each side's identified starting QB/lead rusher/lead receiver as separate rolling-history sub-features, because a single player's own performance (especially the QB) disproportionately drives a football team's outcome in a way no other position does. **Basketball has no equivalent** -- there's no single player whose box-score line dominates team outcome the way a QB's does, so `library/features/nba.py`'s `build_event_features` carries no `identify_starting_*`/per-position sub-feature machinery at all. The team-level rolling box-score averages above (shooting splits, rebounds, turnovers, efficiency) carry that signal instead, and individual players are still fully modeled -- just as their own dedicated player-prop rows (`player_features.parquet`), the same way every other sport's player-prop targets work.

`identify_scoring_leader`/`identify_rebound_leader`/`identify_assist_leader`-style helpers (named in `design/PROJECT_PLAN.md`'s Phase 3 plan) are a **serving-time** concern for the leaders panel (step 6, `Source/aws-lambdas/nba/predict/event_prediction.py`, not yet built), not a training-feature concern -- they don't exist in `library/features/nba.py` yet because nothing in feature engineering or training needs them.

## Possessions and efficiency

Basketball has no NFL/NCAAFB analog to a possessions-per-100 pace metric, so `estimate_possessions` implements Dean Oliver's standard formula:

```
possessions ≈ field_goal_attempts − offensive_rebounds + turnovers + 0.44 × free_throw_attempts
```

`build_event_features` computes each side's own rolling possessions estimate from its rolling `avg_field_goal_attempts`/`avg_offensive_rebounds`/`avg_turnovers`/`avg_free_throw_attempts`, then derives `home_offensive_efficiency`/`home_defensive_efficiency` (+ away) as `avg_points_scored`/`avg_points_allowed` per 100 of that estimate. `None` (not 0) when any rolling input is missing -- same "missing, not fabricated" discipline as every other None-propagating helper in this project, rather than silently producing a misleadingly precise efficiency number from an undefined possessions estimate.

## Travel distance and divisions

NBA is a fixed 30 franchises with a division alignment stable since 2004 -- low realignment risk, closer to NFL's hardcoded-table pattern (`nfl_teams.py`) than NCAAFB's CFBD-sourced dynamic per-season lookup. `library/features/nba_teams.py` hardcodes `TEAM_DIVISIONS` (6 divisions, 5 teams each) and `TEAM_COORDINATES` (arena/downtown-market coordinates, not exact stadium geolocation) -- every one of the 30 ESPN team ids was individually live-verified against `site.web.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{id}` before being hardcoded, not assumed from a remembered id mapping.

Coordinates are deliberately distinct from `nfl_teams.py`'s own for shared-city markets where the arenas are genuinely in a different part of the metro (Miami's Kaseya Center is downtown, the Dolphins' stadium is in Miami Gardens; Washington's Capital One Arena is downtown DC, the Commanders' stadium is in Landover, MD).

`INTERNATIONAL_VENUES` covers Mexico City and Paris (the established recent regular-season neutral-site pattern) plus London (preseason/exhibition precedent, no confirmed regular-season game in this project's backfill window as of this writing).

Unlike `build_event_features`/`build_player_features` for NCAAFB (which take a `team_coordinates` argument built dynamically from team entities, since CFBD's realignment risk is real), NBA's versions take **no** `team_coordinates` parameter -- `library/features/nba_teams.py`'s own `travel_distances_km`/`is_divisional_game`/`is_international_game` wrappers bind the static tables internally, same calling convention as NFL's.

## What's deliberately absent

Where no real data source exists (or nothing persists/enriches one yet), NBA simply doesn't feature-engineer that thing -- no permanently-null placeholder columns kept around just to match NFL's/NCAAFB's schema shape:

- **No injury fields.** NBA's roster fetch (`Source/aws-lambdas/nba/ingest/`) does carry each athlete's own `injuries` in ESPN's raw payload (confirmed live), but nothing persists or attaches it to an event the way NFL's `_enrich_events` does for its own separate injury endpoint -- this is a genuine gap, not a "no data exists" case like NCAAFB's, and worth revisiting once an enrichment pipeline exists.
- **No coach-tenure features.** Explicitly deferred, out of Sub-phase 3A's approved scope -- NFL's coach data comes from a different "core" ESPN API client NBA doesn't have wired up yet.
- **No weather, and no `venue_indoor` either.** Every NBA game is indoor, so there's no weather signal to feature-engineer in the first place (unlike NFL where `weather_temperature` is a real, if sparse, feature) -- and, unlike NFL/NCAAFB, `venue_indoor` itself isn't carried as a feature either, since it would be a constant `true` on every row and contribute nothing a model could split on.
- **No `week`/`season_type` on player-level rows.** ESPN's NBA schedule has no week numbering -- see the `player_features.parquet` field table above.
- **No National Ranking model / team-week dataset.** NBA has no in-season poll -- see "The two datasets" above.

## Player-prop training targets

6 stats, live-verified against ESPN's real NBA box-score field names (`Terraform/dynamodb-sport-registry.tf`'s `nba_player_prop_stats`): `points`, `rebounds`, `assists`, `steals`, `blocks`, `three_pointers_made`. Deliberately excluded from the target list but kept as rolling-average **feature** inputs (via `rolling_player_stat_averages`, same as every other stat_line key): `turnovers` and `free_throws_made` (real, predictive signal -- turnovers especially for usage/pace-adjusted models -- just not stats worth their own dedicated trained model), and `field_goal_attempts`/`three_point_attempts`/`free_throw_attempts` (volume/usage signal, same role NFL's `targets` plays as a feature but not a trained target). `two_pointers_made` isn't tracked as its own field at all -- ESPN reports made/attempted at the field-goal and three-point level, not a separate two-point split.

`train_player_prop_model.py` is genuinely simpler than NFL's/NCAAFB's own version: NBA's `stat_line` has no category-prefixed keys at all (confirmed live -- ESPN's single unnamed stat block, see `Source/aws-lambdas/nba/normalize/handler.py`'s own comment), so there's no NFL-style `OFFENSIVE_CATEGORIES`/`DEFENSIVE_CATEGORIES`/opposing-side-of-the-ball column exclusion to port -- `MIN_NON_NULL_FRACTION` alone does the column filtering, the same volume/magnitude filters (`MIN_PRIOR_GAMES_WITH_STAT`, `MIN_AVG_FRACTION_OF_MEDIAN`) as NFL/NCAAFB otherwise apply unchanged.

## Models trained on these features

`Source/model-training/nba/` -- same shared plumbing as NFL/NCAAFB (`library/ml/training_common.py` for loading/splitting/evaluation/artifact-writing/promotion, `library/ml/backtest.py` for the multi-algorithm tournament mechanics). Every target's `CANDIDATES` list includes **5** algorithm families, not NFL's/NCAAFB's 4 -- `LightGBMClassifierAdapter`/`LightGBMRegressorAdapter` (`library/ml/model_types.py`) were added alongside NBA's onboarding specifically for basketball's larger data volume (see `design/PROJECT_PLAN.md`'s Phase 3 model-selection section), and NBA's are the first `CANDIDATES` lists in this project to use them. No trimming yet -- this is NBA's first real training run, and the point of the newly-added `training_seconds` timing on every candidate (`library/ml/backtest.py`) is to gather the real numbers that later decide whether/how to trim a target's candidate list, not to guess ahead of having them.

| Predicted value | Model type(s) trained & evaluated | Features used |
|---|---|---|
| Home team win probability (`label_home_won`) | `train_win_probability_model.py` -- XGBoost, logistic regression, random forest, MLP, and LightGBM classifiers compete on `log_loss`; the winner is versioned under `nba/win-probability/` | Every event-level feature in the table above |
| Game score -- margin, home score, or away score, one model per target | `train_score_model.py` -- `SCORE_TARGET` environment variable (`margin`, `home_score`, or `away_score`) at `aws ecs run-task` time (`Terraform/ecs-task-nba-train-score-model.tf`), versioned independently under `nba/score-margin/`, `nba/home-score/`, `nba/away-score/`. Same 5-candidate regressor tournament, scored on `rmse` | The identical event-level feature set as win probability |
| Player stat props -- one model per stat (`points`, `rebounds`, `assists`, `steals`, `blocks`, `three_pointers_made`) | `train_player_prop_model.py` -- `TARGET_STAT` environment variable at `aws ecs run-task` time (`Terraform/ecs-task-nba-train-player-prop-model.tf`). Same 5-regressor tournament. Dataset filtering identical to NFL's (`MIN_PRIOR_GAMES_WITH_STAT`, `MIN_AVG_FRACTION_OF_MEDIAN`), minus the opposing-side-of-the-ball exclusion -- see "Player-prop training targets" above | That player's own rolling per-stat averages, `games_with_<stat>`, `games_played`, `starts`, plus event context reoriented to the player's own/opponent perspective (`is_home`, `kickoff_hour_utc`, `rest_days`, `own_elo`/`opponent_elo`/`elo_diff`, `is_divisional_game`, `is_international_game`, `travel_km`) -- restricted to columns with at least `MIN_NON_NULL_FRACTION` of the filtered population non-null |

**Scheduling**: not yet wired into a recurring schedule -- `nba_deploy.yml`'s `ai_training` job (and the equivalent Terraform scheduler entries NFL/NCAAFB each have) is deferred to Sub-phase 3A step 9 (CI wiring). The training orchestrator's registry-driven `RunFeatureEngineering`/`TrainAllTargets` Step Function states will pick NBA up automatically once its `dynamodb-sport-registry.tf` row (already in place since step 2) starts getting invoked -- no new orchestration Terraform needed, same registry-driven onboarding principle every prior sport followed.

## What this makes it possible to predict

**Directly, from these two datasets, once a real training run has been evaluated:**
- **Game outcomes** (win/loss).
- **Game scores** -- margin, home score, away score.
- **Individual player stats** -- points, rebounds, assists, steals, blocks, three-pointers made.

**Not directly -- simulated, not separately feature-engineered (Sub-phase 3A step 8, not yet built):**
- **Season win-loss totals**, **play-in outcomes**, and **championship odds** are all downstream of the win-probability model above, run forward via Monte Carlo simulation over a team's remaining schedule -- same approach as NFL's `season_simulation.py`, adapted for NBA's own postseason format (an 8-seed-per-conference bracket **plus** a 4-team play-in round per conference, genuinely more complex than NFL's straight reseeded bracket, not a parameter tweak). No separate feature set needed for this -- it consumes the event-level features and model that already exist here.
