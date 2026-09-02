# Sport Predictor API

This documents every route live on the shared API Gateway REST API (`Terraform/api-gateway.tf`, `Terraform/api-gateway-{sport}-predict.tf`, `Terraform/api-gateway-{sport}-live-scores.tf`, one pair per sport), fronted by CloudFront at the app's single public domain -- see `docs/AWS_ARCHITECTURE.md`'s client-request-path diagram. Every head-to-head sport (NFL, NCAAFB, NBA, NCAAMBB) gets the identical `/{sport}/events`, `/{sport}/models`, `/{sport}/season`, `/{sport}/live-scores`, `/{sport}/predictions/events/{event_id}[/players/{entity_id}]` route shape; the two field-event sports (PGA, F1) share the same first four routes but have no `/players/{entity_id}` route at all -- a golfer's/driver's own entity IS the participant, so their full prediction is already the single event-level response (see the PGA/F1 section below). Nothing about either contract is sport-specific beyond the path prefix, response field names, and (for head-to-head sports) player-prop stat names. **All six sports are fully live, backend and frontend.**

Formerly `NFL_API.md` -- renamed and expanded to cover every onboarded sport under one doc, since the contract genuinely is shared (this file documents the contract once, then calls out each sport's own concrete values).

## Base URL

`https://<project>.<domain>` -- the CloudFront distribution's own domain (`local.domain` in `Terraform/locals.tf`, exposed as the `api_endpoint` Terraform output). CloudFront routes `/nfl/*`, `/ncaafb/*`, `/nba/*`, `/ncaambb/*`, `/pga/*`, `/f1/*` to API Gateway's own custom domain (`Terraform/api-gateway-domain.tf` -- not the raw `execute-api` hostname, which is disabled entirely) and everything else to the Flutter frontend's S3 bucket (`Terraform/cloudfront.tf`), so the frontend and API share one origin -- no CORS is involved in production traffic.

## Authentication

Every route below requires a valid Cognito-issued JWT. API Gateway validates it via a `COGNITO_USER_POOLS` authorizer (`aws_api_gateway_authorizer.cognito`, `Terraform/api-gateway.tf`) before any Lambda ever runs -- no Lambda checks auth itself.

Send the access token as the raw `Authorization` header value, **not** prefixed with `Bearer `:

```
Authorization: <access_token>
```

A missing or invalid token gets rejected by API Gateway directly (401/403) -- it never reaches a Lambda.

## Architecture: predict vs. predict-read

Every sport's serving layer splits across two Lambdas, not one:

- **`<sport>-predict-read`** (zip-packaged, no ML dependencies) -- what API Gateway actually invokes for every route below. Serves `/events`/`/models`/`/season` directly from DynamoDB/S3, and fronts the two `/predictions/...` routes with a read-through S3 cache (`library.storage.prediction_cache`).
- **`<sport>-predict`** (container image, ML dependencies) -- never invoked by API Gateway directly. A background compute worker: populates the prediction cache asynchronously on a cache miss/stale refresh (triggered by `predict-read`), and runs the weekly season-projection job on a schedule.

This split exists so the hot path (every page load hits `/events`/`/models`) never pays a Lambda cold start for xgboost/scikit-learn/pandas, and so a live request never blocks on a real inference computation -- it gets served from cache or told to poll.

### Prediction-route caching semantics

Both `/predictions/events/{event_id}` and `/predictions/events/{event_id}/players/{entity_id}` follow the same read-through-cache contract, for every sport:

| Response | Meaning |
|---|---|
| `200`, body carries `"stale": false` | Fresh cache hit, served straight from S3. |
| `203`, body carries `"stale": true` and `"retry_after_seconds"` | Stale cache hit -- served anyway (possibly outdated), background refresh triggered. A repeat request while that refresh is in flight returns `203` again without triggering a second one. |
| `202`, `{"status": "computing", "retry_after_seconds": N}` | Cache miss -- async compute triggered, nothing to serve yet. A repeat request for the same key returns `202` again without triggering a second compute. |
| `404` / `422` / `503` | A cached **negative** result (event not ingested / malformed event / no promoted model yet) -- see "Error responses" below. Also short-lived-cached, so a repeat request doesn't re-trigger the same failing compute every time. |

`retry_after_seconds` is a UI hint only (currently `5`), not enforced server-side -- nothing stops a client from polling sooner or later.

## NFL routes (`/nfl/*`) -- live

### `GET /nfl/events`

Scoped to exactly **one week**, not the whole matching history: `status=scheduled` returns the soonest upcoming week (empty if that week hasn't been ingested yet -- the frontend shows a "coming soon" state rather than mistaking that for no games); `status=completed` returns the most recently completed week.

**Query parameters**

| Name | Required | Values | Default |
|---|---|---|---|
| `status` | no | `scheduled`, `completed` | `scheduled` |

**Response `200`**

```json
{
  "sport": "nfl",
  "events": [
    {
      "event_id": "401547417",
      "event_date": "2026-09-13",
      "kickoff_time": "2026-09-13T17:00:00Z",
      "status": "scheduled",
      "season": 2026,
      "season_type": 2,
      "week": 2,
      "round": null,
      "venue_name": "GEHA Field at Arrowhead Stadium",
      "venue_city": "Kansas City",
      "venue_state": "MO",
      "participants": [
        {"entity_id": "12", "role": "home", "result": null, "name": "Kansas City Chiefs", "abbreviation": "KC"},
        {"entity_id": "13", "role": "away", "result": null, "name": "Los Angeles Chargers", "abbreviation": "LAC"}
      ],
      "prediction_comparison": null,
      "leaders_comparison": null
    }
  ]
}
```

`round` is the playoff round name (`Wild Card`/`Divisional`/`Conference Championship`/`Super Bowl`) for a postseason game, `null` for regular season. `prediction_comparison`/`leaders_comparison` are only populated (not `null`) for a **completed** week -- predicted-vs-actual win/margin/score and predicted-vs-actual player-prop leaders, read from the predictions-table audit trail; `null` if no prediction was ever logged for that event before it was played. Every participant carries `name`/`abbreviation` off its own team entity. Excludes the Pro Bowl and any other exhibition game entirely.

### `GET /nfl/models`

Lists every currently-promoted model, with its latest model card summary.

**Response `200`**

```json
{
  "sport": "nfl",
  "models": [
    {
      "model_name": "win-probability",
      "algorithm": "xgboost",
      "version": 6,
      "trained_at": "2026-07-30T00:00:00Z",
      "accuracy": 0.63,
      "log_loss": 0.65,
      "naive_baseline_accuracy": 0.57,
      "top_features": [{"feature": "elo_diff", "importance": 0.22}],
      "candidates_ranked_by": "log_loss",
      "candidates": [
        {"algorithm": "xgboost", "score": 0.63, "rank_score": 0.65},
        {"algorithm": "random_forest_classifier", "score": 0.66, "rank_score": 0.68},
        {"algorithm": "logistic_regression", "score": 0.61, "rank_score": 0.69},
        {"algorithm": "mlp_classifier", "score": 0.59, "rank_score": 0.74}
      ]
    }
  ]
}
```

`accuracy`/`log_loss` are present for a classification-task model (`win-probability`); `rmse`/`mae` are present for every regression-task model (`score-margin`, `home-score`, `away-score`, each `player-prop-<stat>`). `naive_baseline_*` is the same metric against a trivial baseline (classifier: always pick the home team; regressor: predict the player's own rolling average) -- the frontend shows skill relative to that baseline instead of surfacing `log_loss`/`rmse` directly. `candidates` is every algorithm `library.ml.backtest.run_backtest` actually tried for this target this run, ranked best-first by `rank_score` (the value of `candidates_ranked_by` -- what actually decided promotion), not by `score` (the human-readable metric, which can disagree in ranking direction). `top_features` is pre-sorted descending by importance and capped at 5 server-side. A model that's never had a version promoted simply doesn't appear in this list.

### `GET /nfl/season`

The current season's standings (real record + Elo Monte Carlo projection) and, per tracked player-prop stat, a top-10 season-long leaderboard. Recomputed weekly on a schedule, served from S3 -- not computed per-request.

**Response `200`**

```json
{
  "sport": "nfl",
  "season": 2026,
  "standings": [
    {
      "team_id": "12",
      "wins": 8,
      "losses": 2,
      "ties": 0,
      "projected_wins": 13.4,
      "projected_losses": 3.6,
      "division_winner_probability": 0.91,
      "playoff_probability": 0.98,
      "championship_probability": 0.22,
      "name": "Kansas City Chiefs",
      "abbreviation": "KC"
    }
  ],
  "leaderboards": {
    "passing_yards": [
      {"entity_id": "3139477", "name": "Patrick Mahomes", "current_total": 2100.0, "projected_total": 4300.0}
    ],
    "passing_touchdowns": [],
    "rushing_yards": [],
    "rushing_touchdowns": [],
    "receiving_yards": [],
    "receiving_touchdowns": [],
    "defensive_sacks": []
  },
  "generated_at": "2026-08-03T00:00:00Z"
}
```

`standings` is sorted by `projected_wins` descending (flat list, not grouped by division -- division/conference membership is `library/features/nfl_teams.py`'s `TEAM_DIVISIONS`, a frontend-side lookup by `team_id`). `503` if no season projection has been computed yet.

### `GET /nfl/live-scores`

Cache-only -- never triggers a live ESPN call itself; served entirely from what the scheduled 60-second poll last wrote to S3 (near-kickoff/in-progress events only, see `Source/aws-lambdas/nfl/live-scores/live_scores.py`).

**Response `200`**

```json
{
  "events": {
    "401547417": {
      "live": true,
      "completed": false,
      "detail": "3rd Qtr 4:12",
      "home_score": 17,
      "away_score": 14,
      "player_stats": {
        "3139477": {"passing_yards": 210, "passing_touchdowns": 2}
      }
    }
  }
}
```

`player_stats` is only present for an event currently `live` -- best-effort per-player live stats for that event's predicted leaders, omitted (not `null`) if that fetch failed this tick. `events` is `{}` if nothing is currently in a live-poll window.

### `GET /nfl/predictions/events/{event_id}`

Live prediction for one event: win probability, margin, home/away score, plus each team's passing/receiving/rushing/sacks leaders (each scored against its own player-prop model). `{event_id}` is a raw ESPN event id (e.g. `"401547417"`), not the internal `SPORT#NFL#EVENT#...` storage key. See "Prediction-route caching semantics" above for the `200`/`203`/`202` response shapes -- the example below is the `200` (fresh) case.

**Response `200`**

```json
{
  "event_key": "SPORT#NFL#EVENT#401547417",
  "predictions": {
    "win_probability": {"home_win_probability": 0.71, "model_version": 6},
    "margin": {"value": 6.2, "model_version": 3},
    "home_score": {"value": 27.4, "model_version": 2},
    "away_score": {"value": 21.2, "model_version": 2}
  },
  "leaders": {
    "home": {
      "passing": {"entity_id": "3139477", "name": "Patrick Mahomes", "passing_yards": 267, "passing_touchdowns": 2},
      "receiving": [{"entity_id": "...", "name": "...", "receiving_yards": 78, "receiving_touchdowns": 1}],
      "rushing": [{"entity_id": "...", "name": "...", "rushing_yards": 62, "rushing_touchdowns": 0}],
      "sacks": [{"entity_id": "...", "name": "...", "defensive_sacks": 1.5}]
    },
    "away": { "...": "same shape" }
  },
  "stale": false,
  "generated_at": "2026-08-03T00:00:00Z"
}
```

`receiving`/`rushing`/`sacks` are arrays (up to 3, 2, and 3 candidates respectively); `passing` is a single object or `null`. `leaders` as a whole is `null` if it couldn't be computed -- the core `predictions` block above never fails over it. Every computed prediction is also logged to the predictions table for audit (`design/DATA_SCHEMA.md`'s Predictions table).

### `GET /nfl/predictions/events/{event_id}/players/{entity_id}`

One player-prop prediction for one player in one game.

**Query parameters**

| Name | Required | Values |
|---|---|---|
| `stat` | **yes** | `passing_yards`, `passing_touchdowns`, `rushing_yards`, `rushing_touchdowns`, `receiving_yards`, `receiving_touchdowns`, `defensive_sacks` |

The stat list's source of truth is `Terraform/dynamodb-sport-registry.tf`'s `nfl_player_prop_stats` map -- an 8th stat later means adding one entry there (and retraining), not a route change. Omitting `stat` returns `400`.

**Response `200`**

```json
{
  "event_key": "SPORT#NFL#EVENT#401547417",
  "entity_key": "SPORT#NFL#ENTITY#3139477",
  "stat": "passing_yards",
  "prediction": {"value": 267.4, "model_version": 4},
  "stale": false,
  "generated_at": "2026-08-03T00:00:00Z"
}
```

## NCAAFB routes (`/ncaafb/*`) -- live

Identical route shape to NFL, with the following sport-specific differences.

### `GET /ncaafb/events`

Same week-scoping/caching-audit-trail contract as NFL's own `/nfl/events`. Differences:
- No exhibition-game filter -- NCAAFB has no Pro Bowl equivalent to exclude.
- No `venue_city`/`venue_state` fields -- only `venue_name`.
- `round` is `"CFP"` (a real 12-team College Football Playoff game), `"Bowl"` (any other postseason game), or `null` (regular season) -- coarser than NFL's per-round week-number mapping, since CFBD's postseason week numbering has no stable per-round meaning across ~40 unaffiliated bowls plus the CFP.

### `GET /ncaafb/models`

Same shape as NFL's, plus one extra model: `national-ranking` (a regression-task model predicting each team's AP Top 25 rank, `rmse`/`mae` scored) -- NCAAFB's only training target with no NFL/NBA equivalent, feeding the CFP field-selection simulation below.

### `GET /ncaafb/season`

Same route, **different standings shape** -- reflects a 12-team CFP field instead of NFL's division-based playoff structure, and has **no player-prop leaderboard at all** (`leaderboards` key doesn't exist in the response -- team outcomes only, no player-level season simulation for NCAAFB).

**Response `200`**

```json
{
  "sport": "ncaafb",
  "season": 2026,
  "standings": [
    {
      "team_id": "333",
      "conference": "SEC",
      "wins": 9,
      "losses": 1,
      "ties": 0,
      "current_rank": 3,
      "projected_wins": 11.2,
      "projected_losses": 1.8,
      "conference_champion_probability": 0.44,
      "bowl_probability": 0.97,
      "playoff_probability": 0.61,
      "championship_probability": 0.09,
      "name": "Alabama Crimson Tide",
      "abbreviation": "ALA"
    }
  ],
  "generated_at": "2026-08-03T00:00:00Z"
}
```

`current_rank` and every `*_probability` field are `null`/absent if no `national-ranking` model has been promoted yet (season simulation still runs off real win/loss records in that case, just without a ranking-informed CFP field score). `playoff_probability` here means "makes the 12-team CFP field" (auto-bid conference champions plus at-large teams ranked by the model), not NFL's division/wild-card structure.

### `GET /ncaafb/live-scores`

Identical contract to `/nfl/live-scores`.

### `GET /ncaafb/predictions/events/{event_id}` and `.../players/{entity_id}`

Identical contract and `leaders` shape to NFL's own (`passing`/`receiving`/`rushing`/`sacks`, same candidate-count limits). Player-prop `stat` values (`Terraform/dynamodb-sport-registry.tf`'s `ncaafb_player_prop_stats`): `passing_yards`, `passing_touchdowns`, `rushing_yards`, `rushing_touchdowns`, `receiving_yards`, `receiving_touchdowns`, `defensive_sacks` -- same 7 names as NFL, verified against CFBD's own field names independently (not assumed identical just because the names match).

## NBA routes (`/nba/*`) -- live

Identical route shape to NFL, with these sport-specific differences.

### `GET /nba/events`

Same week-scoping-equivalent (day-grouped, not week-grouped -- NBA has no week numbering) and caching-audit-trail contract as NFL's own `/nfl/events`. Each event carries predicted winner/margin/score plus its **top-5** predicted leaders across `scoring`/`rebounding`/`assists` (bumped from an original top-2, see the `playoff-bracket-feature` memory), and, for a playoff-series game, live series-record awareness (e.g. "2-1") alongside the predicted final series score (e.g. "predicted 4-2"). No `venue_city`/`venue_state` gap -- both are served (a real bug, fixed 2026-08-19/20).

### `GET /nba/models`

Same shape as NFL's, but every target's `candidates` list has **5** entries, not 4 -- `LightGBMClassifierAdapter`/`LightGBMRegressorAdapter` (`library/ml/model_types.py`) were added specifically for NBA's onboarding (basketball's larger data volume). No `national-ranking` model -- NBA has no in-season poll.

### `GET /nba/season`

Recomputed weekly, served from S3, same as NFL. **Genuinely different shape** -- no division/wild-card structure, and standings are grouped by division for display only (no seeding benefit, see `NBA_FEATURE_ENGINEERING.md`).

```json
{
  "sport": "nba",
  "season": 2027,
  "standings": [
    {
      "team_id": "25",
      "division": "Northwest",
      "wins": 41,
      "losses": 12,
      "projected_wins": 58.3,
      "projected_losses": 23.7,
      "division_winner_probability": 0.88,
      "play_in_probability": 0.02,
      "playoff_probability": 0.97,
      "championship_probability": 0.19,
      "name": "Oklahoma City Thunder",
      "abbreviation": "OKC"
    }
  ],
  "cup": { "...": "NBA Cup in-season-tournament group standings, per Terraform-maintained CUP_GROUPS[season]; null if the current season has no cup groups configured yet" },
  "cup_bracket": { "rounds": [ "..." ], "champion": null },
  "leaderboards": { "points": [ "..." ], "rebounds": [], "assists": [], "steals": [], "blocks": [], "three_pointers_made": [] },
  "bracket": {
    "rounds": [
      { "round": "Play-In", "matchups": [ "..." ] },
      { "round": "First Round", "matchups": [ "..." ] },
      { "round": "Conference Semifinals", "matchups": [ "..." ] },
      { "round": "Conference Finals", "matchups": [ "..." ] },
      { "round": "NBA Finals", "matchups": [ "..." ] }
    ],
    "champion": null
  },
  "generated_at": "2026-08-22T00:00:00Z"
}
```

`bracket` is play-in-aware (2 play-in games per conference feed the conference's 7/8 seed before the main 8-team-per-conference bracket starts, no reseeding between rounds) and best-of-7-series-aware end to end (prediction + live record), real-vs-projected reconciled as postseason games complete -- same 3-state design (`projected`/`scheduled`/`final`) as every other bracket in this project. `cup_bracket` is projected-only (no reconciliation) -- see `playoff-bracket-feature` memory for the still-unresolved NBA Cup knockout field-size discrepancy. `cup`/`cup_bracket` are both `null` outside a season with NBA Cup groups configured.

### `GET /nba/live-scores`

Identical contract to `/nfl/live-scores` -- `player_stats` keys are `points`/`rebounds`/`assists`/etc. instead of NFL's passing/rushing categories.

### `GET /nba/predictions/events/{event_id}` and `.../players/{entity_id}`

Same contract as NFL's own. `leaders` has **no** position-based categories (`passing`/`rushing`/etc.) -- basketball has no single-player-dominates concept the way a QB does, so the three categories are `scoring`/`rebounding`/`assists`, each always a list (up to 5 candidates, re-sorted by this game's own predicted value, not recent-volume order). Player-prop `stat` values (`Terraform/dynamodb-sport-registry.tf`'s `nba_player_prop_stats`): `points`, `rebounds`, `assists`, `steals`, `blocks`, `three_pointers_made`.

## NCAAMBB routes (`/ncaambb/*`) -- live

Identical route shape to NBA, with these differences.

### `GET /ncaambb/events`

Day-grouped, same as NBA. Each event additionally carries a conference-game flag; predicted leaders use the same `scoring`/`rebounding`/`assists` categories as NBA (basketball's leader categories port near-exact, confirmed to match).

### `GET /ncaambb/models`

Same shape as NBA's, **plus** a `national-ranking` model (predicts AP Top 25 rank, poll-labeled -- see `NCAAMBB_FEATURE_ENGINEERING.md`) feeding March Madness/conference-tournament field seeding, same role NCAAFB's own `national-ranking` model plays for the CFP.

### `GET /ncaambb/season`

Recomputed **daily** (not weekly, like every other sport) -- NCAA MBB's much higher game volume (dozens of games most nights) means a week-old projection would be meaningfully stale much faster than for NFL/NCAAFB/NBA. Genuinely different shape again -- one bracket per conference tournament, plus the March Madness bracket, instead of one unified league bracket.

```json
{
  "sport": "ncaambb",
  "season": 2027,
  "standings": [
    {
      "team_id": "150",
      "conference": "ACC",
      "wins": 24,
      "losses": 6,
      "current_rank": 8,
      "projected_wins": 26.1,
      "projected_losses": 6.9,
      "conference_tournament_champion_probability": 0.31,
      "ncaa_tournament_probability": 0.94,
      "first_four_probability": 0.02,
      "round_of_64_probability": 0.92,
      "sweet_16_probability": 0.41,
      "elite_eight_probability": 0.18,
      "final_four_probability": 0.07,
      "championship_game_probability": 0.03,
      "national_champion_probability": 0.01,
      "name": "Duke Blue Devils",
      "abbreviation": "DUKE"
    }
  ],
  "conference_brackets": [
    { "conference": "ACC", "bracket": { "rounds": [ "..." ], "champion": null } }
  ],
  "march_madness_bracket": {
    "rounds": [
      { "round": "First Four", "matchups": [ "..." ] },
      { "round": "Round of 64", "matchups": [ "..." ] },
      { "round": "Round of 32", "matchups": [ "..." ] },
      { "round": "Sweet 16", "matchups": [ "..." ] },
      { "round": "Elite Eight", "matchups": [ "..." ] },
      { "round": "Final Four", "matchups": [ "..." ] },
      { "round": "Championship", "matchups": [ "..." ] }
    ],
    "champion": null
  },
  "generated_at": "2026-08-22T00:00:00Z"
}
```

`conference_brackets` has one entry per conference with at least 2 tracked members, seeded by conference-only record + point differential (no real head-to-head/RPI tiebreakers, same honest simplification NCAAFB's own conference-champion picking accepts). Each conference bracket's champion becomes that conference's automatic March Madness bid -- the one place the two brackets connect, so an auto-bid updates from "the model's pick" to "the real winner" the moment that conference's tournament finishes. `march_madness_bracket` is the 68-team field (one auto-bid per conference, at-large teams filled by the national-ranking model) flattened into one bracket -- First Four winners splice into their Round-of-64 slots (the same skip-connector UI pattern as NBA's Play-In), then 4 S-curve-seeded 16-team regions feed a neutral-site Final Four and Championship. Both bracket types are real-vs-projected reconciled the same 3-state way as every other bracket in this project. `standings`/`conference_brackets`/`march_madness_bracket` all omit their probability/bracket data (rather than erroring) on a day the conference-membership cache hasn't been refreshed yet or no `national-ranking` model is promoted -- see `NCAAMBB_FEATURE_ENGINEERING.md`'s "Conference membership" section.

### `GET /ncaambb/live-scores`

Identical contract to `/nfl/live-scores` -- `player_stats` keys match NBA's (`points`/`rebounds`/etc.).

### `GET /ncaambb/predictions/events/{event_id}` and `.../players/{entity_id}`

Identical contract to NBA's own, including the `scoring`/`rebounding`/`assists` `leaders` shape. Player-prop `stat` values (`Terraform/dynamodb-sport-registry.tf`'s `ncaambb_player_prop_stats`) -- same 6 names as NBA: `points`, `rebounds`, `assists`, `steals`, `blocks`, `three_pointers_made`.

## PGA routes (`/pga/*`) -- live

Field-event shape, genuinely different from every head-to-head sport above -- a golfer's own entity IS the participant, so there's no separate player-prop route at all. `/pga/events`, `/pga/models`, `/pga/live-scores` follow the same contract as the head-to-head sports (tournament-scoped instead of week-scoped for `/events`); `/pga/predictions/events/{event_id}` has its own shape.

### `GET /pga/predictions/events/{event_id}`

One response scores the **entire field** against every model that applies -- there's no per-golfer sub-route to call separately. See "Prediction-route caching semantics" above for the `200`/`203`/`202` shapes; the example below is the `200` (fresh) case, trimmed to one golfer.

```json
{
  "sport": "pga",
  "event_key": "SPORT#PGA#EVENT#401703511",
  "event_id": "401703511",
  "event_type": "field",
  "tournament_name": "THE PLAYERS Championship",
  "status": "in_progress",
  "par": 72,
  "cutline": {"projected_cut_score": {"value": -2.1, "model_version": 3}},
  "field": [
    {
      "entity_id": "9478",
      "name": "Scottie Scheffler",
      "predictions": {
        "top_10_probability": {"value": 0.61, "model_version": 4},
        "top_5_probability": {"value": 0.44, "model_version": 3},
        "projected_score_to_par": {"value": -9.2, "model_version": 5},
        "rounds": {
          "round_1": {"value": -3.0, "model_version": 2},
          "round_2": {"value": -2.4, "model_version": 2}
        }
      },
      "actual": {"finish_position": null, "score_to_par": -5, "rounds": ["..."]}
    }
  ],
  "generated_at": "2026-08-03T00:00:00Z"
}
```

`field` is sorted ascending by `projected_score_to_par` (lowest = best; falls back to `top_10_probability` descending if no score model is promoted) -- this ordering, not a separately trained ranking model, is what the frontend calls "field finish order." `predictions.rounds` only carries rounds a golfer has actually played or a live pre-round forecast exists for -- a projected-cut golfer's rounds 3-4 are never scored once their `cutline` comparison says they missed it. `actual` is present whenever the tournament has any real result yet (mid-tournament, not just once completed) -- `null` finish_position/score_to_par with a populated `rounds` list is a real, valid state (still playing). `cutline` is `null` if no cutline model is promoted yet.

**Team/individual match-play events** (Ryder Cup, Presidents Cup, WGC-Dell Technologies Match Play) return a different `event_type` (`"match_play"` per individual match, `"cup"` for the team aggregate) with a two-sided `home`/`away` prediction shape instead of a `field` array, scored against `match-win-probability`/`cup-win-probability` respectively -- see `design/DATA_SCHEMA.md`'s match-play section for the underlying event shapes this mirrors.

## F1 routes (`/f1/*`) -- live

Same field-event shape as PGA -- no player-prop route, one response scores the whole grid. `/f1/events`, `/f1/models`, `/f1/live-scores` follow the same contract as the head-to-head sports; `/f1/predictions/events/{event_id}` scores every driver in the field against win/podium/DNF/finish-position/qualifying-position, plus a constructor-level win-probability block. A Sprint weekend's Saturday sprint and Sunday Grand Prix are two separate events with two separate `event_id`s (`design/DATA_SCHEMA.md`), each scored against its own model set (the Sprint event's own `sprint-win-probability`/`sprint-podium-probability`/`sprint-grid` models, not the main race's).

### `GET /f1/predictions/events/{event_id}`

```json
{
  "sport": "f1",
  "event_key": "SPORT#F1#EVENT#2026-15",
  "event_id": "2026-15",
  "event_type": "field",
  "race_name": "Italian Grand Prix",
  "status": "scheduled",
  "field": [
    {
      "entity_id": "max_verstappen",
      "name": "Max Verstappen",
      "constructor_entity_id": "red_bull",
      "constructor_name": "Red Bull",
      "predictions": {
        "win_probability": {"value": 0.31, "model_version": 2},
        "podium_probability": {"value": 0.68, "model_version": 2},
        "dnf_probability": {"value": 0.06, "model_version": 1},
        "projected_finish_position": {"value": 1.8, "model_version": 3},
        "projected_qualifying_position": {"value": 1.2, "model_version": 2}
      },
      "actual": null
    }
  ],
  "constructors": [
    {"entity_id": "red_bull", "name": "Red Bull", "predictions": {"win_probability": {"value": 0.29, "model_version": 1}}}
  ],
  "generated_at": "2026-08-03T00:00:00Z"
}
```

`field` is sorted ascending by `projected_finish_position` -- the same "field finish order" role PGA's `projected_score_to_par` plays. `actual` follows the same "populated whenever real data exists, not gated on `completed`" rule as PGA's -- a DNF is a real, distinct `status` value (`"dnf"`), not a null finish position with no explanation.

## Error responses

Every route shares the same error shape: `{"error": "<message>"}`.

| Status | When |
|---|---|
| `400` | `stat` query parameter missing on a player-prop route |
| `404` | Unknown route, or the referenced event doesn't exist |
| `422` | The event exists but is missing data needed to build a prediction (e.g. no identifiable participants) |
| `503` | No promoted model version exists yet for a required prediction target, or (`/{sport}/season`) no season projection has been computed yet |
| `500` | Unhandled server error (logged to CloudWatch, not surfaced in detail to the client) |

## Rate limiting

A usage plan throttles the whole API (all routes, all sports) to a 60 request/second sustained rate with a burst of 100 (`aws_api_gateway_usage_plan.main` / `aws_api_gateway_method_settings.main`, `Terraform/api-gateway-nfl-predict.tf`) -- raised from an original 10 rps/20 burst after a real production 429 incident (every cached prediction on a page going stale/missing at once, fanning out ~130 near-simultaneous requests for a full NCAAFB week's slate; see the `api-gateway-429-thundering-herd-fix` memory). No API key is required -- Cognito already authenticates every caller, so the usage plan exists purely for throttling, not per-key metering.

## CORS

Production traffic never triggers a browser CORS check (frontend and API share one CloudFront origin). Every route above also has an `OPTIONS` preflight method (`authorization = "NONE"`, mock integration returning `Access-Control-Allow-Origin: *`) purely to support local frontend development, where `flutter run` serves the app from `http://localhost` while still calling the real deployed API cross-origin.
