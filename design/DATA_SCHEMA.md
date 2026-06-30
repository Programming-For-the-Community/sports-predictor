# Data Schema

The schema is designed around two axes: the **event shape** (head-to-head vs. field, described in `CLAUDE.md`) and the **prediction granularity** (event-level outcome vs. individual-player stats). These are orthogonal — a team sport like NFL is head-to-head at the event level but still needs per-player stat lines underneath it, while a field-event sport like PGA already gets player-level granularity for free since the entity *is* the player.

Three tables carry this: `entities` (who), `events` (what happened at the game/match/tournament level), and `player_game_stats` (what an individual player did within a given event — only populated for team sports, since field-event results already live in `events.participants`). `predictions` mirrors that same split: event-level predictions vs. player-prop predictions.

## Entities table

One row per team or player, regardless of sport.

| Attribute | Example | Notes |
|---|---|---|
| `PK` | `SPORT#NFL#ENTITY#KC` | Partition key prefixed by sport so per-sport queries don't scan unrelated data |
| `entity_id` | `KC` | Source system's team/player identifier |
| `sport` | `nfl` | One of the six supported sports |
| `entity_type` | `team` or `player` | Team sports use both (team-level and player-level records); golf and F1 are primarily player-level |
| `name` | `Kansas City Chiefs` | Display name |
| `metadata` | `{conference, division, ...}` for a team; `{team_id: "KC", position: "QB"}` for a player | Sport-specific attributes, stored as a flexible map rather than fixed columns. For player entities in team sports, `team_id` is the current roster link — it's what lets a feature pipeline join a player to their team's pace/scheme context, and it changes on trades, so treat it as current-state, not historical |

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

## Player game stats table

One row per player per event, for team sports only (NFL, NCAA FB, NBA, NCAA MBB). This is where individual performance lives — `events.participants` for these sports only carries team-level results, so without this table there's no record of what any given player actually did. Field-event sports don't need this table: a golfer's or driver's per-event performance is already the `result` in `events.participants`, since the entity and the participant are the same thing.

| Attribute | Example | Notes |
|---|---|---|
| `PK` | `SPORT#NFL#EVENT#2025-W04-KC-LAC` | Same partition key as the parent event — querying "all player stat lines for this game" is a single partition query, and it keeps the table consistent with the rest of the schema's `SPORT#<sport>#EVENT#<id>` convention |
| `SK` | `PLAYER#mahomes-patrick` | Sort key — lets one event partition hold every player who appeared in that game |
| `entity_id` | `mahomes-patrick` | Matches the player's `entity_id` in the entities table |
| `team_id` | `KC` | Which side the player was on for this event — needed because the player entity's `team_id` reflects *current* roster state, not who they played for historically (trades, season-over-season movement) |
| `stat_line` | `{ "passing_yards": 312, "passing_tds": 3, "interceptions": 1 }` | Sport-specific flexible map, same pattern as `entities.metadata` — a basketball box score and a football box score share nothing but the shape of the container |
| `started` | `true` | Cheap signal for feature engineering (did this player start vs. come off the bench) without needing snap counts or play-by-play |

## Predictions table

One row per event per model version for event-level outcomes, or one row per event-player-model for player props — kept separate from raw results so re-running a model doesn't overwrite history.

| Attribute | Example | Notes |
|---|---|---|
| `PK` | `SPORT#NFL#EVENT#2025-W04-KC-LAC` | |
| `SK` | `MODEL#v3` for an event-outcome prediction, `MODEL#v3#PLAYER#mahomes-patrick` for a player-prop prediction | Sort key — lets you keep predictions from multiple model versions for the same event, and lets dozens of player-prop rows coexist in the same event partition without colliding with the event-level prediction or each other |
| `predicted_value` | `{ "KC_win_prob": 0.61 }`, `{ "win_prob": {...}, "top10_prob": {...} }`, or `{ "passing_yards": {"mean": 287, "over_265_5_prob": 0.54}, "passing_tds": {"mean": 2.1} }` | Shape depends on event_type for event-level rows, and on the stat being predicted for player-prop rows — these are different statistical problems with different targets, same as the head-to-head/field-event split, so don't force one shape to fit both |
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

`player_game_stats` needs its own `entity_id` GSI sooner rather than later, even under the same "don't add speculatively" rule — its primary key is event-first (`PK = SPORT#...#EVENT#...`), so "this player's last N games" (the core input to most player-prop features, e.g. rolling averages) isn't answerable from the base table at all, unlike team history which can at least be brute-forced from `events`.
