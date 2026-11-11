# Team-sport efficiency, context and schedule features

Columns shared by the NFL, NCAAFB, NBA and NCAA MBB event rows, and the player-prop context columns all five team sports' player rows get. Each sport's own `*_FEATURE_ENGINEERING.md` covers the rest of its rows. Every column comes from the sports data we ingest; no betting-market figure feeds any model.

Training (`library/features/build_dataset_common.py`) and live serving (each sport's `predict/live_features.py`, `library/features/basketball_live.py`) build these through the same functions.

## Pre-game snapshot

Injury reports, weather and coach records describe a game before kickoff. Ingest attaches them only while a game hasn't finished (`library/normalize/espn.py`'s `attach_injuries`, `aws-lambdas/nfl/ingest/enrichment.py`, `aws-lambdas/ncaafb/ingest/enrichment.py`), and `PipelineStorage.upsert_event` keeps the stored value when a later rewrite lacks it (`library/normalize/pregame.py`'s `PREGAME_FIELDS`). The prediction scheduler's pre-kickoff refresh re-ingests each game's scoreboard about 30 minutes out, so a completed event carries the report as it stood then. A pre-game injury report is stamped `injuries_as_of`; feature code reads injuries only through `pregame_injuries`, so the unstamped lists stored on events completed before this shipped (attached after the game) count as unknown. Every injury column is therefore forward-only, including the older `*_team_injury_count` and `*_qb_injury_status`.

## Paired box history

`library/features/box_history.py` joins each team's `team_game_stats` row with its opponent's row from the same game, the final score and the game's period count. A team's defence ("allowed") reads from the same history as its offence. Event builders receive up to `TEAM_HISTORY_GAMES` (40) paired rows per side; the existing base rolling averages still use the first `ROLLING_WINDOW` of them.

## Windows

`library/features/box_windows.py`. A rate is the ratio of the window's sums, not a mean of per-game rates; a game missing either part is skipped.

| Suffix | Window |
|---|---|
| *(none)* | Last `ROLLING_WINDOW` games (default 5) |
| `_last10` | Last 10 games |
| `_season_to_date` | This season's games (after the last 60-day gap) |
| `_ewm` | Every game given, each 3 games older weighing half as much |

Core metrics (marked ★) get all four windows and a `diff_` (home minus away) column per window. The others get the base and season-to-date windows.

## Football (`library/features/football_box.py`)

Plays: ESPN's `total_offensive_plays`, else pass attempts + rushes + sacks taken. Sacks taken: ESPN's `sacks_taken`, else the opponent's defensive `sacks` (CFBD reports only a defence's sacks).

| Column | Calculation | NFL | NCAAFB |
|---|---|---|---|
| ★ `yards_per_play`, ★ `yards_per_play_allowed`, ★ `net_yards_per_play` | total yards / plays; difference | ✓ | ✓ |
| `yards_per_pass_attempt` (+ `_allowed`) | net passing yards / pass attempts | ✓ | ✓ |
| `yards_per_rush` (+ `_allowed`) | rushing yards / rushes | ✓ | ✓ |
| `first_downs_per_play` (+ `_allowed`) | first downs / plays | ✓ | ✓ |
| ★ `turnover_margin`, `takeaways` | opponent turnovers − own; opponent turnovers | ✓ | ✓ |
| ★ `sack_rate`, ★ `pressure_rate` | sacks taken / (pass attempts + sacks taken); the opponent's | ✓ | ✓ |
| ★ `third_down_pct_allowed`; `third_down_pct_last10/_season_to_date/_ewm` | conversions / attempts | ✓ | ✓ |
| `fourth_down_attempts`, `fourth_down_pct` | per game; conversions / attempts | ✓ | ✓ |
| ★ `points_per_drive` (+ `_allowed`) | points / drives | ✓ | from CFBD advanced stats |
| `red_zone_pct_allowed`, `red_zone_trips`; `red_zone_pct_last10/...` | | ✓ | — |
| `avg_turnovers/total_yards/possession_time_seconds/penalties/penalty_yards` `_last10/_season_to_date/_ewm` | means | ✓ | ✓ |

NFL also has `is_precipitation`: 1 when ESPN's pre-game forecast (`weather_condition_id`, an AccuWeather icon id) is rain, showers, storms, snow, sleet or ice. Forward-only.

### NCAAFB CFBD supplements (`library/features/ncaafb_advanced.py`)

NCAAFB ingest saves three CFBD responses to the raw bucket, each entry tagged with our team id (`aws-lambdas/ncaafb/ingest/supplements.py`); feature engineering and serving read them back through `library/storage/cfbd_supplements.py`.

| Raw key | Columns |
|---|---|
| `ncaafb/advanced/{season}/{type}/{week}.json` (`/stats/game/advanced`, garbage time excluded) | ★ `off_epa_per_play`, ★ `def_epa_per_play` (totalPPA / plays); `off/def_success_rate` (play-weighted); `off/def_explosiveness`, `off/def_rush_epa`, `off/def_pass_epa`, `off/def_stuff_rate`, `off/def_line_yards` (game means); ★ `points_per_drive` (+ `_allowed`) |
| `ncaafb/talent/{season}.json` (`/talent`) | `talent` (247 composite), `diff_talent` |
| `ncaafb/returning/{season}.json` (`/player/returning`) | `returning_ppa_pct`, `returning_pass/rush/receiving_ppa_pct`, `returning_usage`, `diff_returning_ppa_pct` |

All forward-only: saved from the season this shipped.

## Basketball (`library/features/basketball_box.py`)

A game's possessions are the mean of both sides' estimates, FGA − ORB + TOV + 0.44 × FTA (turnovers including team turnovers).

| Column | Calculation |
|---|---|
| ★ `pace` | possessions × regulation minutes / game minutes; each overtime period adds 5 minutes when the event's `periods` is known (forward-only), else the game counts as regulation length |
| ★ `off_rating`, ★ `def_rating`, ★ `net_rating` | 100 × points scored / allowed per possession; difference |
| ★ `efg_pct` (+ ★ `_allowed`) | (FGM + 0.5 × 3PM) / FGA |
| ★ `tov_pct`, ★ `tov_pct_forced` | turnovers / (FGA + 0.44 × FTA + turnovers); the opponent's |
| ★ `orb_pct`, ★ `drb_pct` | ORB / (ORB + opponent DRB); DRB / (DRB + opponent ORB) |
| `ft_rate`, `three_pa_rate` (+ `_allowed`) | FTM / FGA; 3PA / FGA |
| `paint_pts_per_100`, `fast_break_pts_per_100`, `pts_off_tov_per_100` (+ `_allowed`) | per 100 possessions |
| `field_goal_pct`, `three_point_pct`, `free_throw_pct`, `avg_assists/steals/blocks/turnovers` `_last10/_season_to_date/_ewm` | |

The older `*_offensive_efficiency`/`*_defensive_efficiency` columns stay until the models retrain on these.

## Context (`library/features/team_context.py`)

| Column | Calculation |
|---|---|
| `is_neutral_site` | ESPN `neutralSite` / CFBD `neutralSite`; forward-only (NHL too) |
| `{side}_strength_of_schedule` | mean pre-game Elo of this season's opponents |
| `{side}_win_pct_home`, `{side}_win_pct_road` | this season, neutral-site games excluded |
| `home/away_current_rank` (NCAA MBB) | the scoreboard's AP rank going into the game (`curatedRank`, 1–25); forward-only |

## Schedule and body clock (`library/features/schedule.py`)

Pro sports place a game at the home team's market (or a known international venue) using `TEAM_COORDINATES`/`TEAM_UTC_OFFSETS`; college sports place it at the centre of the event's `venue_state` (`library/features/us_states.py`), and a team's home is the state of most of its home games.

| Column | Sports | Calculation |
|---|---|---|
| `{side}_timezone_shift_hours` | all four | venue clock − team's home clock (standard time) |
| `{side}_local_start_hour` | all four | start time on the team's home clock |
| `{side}_is_back_to_back`, `_games_last_4_days`, `_games_last_7_days` | basketball | |
| `{side}_home_stand_game_number`, `_road_trip_game_number` | basketball | games in a row at home / away, counting this one; neutral sites count as away |
| `{side}_trip_km` | basketball | previous game's site → this game's site, within the season |
| `{side}_travel_km` | NCAA MBB | home state → venue state |

## Elo (`library/features/elo.py`)

Each sport rates with its own constants. Football: K 20, home edge 55, log margin multiplier, 2/3 carried over a season. Basketball: K 20, home edge 100, (|margin| + 3)^0.8 / (7.5 + 0.006 × Elo gap), 3/4 carried over. A neutral-site game gets no home edge. The season simulations use the same constants. Each feature-engineering run logs a log-loss sweep over K, home edge and carry-over ("Elo sweep (sport): ..."); the constants change only by hand.

## Player-prop context (`library/features/player_context.py`)

Added to every team sport's player rows through `matchup_columns`.

| Column | Sports | Calculation |
|---|---|---|
| `own_injury_status` | all | 0–3 on the event's pre-game report; None without one |
| `teammates_out_minutes_share`, `teammates_out_fga_share` | basketball | summed over teammates Out, Doubtful or on IR: each one's mean share of team minutes / FGA over their last 10 games |
| `teammates_out_target_share`, `teammates_out_carry_share`, `team_qb_injury_status` | NFL | same for targets / carries; status of an injured teammate with ≥ half the team's recent passes, else 0 |
| `projected_pace`, `opp_def_rating`, `opp_efg_allowed` | basketball | from the last 10 games' player-row team totals |
| `opp_yards_per_pass_allowed`, `opp_yards_per_rush_allowed`, `opp_sack_rate` | football | the opponent's last 10 games |
| `minutes_trend` | basketball | mean minutes last 3 − last 10 |
| `starts_rate_l10` | all | share of the last 10 games started |

Injury-based columns are forward-only: they're None wherever an event has no pre-game report. NCAAFB has no injury source.
