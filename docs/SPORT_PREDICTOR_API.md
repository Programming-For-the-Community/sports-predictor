# Sport Predictor API

This documents every route live on the shared API Gateway REST API (`Terraform/api-gateway.tf`, `Terraform/api-gateway-{nfl,ncaafb,nba}-predict.tf`, `Terraform/api-gateway-{nfl,ncaafb,nba}-live-scores.tf`), fronted by CloudFront at the app's single public domain -- see `design/ARCHITECTURE.md`'s serving-layer diagram. Per `design/CLAUDE.md`'s registry-driven onboarding principle, every head-to-head sport gets the identical `/{sport}/events`, `/{sport}/models`, `/{sport}/season`, `/{sport}/live-scores`, `/{sport}/predictions/...` route shape -- nothing about this contract is sport-specific except the path prefix and each sport's own player-prop stat names. **NFL and NCAAFB are fully live**; **NBA's routes exist in Terraform but are not yet functional** -- see the NBA section below.

Formerly `NFL_API.md` -- renamed and expanded to cover every onboarded sport under one doc, since the contract genuinely is shared (this file documents the contract once, then calls out each sport's own concrete values).

## Base URL

`https://<project>.<domain>` -- the CloudFront distribution's own domain (`local.domain` in `Terraform/locals.tf`, exposed as the `api_endpoint` Terraform output). CloudFront routes `/nfl/*`, `/ncaafb/*`, `/nba/*` to API Gateway and everything else to the Flutter frontend's S3 bucket (`Terraform/cloudfront.tf`), so the frontend and API share one origin -- no CORS is involved in production traffic.

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

`player_stats` is only present for an event currently `live` -- best-effort per-player live stats for that event's predicted leaders (see `design/PROJECT_PLAN.md`'s live-leader-stats feature), omitted (not `null`) if that fetch failed this tick. `events` is `{}` if nothing is currently in a live-poll window.

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

## NBA routes (`/nba/*`) -- **not yet live**

`Terraform/api-gateway-nba-predict.tf` and `Terraform/api-gateway-nba-live-scores.tf` already wire up the identical route shape (`/nba/events`, `/nba/models`, `/nba/season`, `/nba/live-scores`, `/nba/predictions/events/{event_id}`, `.../players/{entity_id}`) pointing at `aws_lambda_function.nba_predict_read` -- but that Lambda is currently a placeholder stub (`Terraform/lambda-nba-predict-read.tf`'s own inline-fabricated ZIP), not real serving code. **Every route above returns a non-functional placeholder response today, not real data.** This is expected, deliberate scaffolding-ahead-of-schedule (see the `project-nba-onboarding` memory's note on why the container-image `nba-predict` Lambda needed a placeholder image from day one) -- not a bug to file.

Feature engineering and training (Sub-phase 3A step 5) are done -- see `NBA_FEATURE_ENGINEERING.md` -- but no model has been trained against real data yet, and `Source/aws-lambdas/nba/predict-read/` doesn't exist as real code at all (step 6). Once step 6 lands, this section gets filled in with real routes, response examples, and NBA's own player-prop `stat` values (intended: `points`, `rebounds`, `assists`, `steals`, `blocks`, `three_pointers_made`, per `Terraform/dynamodb-sport-registry.tf`'s `nba_player_prop_stats`) -- the contract is expected to be structurally identical to NFL's/NCAAFB's `predictions/events/{event_id}` shape, minus the position-based `leaders` categories (NBA has no QB/RB/WR-equivalent leader concept -- see `NBA_FEATURE_ENGINEERING.md`'s "No position-leader tracking" section; the eventual leaders panel groups by scoring/rebounding/assists instead). Step 7 (live-scores) and step 8 (season simulation, play-in-aware) are also still pending -- `/nba/live-scores` and `/nba/season` have no real backing logic yet either.

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
