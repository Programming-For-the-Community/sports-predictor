# NFL Feature Engineering

This describes the feature engineering built for NFL — what it computes, where it runs, and what it makes it possible to predict

## Feature Logic

The math itself lives in `Source/library/features/nfl.py`, deliberately *not* inside `feature-engineering/nfl/`. The reason: the same functions will eventually be called twice — once here, in bulk, to build a training set, and later by an inference Lambda computing a live feature vector for one upcoming game. Keeping one shared implementation instead of two copies is what prevents train/serve skew (the model being trained on features computed slightly differently than what it's fed at prediction time).

### Event-level features (`build_event_features`)

One row per completed game. Inputs to the model:

| Feature | What it captures |
|---|---|
| `home_elo` / `away_elo` / `elo_diff` | Each team's Elo-style rating going into the game (see below) |
| `home_rest_days` / `away_rest_days` | Days since each team's previous game |
| `home_avg_points_scored` / `home_avg_points_allowed` (and the away equivalents) | Rolling average over the last 5 games (configurable via `ROLLING_WINDOW`) |
| `home_games_played` / `away_games_played` | How much history backs those averages — lets a model learn to trust an early-season row less |

Labels carried on the same row (the training targets, not inputs): `label_home_won`, `label_home_score`, `label_away_score`.

**Elo ratings** (`compute_elo_ratings`) are computed by walking every team's games in chronological order and updating a running rating after each result — the standard Elo update, with a home-field advantage added to the expected-score calculation. Defaults: starting rating 1500, K-factor 20, home advantage 55 rating points. This is intentionally the *plain* version — no margin-of-victory scaling, matching `design/PROJECT_PLAN.md` Phase 1's "an Elo-style rating" bullet. Each event gets its **pre-game** rating recorded (using the post-game rating would leak that game's own outcome into its own features).

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
