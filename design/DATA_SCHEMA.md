# Data Schema

The schema is designed around two axes: the **event shape** (head-to-head vs. field, described in `CLAUDE.md`) and the **prediction granularity** (event-level outcome vs. individual-player stats). These are orthogonal — a team sport like NFL is head-to-head at the event level but still needs per-player stat lines underneath it, while a field-event sport like PGA already gets player-level granularity for free since the entity *is* the player.

Three tables carry this: `entities` (who), `events` (what happened at the game/match/tournament level), and `player_game_stats` (what an individual player did within a given event — only populated for team sports, since field-event results already live in `events.participants`). `predictions` mirrors that same split: event-level predictions vs. player-prop predictions.

## Entities table

One row per team or player, regardless of sport.

| Attribute | Example | Notes |
|---|---|---|
| `entity_key` | `SPORT#NFL#ENTITY#KC` | Partition key prefixed by sport so per-sport queries don't scan unrelated data |
| `entity_id` | `KC` | Source system's team/player identifier |
| `sport` | `nfl` | One of the six supported sports |
| `entity_type` | `team` or `player` | Team sports use both (team-level and player-level records); golf and F1 are primarily player-level |
| `name` | `Kansas City Chiefs` | Display name |
| `metadata` | `{abbreviation, location, nickname}` for a team; `{team_id: "KC", jersey: "15"}` for a player | Sport-specific attributes, stored as a flexible map rather than fixed columns. For player entities in team sports, `team_id` is the current roster link — it's what lets a feature pipeline join a player to their team's pace/scheme context, and it changes on trades, so treat it as current-state, not historical. Division/conference are NOT here — ESPN's teams endpoint doesn't return them, and since they're essentially static (last realignment 2002), they live in a hardcoded table (`library/features/nfl_teams.py`) instead |

## Events table

One row per game, match, tournament, or race.

| Attribute | Example | Notes |
|---|---|---|
| `event_key` | `SPORT#NFL#EVENT#2025-W04-KC-LAC` | Partition key prefixed by sport |
| `event_id` | `2025-W04-KC-LAC` | Source system's game/event identifier |
| `sport` | `nfl` | |
| `event_type` | `head_to_head` or `field` | Determines how `participants` is interpreted |
| `event_date` | `2025-09-28` | |
| `status` | `scheduled`, `completed` | |
| `participants` | see below | Array — length 2 for head-to-head, length N for field events |
| `season`, `season_type`, `week` | `2025`, `2`, `4` | Written by every adapter's normalize step; `season_type`/`week` also double as feature-engineering inputs (how far into the season a game falls) |
| `venue_indoor`, `venue_city`, `venue_state` | `false`, `"Green Bay"`, `"WI"` | From the source API's venue data — the same response already fetched for `participants`, not a separate call. `venue_indoor` is a real feature input; city/state are carried for reference but excluded from training (raw strings aren't model-consumable without encoding) |
| `weather_temperature` | `52` or `null` | Frequently `null` — most reliably for indoor games, where it doesn't apply, but also for outdoor games the source API simply didn't report on. A partial signal, not a guaranteed one |
| `home_coach_id`, `home_coach_name`, `home_coach_experience`, `home_coach_season_win_pct` (+ `away_*`) | `"4872749"`, `"Mike LaFleur"`, `0`, `0.4706` | NFL-only so far. Attached by ingest (`aws-lambdas/nfl/ingest/handler.py`'s `_enrich_events`, via ESPN's other "core" API) before every event write, so every event carries whatever coach data was current as of its most recent ingest cycle. `experience`/`season_win_pct` are ESPN's own numbers (tenure with this team, this season's win rate), used directly as feature inputs — no derivation. `*_coach_id`/`*_coach_name` are identifiers only, never fed to a model, same rule as every other raw id/name field here. Absent entirely on any event ingested before this shipped — forward-only, no historical backfill (ESPN's coach data is current-state only, no historical-as-of-date endpoint exists) |
| `home_injuries`, `away_injuries` | `[{"entity_id": "4912218", "status": "Questionable"}, ...]` | NFL-only so far. Each team's current injury report as of the most recent ingest cycle, same enrichment as coach data above. Re-fetched daily (not just the historical Tue/Wed ingest cadence) since injury reports genuinely change through the week in a way box scores and coach data don't — see `Terraform/scheduler-nfl-ingest.tf`. An empty list is real signal ("checked, nobody's hurt"), distinct from the key being absent entirely ("never checked") |
| `home_depth_chart`, `away_depth_chart` | `{"qb": {"position": {...}, "athletes": [{"id": "4912218"}, ...]}, ...}` | NFL-only so far. Filtered to QB/RB/WR only (the positions live prediction's leader-selection needs — see `aws-lambdas/nfl/predict/live_features.py`), not ESPN's full ~25-position payload. Same enrichment/refresh cadence as injuries above |

**Head-to-head `participants` shape** (NFL, NCAA FB, NBA, NCAA MBB):
```json
[
  { "entity_id": "KC", "role": "home", "result": { "score": 27, "won": true } },
  { "entity_id": "LAC", "role": "away", "result": { "score": 20, "won": false } }
]
```

**Field-event `participants` shape** (PGA Tour, F1):
```json
[
  { "entity_id": "scheffler", "result": { "finish_position": 1, "score_to_par": -12 } },
  { "entity_id": "fitzpatrick", "result": { "finish_position": 2, "score_to_par": -10 } }
]
```

This is the same array structure either way — adapters for field-event sports just populate more entries, and feature/model code for those sports treats `participants` as a ranked list rather than a head-to-head pair.

## Player game stats table

One row per player per event, for team sports only (NFL, NCAA FB, NBA, NCAA MBB). This is where individual performance lives — `events.participants` for these sports only carries team-level results, so without this table there's no record of what any given player actually did. Field-event sports don't need this table: a golfer's or driver's per-event performance is already the `result` in `events.participants`, since the entity and the participant are the same thing.

| Attribute | Example | Notes |
|---|---|---|
| `event_key` | `SPORT#NFL#EVENT#2025-W04-KC-LAC` | Same partition key as the parent event — querying "all player stat lines for this game" is a single partition query, and it keeps the table consistent with the rest of the schema's `SPORT#<sport>#EVENT#<id>` convention |
| `player_key` | `SPORT#NFL#PLAYER#mahomes-patrick` | Sort key — lets one event partition hold every player who appeared in that game. Sport-scoped like every other key here, since this table (like `events` and `entities`) is shared across every sport, not one table per sport — an unscoped key would risk two different sports' athletes colliding under the same raw id once a second sport's data lands |
| `entity_id` | `mahomes-patrick` | Matches the player's `entity_id` in the entities table |
| `team_id` | `KC` | Which side the player was on for this event — needed because the player entity's `team_id` reflects *current* roster state, not who they played for historically (trades, season-over-season movement) |
| `stat_line` | `{ "passing_yards": 312, "passing_tds": 3, "interceptions": 1 }` | Sport-specific flexible map, same pattern as `entities.metadata` — a basketball box score and a football box score share nothing but the shape of the container |
| `started` | `true` | Cheap signal for feature engineering (did this player start vs. come off the bench) without needing snap counts or play-by-play |

## Team game stats table

One row per team per event, for team sports only. ESPN's box score already computes team-level aggregates (turnovers, total yards, time of possession, third/fourth-down and red-zone efficiency, etc.) that aren't derivable by summing individual player stat lines.

| Attribute | Example | Notes |
|---|---|---|
| `event_key` | `SPORT#NFL#EVENT#2025-W04-KC-LAC` | Same partition key as the parent event |
| `team_key` | `TEAM#KC` | Sort key — lets one event partition hold both teams' rows |
| `team_id` | `KC` | Matches the team's `entity_id` in the entities table |
| `stat_line` | `{ "turnovers": 1, "total_yards": 350, "possession_time_seconds": 1800, "third_down_conversions": 6, "third_down_attempts": 12 }` | Same flexible-map pattern as `player_game_stats.stat_line` |

## Predictions table

One row per event per model per version for event-level outcomes, or one row per event-player-model for player props — kept separate from raw results so re-running a model doesn't overwrite history. Written by the inference Lambda (`Source/aws-lambdas/nfl/predict/handler.py`) as an audit trail every time it computes a prediction — see "Serving layer" below for the request contract that triggers a write. The IAM role backing that Lambda only has `PutItem` on this table (see `Terraform/iam-lambda-inference.tf`); it never reads a past prediction back.

| Attribute | Example | Notes |
|---|---|---|
| `event_key` | `SPORT#NFL#EVENT#401547417` | The raw ESPN event id, same `event_key()` builder (`library/schema/keys.py`) every other NFL table uses |
| `model_key` | `MODEL#win-probability#v6` for an event-outcome prediction, `MODEL#player-prop-passing-yards#v5#PLAYER#mahomes-patrick` for a player-prop prediction | Sort key — lets you keep predictions from multiple model versions for the same event, and lets dozens of player-prop rows coexist in the same event partition without colliding with the event-level prediction or each other. The model name matches whatever `model_common.py` named that model at training time (`win-probability`, `score-margin`, `home-score`, `away-score`, or `player-prop-<stat>` — see `Terraform/s3-model-artifacts.tf` for the matching S3 path convention), and the version is read off that model's own `current.json` pointer at request time, so it's embedded in the key rather than tracked as a separate top-level attribute |
| `predicted_value` | `{"home_win_probability": 0.62, "model_version": 6}` for win-probability, `{"value": 3.2, "model_version": 2}` for margin/score/player-prop rows | Shape depends on which model produced it — a classifier's raw output is already a probability, a regressor's is a raw number, and forcing them into one shared shape would just add a layer of translation nothing reads |
| `generated_at` | `2026-08-02T14:00:00Z` | ISO 8601, UTC, stamped by the inference Lambda at prediction time |

## Serving layer

The inference Lambda (`Source/aws-lambdas/nfl/predict/handler.py`) sits behind API Gateway (`Terraform/api-gateway-nfl-predict.tf`), authenticated by the same Cognito authorizer every other route uses (see "Access control" in `ARCHITECTURE.md`). Every prediction is computed live from current DynamoDB/S3 state on each request — nothing is pre-computed or served from a cache.

| Route | Returns |
|---|---|
| `GET /nfl/predictions/events/{event_id}` | Win probability, margin, home score, and away score for one matchup, from one shared live feature vector (`live_features.build_live_event_features`) scored against all four event-level models |
| `GET /nfl/predictions/events/{event_id}/players/{entity_id}?stat=passing_yards` | One player-prop prediction for one player in one game (`live_features.build_live_player_features`), scored against the `player-prop-<stat>` model matching the `stat` query parameter |

`event_id`/`entity_id` are raw ESPN ids, translated internally to `SPORT#NFL#EVENT#...`/`SPORT#NFL#ENTITY#...` keys the same way every other NFL adapter does — callers never construct a DynamoDB key themselves. Packaged as a container image rather than the zip format `ingest`/`normalize` use (`Terraform/lambda-nfl-predict.tf`) — xgboost pulls in numpy and scipy, which alone measure ~225MB unzipped for this runtime, leaving almost no headroom under Lambda's 250MB unzipped zip limit; container Lambdas get a 10GB image limit instead.

## Sport registry table (added in Phase 4)

Drives the Step Functions Map state — this is what makes onboarding a new sport a data change rather than a code change to shared orchestration.

| Attribute | Example | Notes |
|---|---|---|
| `sport_key` | `SPORT#PGA` | |
| `sport` | `pga` | |
| `event_type` | `field` | |
| `adapter_module` | `adapters.pga` | Where the orchestrator looks for `fetch()`, `normalize()`, etc. |
| `polling_cadence` | `weekly` | Drives how often the Map state invokes this adapter |
| `current_model_version` | `v1` | Which model version the inference Lambda should serve by default |
| `active` | `true` | Lets you pause a sport (e.g., off-season) without deleting its configuration |

## Access patterns and indexes

The `SPORT#<sport>#...` prefix on every partition key means a query for "all NFL events" or "all NFL entities" is a single partition query, not a table scan — this matters once six sports share the same tables. Add a global secondary index on `event_date` (scoped within the sport prefix) once you need date-range queries like "this week's games," and an index on `entity_id` once the frontend needs "show me this team's full history" — don't add either speculatively before a feature actually needs it, since each GSI roughly doubles the write cost for that table.

`player_game_stats` needs its own `entity_id` GSI sooner rather than later, even under the same "don't add speculatively" rule — its primary key is event-first (`PK = SPORT#...#EVENT#...`), so "this player's last N games" (the core input to most player-prop features, e.g. rolling averages) isn't answerable from the base table at all, unlike team history which can at least be brute-forced from `events`.
