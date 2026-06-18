# Data Schema

The schema is designed around the two data shapes described in `CLAUDE.md`: head-to-head events (two participants) and field events (N participants). Both shapes share the same underlying tables — what differs is how many entries appear in an event's `participants` list and what a "result" looks like for each.

## Entities table

One row per team or player, regardless of sport.

| Attribute | Example | Notes |
|---|---|---|
| `PK` | `SPORT#NFL#ENTITY#KC` | Partition key prefixed by sport so per-sport queries don't scan unrelated data |
| `entity_id` | `KC` | Source system's team/player identifier |
| `sport` | `nfl` | One of the six supported sports |
| `entity_type` | `team` or `player` | Team sports use both (team-level and player-level records); golf and F1 are primarily player-level |
| `name` | `Kansas City Chiefs` | Display name |
| `metadata` | `{conference, division, ...}` | Sport-specific attributes, stored as a flexible map rather than fixed columns |

## Events table

One row per game, match, tournament, or race.

| Attribute | Example | Notes |
|---|---|---|
| `PK` | `SPORT#NFL#EVENT#2025-W04-KC-LAC` | Partition key prefixed by sport |
| `event_id` | `2025-W04-KC-LAC` | Source system's game/event identifier |
| `sport` | `nfl` | |
| `event_type` | `head_to_head` or `field` | Determines how `participants` is interpreted |
| `event_date` | `2025-09-28` | |
| `status` | `scheduled`, `completed` | |
| `participants` | see below | Array — length 2 for head-to-head, length N for field events |

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

## Predictions table

One row per event per model version, kept separate from raw results so re-running a model doesn't overwrite history.

| Attribute | Example | Notes |
|---|---|---|
| `PK` | `SPORT#NFL#EVENT#2025-W04-KC-LAC` | |
| `SK` | `MODEL#v3` | Sort key — lets you keep predictions from multiple model versions for the same event |
| `predicted_value` | `{ "KC_win_prob": 0.61 }` or `{ "win_prob": {...}, "top10_prob": {...} }` | Shape depends on event_type |
| `model_version` | `v3` | |
| `generated_at` | `2025-09-26T14:00:00Z` | |

## Sport registry table (added in Phase 4)

Drives the Step Functions Map state — this is what makes onboarding a new sport a data change rather than a code change to shared orchestration.

| Attribute | Example | Notes |
|---|---|---|
| `PK` | `SPORT#PGA` | |
| `sport` | `pga` | |
| `event_type` | `field` | |
| `adapter_module` | `adapters.pga` | Where the orchestrator looks for `fetch()`, `normalize()`, etc. |
| `polling_cadence` | `weekly` | Drives how often the Map state invokes this adapter |
| `current_model_version` | `v1` | Which model version the inference Lambda should serve by default |
| `active` | `true` | Lets you pause a sport (e.g., off-season) without deleting its configuration |

## Access patterns and indexes

The `SPORT#<sport>#...` prefix on every partition key means a query for "all NFL events" or "all NFL entities" is a single partition query, not a table scan — this matters once six sports share the same tables. Add a global secondary index on `event_date` (scoped within the sport prefix) once you need date-range queries like "this week's games," and an index on `entity_id` once the frontend needs "show me this team's full history" — don't add either speculatively before a feature actually needs it, since each GSI roughly doubles the write cost for that table.
