# NFL API

This documents the routes currently live under `/nfl/*` on the shared API Gateway REST API (`Terraform/api-gateway.tf`, `Terraform/api-gateway-nfl-predict.tf`). Every route is served by one Lambda (`Source/aws-lambdas/nfl/predict/handler.py`), fronted by CloudFront at the app's single public domain — see `design/ARCHITECTURE.md`'s serving-layer diagram. Per `design/CLAUDE.md`'s registry-driven onboarding principle, every other head-to-head sport (NCAA FB, NBA, NCAA MBB) gets the identical `/{sport}/events`, `/{sport}/models`, `/{sport}/season`, `/{sport}/predictions/...` route shape once it has a backend — nothing about this contract is NFL-specific except the path prefix and the player-prop stat names.

## Base URL

`https://<project>.<domain>` — the CloudFront distribution's own domain (`local.domain` in `Terraform/locals.tf`, exposed as the `api_endpoint` Terraform output). CloudFront routes `/nfl/*` to API Gateway and everything else to the Flutter frontend's S3 bucket (`Terraform/cloudfront.tf`), so the frontend and API share one origin — no CORS is involved in production traffic.

## Authentication

Every route below requires a valid Cognito-issued JWT. API Gateway validates it via a `COGNITO_USER_POOLS` authorizer (`aws_api_gateway_authorizer.cognito`, `Terraform/api-gateway.tf`) before the Lambda ever runs — the Lambda itself never checks auth.

Send the access token as the raw `Authorization` header value, **not** prefixed with `Bearer `:

```
Authorization: <access_token>
```

A missing or invalid token gets rejected by API Gateway directly (401/403) — it never reaches `handler.py`.

## Routes

### `GET /nfl/events`

Lists events for the current data set, filtered by status.

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
      "status": "scheduled",
      "season": 2026,
      "season_type": 2,
      "week": 2,
      "participants": [
        {"entity_id": "12", "role": "home", "result": null},
        {"entity_id": "13", "role": "away", "result": null}
      ]
    }
  ]
}
```

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
      "candidates": [
        {"algorithm": "xgboost", "score": 0.65},
        {"algorithm": "logistic_regression", "score": 0.71},
        {"algorithm": "random_forest_classifier", "score": 0.69},
        {"algorithm": "mlp_classifier", "score": 0.74}
      ]
    },
    {
      "model_name": "score-margin",
      "algorithm": "xgboost",
      "version": 3,
      "trained_at": "2026-07-30T00:00:00Z",
      "rmse": 9.8,
      "mae": 7.4,
      "naive_baseline_rmse": 12.1,
      "naive_baseline_mae": 9.6,
      "top_features": [{"feature": "elo_diff", "importance": 0.19}],
      "candidates": [
        {"algorithm": "xgboost", "score": 9.8},
        {"algorithm": "elastic_net", "score": 10.4},
        {"algorithm": "random_forest_regressor", "score": 10.1},
        {"algorithm": "mlp_regressor", "score": 11.2}
      ]
    }
  ]
}
```

`accuracy`/`log_loss` are present for a classification-task model (`win-probability`); `rmse`/`mae` are present for every regression-task model (`score-margin`, `home-score`, `away-score`, and each `player-prop-<stat>` model). `naive_baseline_accuracy` (classifier) and `naive_baseline_rmse`/`naive_baseline_mae` (regressors) are the same metric against a trivial baseline (classifier: always pick the home team; regressor: predict the player's own rolling average) -- the frontend uses these to show skill relative to that baseline instead of surfacing `log_loss`/`rmse` directly, which aren't intuitive without ML background. `candidates` is every algorithm `library.ml.backtest.run_backtest` actually tried for this target on this run, each with a `score` on the same metric this card's own gate metric uses (lower is always better) -- see `library/ml/model_types.py` for the full algorithm roster, which differs by task (a classifier target competes among `xgboost`/`logistic_regression`/`random_forest_classifier`/`mlp_classifier`; a regressor target among `xgboost`/`elastic_net`/`random_forest_regressor`/`mlp_regressor`). Both `naive_baseline_*` and `candidates` are `null`/absent on any model card trained before the relevant feature existed. `top_features` is pre-sorted descending by importance and capped at 5 server-side.

A model that's never had a version promoted simply doesn't appear in this list.

### `GET /nfl/season`

The current season's standings (real record + Elo Monte Carlo projection) and, per tracked player-prop stat, a top-10 season-long leaderboard. Recomputed fully on every request — nothing here is cached or precomputed on a schedule. See `Source/aws-lambdas/nfl/predict/season_simulation.py` for the simulation itself.

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
      "projected_wins": 13.4,
      "division_winner_probability": 0.91,
      "playoff_probability": 0.98,
      "championship_probability": 0.22
    }
  ],
  "leaderboards": {
    "passing_yards": [
      {"entity_id": "3139477", "name": "Patrick Mahomes", "current_total": 2100.0, "projected_total": 4300.0}
    ],
    "passing_touchdowns": [ /* ... */ ],
    "rushing_yards": [ /* ... */ ],
    "rushing_touchdowns": [ /* ... */ ],
    "receiving_yards": [ /* ... */ ],
    "receiving_touchdowns": [ /* ... */ ],
    "defensive_sacks": [ /* ... */ ]
  },
  "generated_at": "2026-08-03T00:00:00Z"
}
```

`standings` is sorted by `projected_wins` descending (flat list, not grouped by division — division/conference membership is `library/features/nfl_teams.py`'s `TEAM_DIVISIONS`, a frontend-side lookup by `team_id` if grouping is needed). `leaderboards` is `null` if the backend couldn't compute it for this request (best-effort field — never fails the standings above over it).

### `GET /nfl/predictions/events/{event_id}`

Live prediction for one event: win probability, margin, home/away score, plus each team's passing/receiving/rushing/sacks leaders (each scored against its own player-prop model). `{event_id}` is a raw ESPN event id (e.g. `"401547417"`), not the internal `SPORT#NFL#EVENT#...` storage key.

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
  "generated_at": "2026-08-03T00:00:00Z"
}
```

`receiving`/`rushing`/`sacks` are arrays (up to 3, 2, and 3 candidates respectively); `passing` is a single object or `null`. `leaders` as a whole is `null` if it couldn't be computed — the core `predictions` block above never fails over it. Every prediction here is also logged to the predictions table for audit (`design/DATA_SCHEMA.md`'s Predictions table) — this route only ever writes there, never reads a past prediction back.

### `GET /nfl/predictions/events/{event_id}/players/{entity_id}`

One player-prop prediction for one player in one game.

**Query parameters**

| Name | Required | Values |
|---|---|---|
| `stat` | **yes** | `passing_yards`, `passing_touchdowns`, `rushing_yards`, `rushing_touchdowns`, `receiving_yards`, `receiving_touchdowns`, `defensive_sacks` |

The stat list's source of truth is `Terraform/scheduler-nfl-train-player-prop-model.tf`'s `nfl_player_prop_stats` map — an 8th stat later means adding one entry there (and retraining), not a route change. Omitting `stat` returns `400`.

**Response `200`**

```json
{
  "event_key": "SPORT#NFL#EVENT#401547417",
  "entity_key": "SPORT#NFL#ENTITY#3139477",
  "stat": "passing_yards",
  "prediction": {"value": 267.4, "model_version": 4},
  "generated_at": "2026-08-03T00:00:00Z"
}
```

## Error responses

Every route shares the same error shape: `{"error": "<message>"}`.

| Status | When |
|---|---|
| `400` | `stat` query parameter missing on the player-prop route |
| `404` | Unknown route, or the referenced event doesn't exist |
| `422` | The event exists but is missing data needed to build a prediction (e.g. no identifiable participants) |
| `503` | No promoted model version exists yet for a required prediction target |
| `500` | Unhandled server error (logged to CloudWatch, not surfaced in detail to the client) |

## Rate limiting

A usage plan throttles the whole API (all routes, all sports) to a 10 request/second sustained rate with a burst of 20 (`aws_api_gateway_usage_plan.main`). No API key is required — Cognito already authenticates every caller, so the usage plan exists purely for throttling, not per-key metering.

## CORS

Production traffic never triggers a browser CORS check (frontend and API share one CloudFront origin). Every route above also has an `OPTIONS` preflight method (`authorization = "NONE"`, mock integration returning `Access-Control-Allow-Origin: *`) purely to support local frontend development, where `flutter run` serves the app from `http://localhost` while still calling the real deployed API cross-origin.
