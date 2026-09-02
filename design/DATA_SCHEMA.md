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
No `role` key -- that's a head-to-head-only concept (home/away), meaningless for a ranked field. `finish_position` is parsed from ESPN's `status.position.displayName` (`"T26"` -> 26 + `is_tie: true`, `"1"` -> 1 + `is_tie: false`, `"-"` -> `null` for a player with no finish, e.g. cut/withdrawn/hasn't-played-yet). `status` is this project's own normalized vocabulary mapped from ESPN's `status.type.name` (`STATUS_FINISH` -> `"finished"`, `STATUS_CUT` -> `"cut"`, `STATUS_SCHEDULED` -> `"scheduled"`, `STATUS_MDF` -> `"made_cut_did_not_finish"` (a golfer who made the cut but withdrew before finishing, e.g. injury mid-round-3) -- all four confirmed against real data, `STATUS_MDF` specifically confirmed common (not rare) via a live sweep of every 2017-2025 season calendar, 2026-08-26; withdrawal/disqualification BEFORE making the cut still unconfirmed despite that same sweep (~28,000 competitor-status rows checked with no WD/DQ status ever observed -- ESPN most likely omits a pre-cut WD/DQ golfer from `competitors` entirely rather than tagging a status, though this isn't confirmed either way), so an unrecognized status name falls back to a generic transform (`STATUS_X` -> `"x"`) rather than guessing an exact string, logged so a real case can be folded in once actually observed), analogous to `_event_status`'s `_NON_PLAYED_STATUS_NAMES` mapping for head-to-head sports (`library/normalize/espn.py`) but its own vocabulary, not a reuse of that one -- a cut golfer is a normal, common outcome for this sport, not an edge case the way a canceled NFL game is. `score_to_par` comes from ESPN's `score.displayValue`, already relative to par (e.g. `"-17"`, or the literal string `"E"` for even par -- confirmed live, parsed to `0`); `total_strokes` from `score.value` (raw stroke count). Both are `null` together for a golfer who hasn't played yet, which needed its own real-data check to get right: a not-yet-started tournament's leaderboard (confirmed live, 2026-08-24, on a real scheduled event fetched ahead of its start) pre-lists every competitor with `score: {"value": 0.0, "displayValue": "-"}` -- `0.0` there is a sentinel, not a real 0-stroke round, so `displayValue == "-"` is checked first and forces both fields to `null` rather than passing `0.0` through as if it were a completed round. `earnings` is real prize money, `0` for a missed cut -- useful context, not a feature input. Per-round scores (ESPN's `linescores`, one entry per round played, each further nested down to a hole-by-hole breakdown) are a genuinely richer signal than anything the head-to-head sports get from their own scoreboard response, but are deliberately NOT flattened into `events.participants` here -- that belongs in the PGA adapter's own feature-building step (Phase 5's step 2), which decides what of that depth is actually worth turning into a feature, not in the shared schema.

This is the same `participants` array attribute either way -- a field-event adapter just writes more entries with a different `result` shape, and DynamoDB's tables are schemaless beyond their declared key/GSI attributes, so **no Terraform change was needed** for this (`dynamodb-events.tf`'s own comment already anticipated both shapes). The one real shared-code gap this surfaced: `library/serving/common.py`'s `enrich_participants` hardcoded `entity_type="team"` when looking up each participant's entity, which would have silently degraded every golfer's name to `null` (golfer entities are `entity_type: "player"`, not `"team"` -- see the Entities table section above). Fixed by adding an `entity_type` parameter (default `"team"`, preserving every existing head-to-head caller unchanged); a future PGA `serving/pga_reads.py` calls it with `entity_type="player"`. Both ingest orchestrator Step Functions (`sfn-ingest-orchestrator.tf`, `sfn-training-orchestrator.tf`) needed no changes -- confirmed by reading their state machine definitions, neither one branches on `event_type` at all; they resolve every sport's Lambda/ECS task purely by name (`States.Format('...-{}-ingest', $.sport.S)`), so PGA onboards through the exact same registry-driven path NCAA FB/NBA/NCAA MBB already did.

There is no golf equivalent of `venue_indoor`/`venue_city`/`venue_state` from a single `venue` object -- ESPN's golf leaderboard response has no top-level `venue` key at all; course info instead lives in a `courses` array (supports the (rare) multi-course-tournament case), each with `id`, `name`, `totalYards`, `shotsToPar` (par), and `address: {city, state?, country}` (`state` present for US courses, absent for international ones, e.g. Scotland's Renaissance Club carries only `city`/`country`). The PGA adapter maps the `host: true` course's `address` onto the existing `venue_city`/`venue_state` attributes (state genuinely `null` for a non-US event, the same "partial signal" treatment `weather_temperature` already gets) rather than adding new course-specific top-level attributes; `venue_indoor` stays `null` for every PGA event -- outdoor-vs-indoor isn't a meaningful distinction for golf, not a gap in the data. `course_id` (the host course's own `id`, e.g. `"65"` for Bellerive Country Club) is carried as its own top-level event attribute, separate from `venue_name` -- added 2026-08-25 specifically so a course-fit feature (`library/features/pga.py`) can match "this golfer's history at this course" reliably across seasons by a stable id rather than a name string a sponsor/course-name change could silently break.

**Medal and Teamstroke (individual and team stroke-play) tournaments both write `event_type: "field"`.** `library/normalize/pga.py`'s `is_flat_stroke_play(event)` accepts either (checks `tournament.scoringSystem.name` for `"Medal"` or `"Teamstroke"`), since both share the exact same FLAT `event["competitions"]` shape. Teamstroke (Zurich Classic of New Orleans -- the only real calendar entry using it, confirmed live 2026-08-26) is a 2-golfer pairing per competitor (`team.displayName` + `roster: [{"athlete": {...}}, ...]`) rather than a single `athlete`, sharing one combined score/status -- `_competitor_to_participants` expands each pairing into TWO participant rows, both carrying the identical shared result plus a `partner_entity_ids` field, so a pairing plugs into the existing top-10/cutline/score/round models unchanged (each golfer just shares their partner's result, same as they'd share a real prize-money split).

**Team match play (Ryder Cup, Presidents Cup) and individual match play (WGC-Dell Technologies Match Play) write TWO new event types, normalized by `library/normalize/pga_matchplay.py`** -- a genuinely different shape from flat stroke play: `event["competitions"]` is nested (`[[{...}], [{...}], ...]`, one outer entry per session/day), and `"team"` replaces `"athlete"` on team-match competitors. Confirmed live, 2026-08-26, on a real Presidents Cup, a real WGC Match Play, and two real editions of the made-for-TV exhibition The Match (used to derive the exclusion below).
  - **`event_type: "match_play"`** -- one row per INDIVIDUAL match (a session's own foursome/fourball/singles pairing, or one WGC bracket match). `event_id` is synthesized as `f"{tournament_event_id}-match-{match_id}"` (ESPN's own inner competition id) since one leaderboard response covers many matches. `participants` is `[home, away]`, each `{"entity_id", "role", "golfer_entity_ids": [...], "result": {"status", "won", "halved", "margin_display", "margin_holes"}}` -- `golfer_entity_ids` is 1 golfer (singles/WGC) or 2 (foursomes/fourball pairing); `entity_id` is the national TEAM's id for team match play, or the golfer's own id (same as `golfer_entity_ids[0]`) for individual match play, so downstream feature code can treat both shapes uniformly. Extra context fields: `parent_event_id` (the Cup/WGC tournament's own event_id), `tournament_name`, `session_name` (e.g. `"Thursday Foursomes"`), `match_format` (`"foursome"`/`"fourball"`/`"singles"`).
  - **`event_type: "cup"`** -- one row for the OVERALL team result, Ryder Cup/Presidents Cup only (WGC Match Play has no team layer, so no such row exists for it -- `leaderboard_event_to_cup_event_item` returns `None`). `event_id` is the tournament's own real event_id. `participants` is `[home, away]`, each `{"entity_id": team_id, "role", "result": {"points", "won", "halved"}}` -- `halved` derived from point equality (a tied Cup hasn't been observed live in this project's 2017-2026 window, but a real Presidents Cup HAS tied before, 2003).

A new `entity_type: "team"` (national teams -- USA/INTL/EUROPE) is written alongside the existing `"player"` entities for team match play, type-aware keyed (`entity_key(sport, id, "team")`) so a low-digit team id (e.g. `"1"`) can never collide with a golfer's own numeric athlete id -- the same fix this project already applied for NBA's team/player id collision.

**The Match is excluded permanently, not deferred.** Shape-based, not name-based: Match-scored, team-based (identical `team`+`roster` shape to Ryder Cup), but with no Cup-level summary entry (`library/normalize/pga_matchplay.py`'s `is_exhibition`) -- a single one-off session, not a multi-day team competition with an aggregate score. Confirmed live on a real 2022 edition whose "athletes" aren't reliably PGA Tour golfers at all (Tom Brady/Aaron Rodgers vs. Patrick Mahomes/Josh Allen), which is exactly why this is excluded rather than normalized: no competitive/predictive value, and a real risk of polluting the golfer entities table with non-golfers. Raw JSON is still written to S3 either way; it just never reaches DynamoDB.

A points-based format (Barracuda Championship -- `"Modified Stableford"`) and any other unrecognized future `scoringSystem` fall through every check above and are also skipped, fail-closed rather than guessing a shape. Every caller (ingest, schedule-sync, normalize, backfill) checks `is_flat_stroke_play`/`is_supported_match_play`/`is_exhibition` (in that dispatch order) before normalizing an event, with both normalizer modules' functions also raising `ValueError` up front as a defense-in-depth backstop for a caller that forgets to check.

**A stroke-play tournament can still have zero competitor data -- a real ESPN gap, not a parsing issue.** Confirmed live, 2026-08-26, via a full sweep of every 2017-2025 season calendar entry: a small cluster of events (9 out of 464 sampled) have a `competitions[0]` with no `"competitors"` key at all. Most of these are genuinely-canceled 2020 COVID-disruption tournaments (THE PLAYERS Championship, The Open Championship, RBC Canadian Open, etc. -- `status.type.name == "STATUS_CANCELED"`, nothing was ever played, so there's nothing lost). But three are real, completed, played Fall-2020 events (Shriners Hospitals for Children Open, Sanderson Farms Championship, Corales Puntacana Championship -- all `STATUS_FINAL`, `completed: true`) where ESPN itself never populated competitor data, even though the tournament's own aggregate fields (`purse`, `cutScore`/`cutRound`/`cutCount`) ARE still present and real. A separate real 2026 case (The Sentry, January 2026) surfaced live during the initial full backfill run -- `STATUS_CANCELED`, no stated reason anywhere in ESPN's own response. `data-backfills/pga/backfill.py`'s `process_tournament` checks for this (after the `is_flat_stroke_play` check) and skips writing the event entirely rather than writing it with `participants: []` -- an empty-but-written event would silently corrupt the cutline dataset's `field_size` feature (`len(participants)`) to `0` for a tournament that really had a 130+ player field. The raw leaderboard response is still preserved in S3 either way. Backfill tracks these separately from both `tournaments_processed` and the unrecognized-scoring-system `tournaments_skipped` (a distinct `tournaments_empty` counter plus a `pga/backfill-empty-events/{timestamp}.json` S3 report) so a real gap stays visible in the run's own summary instead of silently inflating the "processed" count. A Match-scored event (team/individual match play) with no individual match data gets the identical treatment via `_process_match_play_tournament`'s own check -- not observed live as of 2026-08-26, but handled the same fail-closed way rather than assumed away.

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

Three Lambda types sit behind API Gateway per sport (`Terraform/api-gateway-{sport}-predict.tf`, `api-gateway-{sport}-live-scores.tf` — identical shape for all 6 sports), all authenticated by the same Cognito authorizer (see `design/ARCHITECTURE.md`'s client-request-path walkthrough). `predict` and `predict-read` are split for cold-start isolation — `predict-read` never imports the ML dependency chain, so its two routes stay light and, unlike `predict`, isn't VPC-attached at all (see that same doc's "Networking" section for why). Most predictions are computed live from current DynamoDB/S3 state on each request; `GET /{sport}/season` is the one exception, serving a cached S3 object that `predict` recomputes weekly rather than per-request.

| Route | Lambda | Returns |
|---|---|---|
| `GET /{sport}/predictions/events/{event_id}` | `predict` | Event-level prediction(s) for one matchup/tournament/race, from one shared live feature vector scored against every event-level model that sport trains |
| `GET /{sport}/predictions/events/{event_id}/players/{entity_id}?stat=passing_yards` | `predict` | One player-prop prediction for one player in one event (head-to-head sports only), scored against the `player-prop-<stat>` model matching the `stat` query parameter |
| `GET /{sport}/events?status=scheduled\|completed` | `predict-read` | Event list, filtered by status |
| `GET /{sport}/models` | `predict-read` | Current model versions |
| `GET /{sport}/season` | `predict-read` | Season-projection output, read from the S3 cache `predict` writes weekly (`scheduler-{sport}-season-projection.tf`) |
| `GET /{sport}/live-scores` | `{sport}-live-scores` | Cache-only live score refresh for events near kickoff or in progress; never writes to the Predictions table. Its own IAM role (`lambda_live_scores`), not `lambda_inference` — also not VPC-attached |

`event_id`/`entity_id` are raw source-system ids (ESPN, CFBD, Jolpica-F1, etc., depending on sport), translated internally to `SPORT#{SPORT}#EVENT#...`/`SPORT#{SPORT}#ENTITY#...` keys the same way every adapter does — callers never construct a DynamoDB key themselves. `predict` is packaged as a container image rather than the zip format `ingest`/`normalize`/`predict-read` use (`Terraform/lambda-{sport}-predict.tf`) — xgboost pulls in numpy and scipy, which alone measure ~225MB unzipped for this runtime, leaving almost no headroom under Lambda's 250MB unzipped zip limit; container Lambdas get a 10GB image limit instead.

## Sport registry table (live as of Phase 4)

Drives both orchestrator state machines' Map states (`Terraform/sfn-ingest-orchestrator.tf`, `Terraform/sfn-training-orchestrator.tf`) — this is what makes onboarding a new sport a data change (a new registry row plus that sport's own Lambdas/ECS task definitions, deployed under the naming convention below) rather than a code change to shared orchestration. Populated via `Terraform/dynamodb-sport-registry.tf` (`aws_dynamodb_table_item`, not applied by hand), one row per sport.

| Attribute | Example | Notes |
|---|---|---|
| `sport_key` | `SPORT#NFL` | |
| `sport` | `nfl` | |
| `event_type` | `head_to_head` | `field` for PGA/F1 — both orchestrators are event-type-agnostic (they resolve every sport's Lambda/ECS task purely by name, never branching on `event_type`), so this column is read by feature/serving code, not by either state machine |
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
