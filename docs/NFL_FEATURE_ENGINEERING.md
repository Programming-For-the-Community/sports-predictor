# NFL Feature Engineering

This describes the feature engineering built for NFL — what it computes, where it runs, and what it makes it possible to predict

## Feature Logic

The math itself lives in `Source/library/features/nfl.py`, deliberately *not* inside `feature-engineering/nfl/`. The reason: the same functions will eventually be called twice — once here, in bulk, to build a training set, and later by an inference Lambda computing a live feature vector for one upcoming game. Keeping one shared implementation instead of two copies is what prevents train/serve skew (the model being trained on features computed slightly differently than what it's fed at prediction time).

### Event-level features (`build_event_features`)

One row per completed game. Inputs to the model:

| Feature | What it captures |
|---|---|
| `week` / `season_type` | How far into the season the game falls, and whether it's regular season or postseason. Both already land in DynamoDB via `scoreboard_event_to_event_item` (`library/normalize/espn.py`) — no new data source, they just weren't being surfaced as model inputs before |
| `home_elo` / `away_elo` / `elo_diff` | Each team's Elo-style rating going into the game (see below) |
| `home_rest_days` / `away_rest_days` | Days since each team's previous game |
| `home_avg_points_scored` / `home_avg_points_allowed` (and the away equivalents) | Rolling average over the last 5 games (configurable via `ROLLING_WINDOW`) |
| `home_games_played` / `away_games_played` | How much history backs those averages — lets a model learn to trust an early-season row less |
| `venue_indoor` | Whether the game was played in a dome — weather is meaningless for an indoor game, so this lets the model treat `weather_temperature` differently (or ignore it) for those rows |
| `weather_temperature` | Game-time temperature from ESPN's venue data. Frequently `null` — most reliably for indoor games, where it doesn't apply, but also for some outdoor games ESPN simply didn't report on. A partial signal, not a guaranteed one |
| `home_qb_avg_passing_yards` / `home_qb_avg_passing_tds` / `home_qb_avg_interceptions` (and the away equivalents) | Rolling average over each side's **starting QB's own last 5 games** — not the team's, so a backup filling in doesn't inherit the usual starter's numbers, and a traded QB's history follows them rather than resetting |
| `home_qb_games_played` / `away_qb_games_played` | How much of that QB's own history backs the averages above; `0` when a starter couldn't be identified for that game |
| `home_rb_avg_rushing_yards` / `home_rb_avg_rushing_tds` (and the away equivalents) | Same rolling-history approach as the QB fields, tracking each side's identified lead rusher |
| `home_rb_games_played` / `away_rb_games_played` | How much of that rusher's own history backs the averages above |
| `home_wr_avg_receiving_yards` / `home_wr_avg_receiving_tds` / `home_wr_avg_receptions` (and the away equivalents) | Same rolling-history approach, tracking each side's identified lead receiver |
| `home_wr_games_played` / `away_wr_games_played` | How much of that receiver's own history backs the averages above |
| `home_avg_turnovers` / `home_avg_total_yards` / `home_avg_possession_time_seconds` (and the away equivalents) | Rolling average of each team's own box score totals (see `team_game_stats` in `design/DATA_SCHEMA.md`) |
| `home_third_down_pct` / `home_red_zone_pct` (and the away equivalents) | Conversions over attempts across the rolling window (not an average of per-game percentages, which would let a small-sample game skew the rate) |
| `home_box_games_played` / `away_box_games_played` | How much team box score history backs the averages above |
| `is_divisional_game` | Whether both teams are in the same division (`library/features/nfl_teams.py`) — divisional matchups are played more often and behave differently in some analyses |
| `is_international_game` | Whether the game was played at one of `nfl_teams.INTERNATIONAL_VENUES` (London, Munich, etc.) rather than either team's home market |
| `home_travel_km` / `away_travel_km` | Distance each team travels to the game, computed between team home-market coordinates and, for international games, the actual host-city coordinates instead of assuming the home team traveled 0 km |
| `home_win_streak` / `away_win_streak` | Each team's current run of consecutive wins (positive) or losses (negative), reset by a tie — a short-term-momentum signal distinct from Elo's longer-run skill estimate |

Also carried on the row for reference, but **excluded from training** by `train_model.py` (raw strings aren't model-consumable without encoding — see `design/DATA_SCHEMA.md`): `venue_city`, `venue_state`.

Labels carried on the same row (the training targets, not inputs): `label_home_won`, `label_home_score`, `label_away_score`.

**Elo ratings** (`compute_elo_ratings`) are computed by walking every team's games in chronological order and updating a running rating after each result, with a home-field advantage added to the expected-score calculation and a margin-of-victory multiplier (`_mov_multiplier`) scaling each update — a blowout moves ratings more than a one-score win, log-dampened and further dampened when the winner was already a big favorite. Defaults: starting rating 1500, K-factor 20, home advantage 55 rating points. Each event gets its **pre-game** rating recorded (using the post-game rating would leak that game's own outcome into its own features). Ties apply no MOV scaling (there's no winner to measure a margin from).

**Identifying a game's key players** (`identify_starting_qb` / `identify_lead_rusher` / `identify_lead_receiver`): a team's box score doesn't flag who started, so these pick whoever had the most passing attempts / rushing attempts / receiving targets in that game — correctly favoring the primary contributor over a backup with a handful of mop-up reps. `build_dataset.py`'s orchestration groups `player_game_stats` by `(event_key, team_id)`, runs each identify function per side per event, and tracks each identified player's own rolling history by `entity_id` (the same incremental, capped-at-`window` approach used for team history) rather than re-deriving it per game. A raw player identity isn't used as a feature directly — like head coach, it's high-cardinality and doesn't generalize across roster turnover — so only the derived rolling stats above are surfaced.

**Team box score stats**: ESPN's box score also reports team-level aggregates (turnovers, total yards, time of possession, third/fourth-down and red-zone efficiency) directly, separate from individual player stat lines — stored in the `team_game_stats` table and tracked with the same incremental rolling-history approach as team scoring, keyed by `team_id`.

**Division/travel/streak** (`library/features/nfl_teams.py`, `current_streak`): division and each team's home-market coordinates are a static, hardcoded table, not fetched from any API — the last NFL realignment was 2002, well before this project's data window, so a fixed table is simpler and more robust than depending on an ESPN endpoint for something that essentially never changes. `current_streak` walks a team's own event history backward from most recent, comparing scores directly (not each participant's `won` flag, which reads `False` for both sides on a tie) so a tie correctly resets the streak instead of counting as a loss.

`travel_distances_km` also hardcodes `INTERNATIONAL_VENUES` (London, Munich, Mexico City, etc.) so the handful of neutral-site games each season get real travel distances for both teams instead of assuming the designated home team is at their actual home market. This table needs a manual update whenever the NFL adds a new host city — `build_event_features` logs a warning (venue has no US state and isn't in `INTERNATIONAL_VENUES`) when it hits one it doesn't recognize, so a new city shows up in CloudWatch instead of silently producing wrong travel numbers for that game.

### Player-level features (`build_player_features`)

One row per player per completed game. For every stat a player's `stat_line` contains (passing yards, receptions, field goals made — whatever ESPN reports for that position), the function averages it over that player's last 5 games *that had that stat*, rather than diluting the average with games where the stat didn't apply. This is generic across positions on purpose — a QB's passing keys and a kicker's field-goal keys never collide, so there's no per-position hardcoding.

Also included: `games_played` and `starts` over the window. Label carried on the row: `label_stat_line` (that game's actual stat line, JSON-encoded since Parquet/Arrow needs a stable schema across rows and stat-line keys vary by position) and `label_started`.

**Known simplification — usage rate:** `design/PROJECT_PLAN.md` calls for "usage rate" as a player feature. What's implemented is raw per-game volume (attempts, targets, carries — as ordinary rolling-average stat_line keys). A true usage-rate metric (share of team volume, e.g. target share) needs every player on the team's stat line for the same game to compute a team total, which isn't wired up yet. This is a follow-up, not something silently skipped.

## What this makes it possible to predict

**Directly, from these two datasets:**
- **Game outcomes** (win/loss) — a classifier trained on the event-level features and `label_home_won`.
- **Game scores** — a regressor trained on the same event-level features, predicting `label_home_score` / `label_away_score` (or the margin between them).
- **Individual player stats** — one regressor per stat (passing yards, receptions, etc.), trained on the player-level features and the corresponding key inside `label_stat_line`.

**Not directly — these are simulated, not separately feature-engineered:**
- **Team win-loss totals for a season**, **playoff game winners**, and **Super Bowl winner** are all downstream of the single-game outcome model above, not new feature categories. The standard approach (and the one this project follows): run the game-outcome model's win probability for every remaining game on a team's schedule — or every matchup in a playoff bracket — and simulate forward (e.g. Monte Carlo over those probabilities) rather than training a model whose target *is* "wins this season" or "wins the Super Bowl" directly. There's no separate feature set to build for these; they consume the event-level features and model that already exist here. This logic belongs in a future `predict.py`, not in feature engineering.
