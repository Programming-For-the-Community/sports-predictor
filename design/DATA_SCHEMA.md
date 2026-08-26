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
| `home_injuries`, `away_injuries` | `[{"entity_id": "4912218", "status": "Questionable"}, ...]` | NFL-only so far. Each team's current injury report as of the most recent ingest cycle, same enrichment as coach data above. Re-fetched daily (not just the historical Tue/Wed ingest cadence) since injury reports genuinely change through the week in a way box scores and coach data don't — see `Terraform/sfn-ingest-orchestrator.tf`'s daily schedule. An empty list is real signal ("checked, nobody's hurt"), distinct from the key being absent entirely ("never checked") |
| `home_depth_chart`, `away_depth_chart` | `{"qb": {"position": {...}, "athletes": [{"id": "4912218"}, ...]}, ...}` | NFL-only so far. Filtered to QB/RB/WR only (the positions live prediction's leader-selection needs — see `aws-lambdas/nfl/predict/live_features.py`), not ESPN's full ~25-position payload. Same enrichment/refresh cadence as injuries above |

**Head-to-head `participants` shape** (NFL, NCAA FB, NBA, NCAA MBB):
```json
[
  { "entity_id": "KC", "role": "home", "result": { "score": 27, "won": true } },
  { "entity_id": "LAC", "role": "away", "result": { "score": 20, "won": false } }
]
```

**Field-event `participants` shape** (PGA Tour, F1) -- confirmed against ESPN's real golf leaderboard response (`site.web.api.espn.com/apis/site/v2/sports/golf/leaderboard?event={id}`, a different host+path than the other sports' `.../golf/pga/scoreboard`, which returns the same competitors but without `status.position`/`earnings`) before locking the field names in, per the project's own "verify raw fields before writing code against them" rule:
```json
[
  {
    "entity_id": "9478",
    "result": {
      "finish_position": 12,
      "is_tie": true,
      "status": "finished",
      "score_to_par": -2,
      "total_strokes": 276,
      "earnings": 177500.0,
      "rounds": [
        { "round": 1, "score_to_par": -2, "total_strokes": 68.0 },
        { "round": 2, "score_to_par": 0, "total_strokes": 70.0 },
        { "round": 3, "score_to_par": -1, "total_strokes": 69.0 },
        { "round": 4, "score_to_par": -1, "total_strokes": 69.0 }
      ]
    }
  }
]
```
`rounds` -- added 2026-08-25 for per-round score projection features/models (`library/features/pga.py`, `docs/PGA_FEATURE_ENGINEERING.md`). One entry per round the golfer ACTUALLY PLAYED, parsed from ESPN's own `competitor["linescores"]` (confirmed live to always be present at the round grain, 100% of competitors checked across two real tournaments -- the further-nested hole-by-hole breakdown within each round is not consistently populated and isn't parsed). A cut golfer's own `rounds` naturally has only 2 entries, not 4 -- confirmed live directly on a real missed-cut player -- so no conditional cut-logic is needed anywhere that reads this list; a golfer who didn't play rounds 3-4 simply isn't in it for those rounds. Each round's own `score_to_par`/`total_strokes` are parsed through the exact same "E"/"-" special-casing the tournament-level total uses.
No `role` key -- that's a head-to-head-only concept (home/away), meaningless for a ranked field. `finish_position` is parsed from ESPN's `status.position.displayName` (`"T26"` -> 26 + `is_tie: true`, `"1"` -> 1 + `is_tie: false`, `"-"` -> `null` for a player with no finish, e.g. cut/withdrawn/hasn't-played-yet). `status` is this project's own normalized vocabulary mapped from ESPN's `status.type.name` (`STATUS_FINISH` -> `"finished"`, `STATUS_CUT` -> `"cut"`, `STATUS_SCHEDULED` -> `"scheduled"` -- all three confirmed against real data; withdrawal/disqualification still unconfirmed, so an unrecognized status name falls back to a generic transform (`STATUS_X` -> `"x"`) rather than guessing an exact string, logged so a real case can be folded in once actually observed), analogous to `_event_status`'s `_NON_PLAYED_STATUS_NAMES` mapping for head-to-head sports (`library/normalize/espn.py`) but its own vocabulary, not a reuse of that one -- a cut golfer is a normal, common outcome for this sport, not an edge case the way a canceled NFL game is. `score_to_par` comes from ESPN's `score.displayValue`, already relative to par (e.g. `"-17"`, or the literal string `"E"` for even par -- confirmed live, parsed to `0`); `total_strokes` from `score.value` (raw stroke count). Both are `null` together for a golfer who hasn't played yet, which needed its own real-data check to get right: a not-yet-started tournament's leaderboard (confirmed live, 2026-08-24, on a real scheduled event fetched ahead of its start) pre-lists every competitor with `score: {"value": 0.0, "displayValue": "-"}` -- `0.0` there is a sentinel, not a real 0-stroke round, so `displayValue == "-"` is checked first and forces both fields to `null` rather than passing `0.0` through as if it were a completed round. `earnings` is real prize money, `0` for a missed cut -- useful context, not a feature input. Per-round scores (ESPN's `linescores`, one entry per round played, each further nested down to a hole-by-hole breakdown) are a genuinely richer signal than anything the head-to-head sports get from their own scoreboard response, but are deliberately NOT flattened into `events.participants` here -- that belongs in the PGA adapter's own feature-building step (Phase 5's step 2), which decides what of that depth is actually worth turning into a feature, not in the shared schema.

This is the same `participants` array attribute either way -- a field-event adapter just writes more entries with a different `result` shape, and DynamoDB's tables are schemaless beyond their declared key/GSI attributes, so **no Terraform change was needed** for this (`dynamodb-events.tf`'s own comment already anticipated both shapes). The one real shared-code gap this surfaced: `library/serving/common.py`'s `enrich_participants` hardcoded `entity_type="team"` when looking up each participant's entity, which would have silently degraded every golfer's name to `null` (golfer entities are `entity_type: "player"`, not `"team"` -- see the Entities table section above). Fixed by adding an `entity_type` parameter (default `"team"`, preserving every existing head-to-head caller unchanged); a future PGA `serving/pga_reads.py` calls it with `entity_type="player"`. Both ingest orchestrator Step Functions (`sfn-ingest-orchestrator.tf`, `sfn-training-orchestrator.tf`) needed no changes -- confirmed by reading their state machine definitions, neither one branches on `event_type` at all; they resolve every sport's Lambda/ECS task purely by name (`States.Format('...-{}-ingest', $.sport.S)`), so PGA onboards through the exact same registry-driven path NCAA FB/NBA/NCAA MBB already did.

There is no golf equivalent of `venue_indoor`/`venue_city`/`venue_state` from a single `venue` object -- ESPN's golf leaderboard response has no top-level `venue` key at all; course info instead lives in a `courses` array (supports the (rare) multi-course-tournament case), each with `id`, `name`, `totalYards`, `shotsToPar` (par), and `address: {city, state?, country}` (`state` present for US courses, absent for international ones, e.g. Scotland's Renaissance Club carries only `city`/`country`). The PGA adapter maps the `host: true` course's `address` onto the existing `venue_city`/`venue_state` attributes (state genuinely `null` for a non-US event, the same "partial signal" treatment `weather_temperature` already gets) rather than adding new course-specific top-level attributes; `venue_indoor` stays `null` for every PGA event -- outdoor-vs-indoor isn't a meaningful distinction for golf, not a gap in the data. `course_id` (the host course's own `id`, e.g. `"65"` for Bellerive Country Club) is carried as its own top-level event attribute, separate from `venue_name` -- added 2026-08-25 specifically so a course-fit feature (`library/features/pga.py`) can match "this golfer's history at this course" reliably across seasons by a stable id rather than a name string a sponsor/course-name change could silently break.

**Only Medal (individual stroke-play) scoring tournaments are ever written to the `events` table.** PGA TOUR's real calendar also carries genuinely different formats this schema and its normalizers do NOT support: team match play (Ryder Cup, Presidents Cup, WGC-Dell Technologies Match Play -- ESPN's `tournament.scoringSystem.name == "Match"`), team stroke play (Zurich Classic of New Orleans -- `"Teamstroke"`), and made-for-TV exhibitions (The Match). Confirmed live, 2026-08-25, that all of these use a genuinely different `event["competitions"]` shape (a list of per-match/session dicts wrapped in an EXTRA list layer, `[[{...}], [{...}], ...]`, instead of every Medal event's flat `[{...}]`) and/or a `"team"` key in place of `"athlete"` on each competitor -- either one crashes this project's normalizer if fed through unchanged (reproduced directly: an `AttributeError` from the extra list layer, or a `KeyError` from the missing `athlete` key). `library/normalize/pga.py`'s `is_medal_scoring(event)` is the authoritative filter, checked by every caller (ingest, schedule-sync, normalize, backfill) before ever normalizing an event, with both normalizer functions also raising `ValueError` up front as a defense-in-depth backstop for a caller that forgets to check. Raw JSON for a skipped tournament is still written to S3 either way (preserves the record); it just never reaches DynamoDB. A future team/match-play feature (if ever built) starts from that raw JSON, not from `events` -- this table's `participants` shape has no room for a team-vs-team match-play result at all.

**`purse`/`is_major`** (PGA-only, added Phase 5 step 3 when the ranking model needed field-strength context a rolling per-golfer average can't provide -- a stronger field lowers everyone's top-10 odds regardless of who's in it): `purse` is the event's own top-level `purse` (a plain USD integer, e.g. `20000000`); `is_major` is the nested `tournament.major` boolean. Both confirmed live, 2026-08-24. Neither has a head-to-head equivalent -- a team sport's schedule strength is already captured by Elo (`library/features/common.py`), which has no analog for a field event with no opponent, just a field.

**`cut_score`/`cut_round`/`cut_count`** (PGA-only, added 2026-08-25 for the projected-cut-line model): straight off the leaderboard's own `tournament` object -- `cutScore` (the relative-to-par score that made the cut, e.g. `-2`), `cutRound` (which round the cut falls after, e.g. `2`), `cutCount` (how many players made it, e.g. `71`). Confirmed live on both a real-cut event (Genesis Scottish Open) and a no-cut FedEx Cup playoff event (BMW Championship, where all three genuinely report `0`, not a missing value) -- cut-line training filters on `cut_count > 0`, not a null check, specifically because of this.

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

Three Lambdas sit behind API Gateway (`Terraform/api-gateway-nfl-predict.tf`, `api-gateway-nfl-live-scores.tf`), all authenticated by the same Cognito authorizer (see "Access control" in `ARCHITECTURE.md`). `predict` and `predict-read` are split for cold-start isolation — `predict-read` never imports the ML dependency chain, so its two routes stay light. Most predictions are computed live from current DynamoDB/S3 state on each request; `GET /nfl/season` is the one exception, serving a cached S3 object that `predict` recomputes weekly rather than per-request.

| Route | Lambda | Returns |
|---|---|---|
| `GET /nfl/predictions/events/{event_id}` | `predict` | Win probability, margin, home score, and away score for one matchup, from one shared live feature vector (`live_features.build_live_event_features`) scored against all four event-level models |
| `GET /nfl/predictions/events/{event_id}/players/{entity_id}?stat=passing_yards` | `predict` | One player-prop prediction for one player in one game (`live_features.build_live_player_features`), scored against the `player-prop-<stat>` model matching the `stat` query parameter |
| `GET /nfl/events?status=scheduled\|completed` | `predict-read` | Event list, filtered by status |
| `GET /nfl/models` | `predict-read` | Current model versions |
| `GET /nfl/season` | `predict-read` | Season-projection output, read from the S3 cache `predict` writes weekly (`scheduler-nfl-season-projection.tf`) |
| `GET /nfl/live-scores` | `nfl_live_scores` | Cache-only live score refresh for events near kickoff or in progress; never writes to the Predictions table (see `ARCHITECTURE.md`'s Serving Layer note) |

`event_id`/`entity_id` are raw ESPN ids, translated internally to `SPORT#NFL#EVENT#...`/`SPORT#NFL#ENTITY#...` keys the same way every other NFL adapter does — callers never construct a DynamoDB key themselves. Packaged as a container image rather than the zip format `ingest`/`normalize` use (`Terraform/lambda-nfl-predict.tf`) — xgboost pulls in numpy and scipy, which alone measure ~225MB unzipped for this runtime, leaving almost no headroom under Lambda's 250MB unzipped zip limit; container Lambdas get a 10GB image limit instead.

## Sport registry table (live as of Phase 4)

Drives both orchestrator state machines' Map states (`Terraform/sfn-ingest-orchestrator.tf`, `Terraform/sfn-training-orchestrator.tf`) — this is what makes onboarding a new sport a data change (a new registry row plus that sport's own Lambdas/ECS task definitions, deployed under the naming convention below) rather than a code change to shared orchestration. Populated via `Terraform/dynamodb-sport-registry.tf` (`aws_dynamodb_table_item`, not applied by hand), one row per sport.

| Attribute | Example | Notes |
|---|---|---|
| `sport_key` | `SPORT#NFL` | |
| `sport` | `nfl` | |
| `event_type` | `head_to_head` | `field` for PGA/F1 once Phase 5 adds them — not yet consumed by either orchestrator, which only handles head-to-head sports so far |
| `polling_cadence` | `daily` | Informational for now — both orchestrators' own EventBridge schedules run year-round at a fixed cadence (daily for ingest, monthly for training) and rely on `season_start`/`season_end` for season gating, not a per-sport cadence lookup. Revisit once a second sport's real cadence differs enough to need it |
| `season_start` / `season_end` | `08-01` / `02-28` | Year-agnostic `MM-DD` bounds (inclusive) of this sport's season — the season on/off switch. Both orchestrators check `Terraform/lambda-season-gate.tf` (which calls `library.season.is_in_season`, handling the calendar-year wraparound most real seasons cross) against these before running anything for a sport. Deliberately static, Terraform-owned config, not a runtime-mutable flag: an earlier `active` boolean lived here instead, and every `terraform apply` silently reset it back to whatever this file declared, since `aws_dynamodb_table_item` manages a whole item as one opaque blob with no way to let something outside Terraform own just one field of it |
| `training_targets` | see below | List of every model this sport trains, read by the training-orchestrator's inner Map state instead of what used to be Terraform `for_each` maps (`local.nfl_score_targets`, `local.nfl_player_prop_stats`) |
| `current_model_version` | *(not yet set)* | Reserved for the Phase 7 model-promotion approval flow — which model version the inference Lambda should serve by default. Not written by Terraform on purpose; that pointer is meant to move only on human approval, not on every `terraform apply` |

**`training_targets` shape** — one entry per model the training-orchestrator should train for this sport:
```json
{
  "model_name": "player-prop-passing-yards",
  "task_definition_suffix": "train-player-prop-model",
  "container_name": "nfl-train-player-prop-model",
  "env_name": "TARGET_STAT",
  "env_value": "passing_yards"
}
```
`task_definition_suffix` resolves the ECS task-definition family at runtime as `<project>-<sport>-<task_definition_suffix>` (e.g. `sports-predictor-nfl-train-player-prop-model`) — the same `${project}-${sport}-<stage>` convention every Lambda/ECS resource in this project already follows (`sports-predictor-nfl-ingest`, `sports-predictor-nfl-feature-engineering`, etc.), which is what lets the state machines resolve a resource name from `sport` alone rather than needing a stored ARN per row. `env_name`/`env_value` is a single optional override pair, not a list — every training script this project has needed so far takes at most one (`SCORE_TARGET` or `TARGET_STAT`); a target with no real override (`win-probability`) gets a harmless no-op (`AWS_REGION` re-asserted at its own value) rather than a variable-length-list special case the state machine would otherwise need to branch on.

## Access patterns and indexes

The `SPORT#<sport>#...` prefix on every partition key means a query for "all NFL events" or "all NFL entities" is a single partition query, not a table scan — this matters once six sports share the same tables. Add a global secondary index on `event_date` (scoped within the sport prefix) once you need date-range queries like "this week's games," and an index on `entity_id` once the frontend needs "show me this team's full history" — don't add either speculatively before a feature actually needs it, since each GSI roughly doubles the write cost for that table.

## S3 key conventions

Every S3 prefix used by more than one sport follows `<sport>/<purpose>/...`, the same partitioning discipline the DynamoDB tables use, so a bucket serving all six sports never has one sport's keys collide with another's. Established prefixes, all under the model-artifacts or raw-data-lake bucket as noted:

| Prefix | Bucket | Written by | Read by |
|---|---|---|---|
| `<sport>/training-data/*.parquet` | model artifacts | feature-engineering Fargate task | training Fargate tasks |
| `<sport>/<model-name>/...` (versioned artifacts + model cards) | model artifacts | training Fargate tasks | `predict`/`predict-read` Lambdas |
| `predictions-cache/<sport>/events/<event_key>[.json \| /players/<entity_id>/<stat>.json]` | model artifacts | `predict` (populate-on-miss) | `predict-read` (read-through cache) |
| `season-projections/<sport>/latest.json` | model artifacts | `predict`'s scheduled season-projection run | `predict-read`'s `/{sport}/season` route. Deliberately **not** nested under `<sport>/` the same way trained models are — `list_models` treats every top-level segment under `<sport>/` as a model name, so nesting it there would surface a bogus "season-projection" model |
| `ncaambb/rankings/{season}/{season_type}/{week}.json` | raw data lake | NCAA MBB backfill + daily ingest (one raw AP poll snapshot per key) | feature-engineering's poll-centric ranking-dataset builder (see `NCAAMBB_FEATURE_ENGINEERING.md`) |
| `ncaafb/rankings/{season}.json` | raw data lake | NCAA FB backfill (one raw ranking snapshot per season) | NCAA FB's ranking data is otherwise attached directly to each event at ingest time (`home_current_rank`/`away_current_rank`), unlike NCAA MBB's poll-centric join -- this key is a backfill-time cache, not read by feature engineering the way NCAA MBB's per-poll keys are |
| `<sport>/conference-membership/{season}.json` | raw data lake | `ncaambb-schedule-sync` (the one Lambda in this prefix's sport with internet egress) | `ncaambb-predict` (VPC-isolated, reads the cache instead of calling out itself) |
| `pga/statistics/{date}.json` | raw data lake | `pga-ingest`, unconditionally every day regardless of whether a tournament is current (one raw season-stats snapshot per date -- driving distance/accuracy, GIR%, putts per hole, scoring average, etc.) | `feature-engineering/pga/build_dataset.py` (nullable, join-by-nearest-preceding-date features -- see `docs/PGA_FEATURE_ENGINEERING.md`). Same "raw snapshot bypasses DynamoDB entirely" pattern as NCAA MBB's AP polls above -- ESPN's own `golf/pga/statistics` endpoint is CURRENT-SNAPSHOT-ONLY (confirmed live, 2026-08-25: its `season`/`year` query params are silently ignored), so this prefix is the ONLY possible historical record of these categories this project can ever have; there is no backfill path for it at all |

The last row is the general shape for the S3-relay pattern described in `design/ARCHITECTURE.md`'s "Networking: no NAT Gateway, anywhere" section — a future sport needing something similar should follow the same `<sport>/<purpose>/...` prefix convention and grant the reading Lambda a scoped `s3:GetObject` statement on just that prefix, not bucket-wide access.

`player_game_stats` needs its own `entity_id` GSI sooner rather than later, even under the same "don't add speculatively" rule — its primary key is event-first (`PK = SPORT#...#EVENT#...`), so "this player's last N games" (the core input to most player-prop features, e.g. rolling averages) isn't answerable from the base table at all, unlike team history which can at least be brute-forced from `events`.
