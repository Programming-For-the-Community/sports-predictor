# Project Plan

A phased checklist in implementation order. Each phase assumes the previous one is functionally complete — don't start Phase 2 before Phase 1's model is actually predicting and displaying in the frontend. The point of this ordering is to prove the architecture on the easiest case before spending effort generalizing it.

## Phase 0 — Foundations (shared infrastructure)

- [x] Define entity/event schema (see `DATA_SCHEMA.md`)
- [x] Set up IAM roles for Lambda, Fargate, and Step Functions with least-privilege access to the specific tables/buckets they need
- [x] Create S3 buckets: raw data lake, model artifacts, frontend hosting
- [x] Create DynamoDB tables: entities, events, player_game_stats, predictions (sport registry table's schema comes in Phase 4, but the empty table itself is created here — see Phase 4)
- [x] Apply the tagging strategy to every resource at creation time (see `TAGGING_STRATEGY.md`) — don't defer this, retrofitting tags later means re-auditing every resource
- [x] Set up Cognito User Pool + App Client, create your own user, disable self-signup
- [x] Stand up API Gateway with a Cognito authorizer attached (no routes yet — just the auth scaffold)
- [x] Set an AWS Budget alert at $10–15/month as a misconfiguration tripwire
- [x] Initialize repo structure (`/adapters`, `/core`, `/infra`, `/frontend`, `/docs`) and commit this documentation set

## Phase 1 — NFL adapter (proof of architecture)

- [x] Write the NFL ingest function pulling from nflverse
- [x] Write the normalize function mapping nflverse data into the entity/event schema, including per-player box scores into `player_game_stats`
- [x] Backfill 10 years of historical NFL data, including player box scores
- [x] Build feature engineering: rolling averages, an Elo-style rating, home/away and rest-day splits (event-level), plus per-player rolling stat averages and usage rate (player-prop-level)
- [x] Train the first XGBoost win-probability model, store the artifact in S3
- [x] Train a first player-prop model for at least one stat (e.g., QB passing yards) — proves the `player_game_stats` table and the per-target train/predict split actually work before generalizing to other sports
- [x] Write the inference Lambda and wire it to API Gateway routes for both event-outcome and player-prop predictions
- [x] Build a minimal Flutter Web frontend showing one sport's event predictions and at least one player-prop prediction
- [x] Confirm the Cognito login gate works end to end on the live URL — log out, confirm the API rejects unauthenticated calls, log back in, confirm it works

**Beyond the original Phase 1 checklist**, NFL has since grown well past "proof of architecture" scope:
- Ingest switched from nflverse to ESPN's public site + core APIs (coach, injury, and depth-chart enrichment), with a dedicated `nfl-schedule-sync` Lambda and a daily roster-sync job fixing stale `team_id` links
- Inference split into `predict` + `predict-read` Lambdas (cold-start isolation) plus a dedicated `nfl-live-scores` Lambda (cache-only, 60s EventBridge poll gated to near-kickoff, zero DynamoDB writes)
- Weekly season-projection precompute + S3 cache (fixed a season-tab timeout regression)
- `nfl-predict`'s inference image moved to arm64; the training task family did not
- New GSIs: `entities.team-index`, `events.status-index`, a `player_game_stats`/`team_game_stats` sport-index
- Event-level model set grew to four targets (win-probability, score-margin, home-score, away-score) plus seven player-prop stat targets, all trained through the shared harness described in Phase 4

## Phase 2 — NCAA FB adapter (validate generalization)

- [ ] Register for a CFBD API key
- [ ] Write NCAA FB ingest/normalize functions against the same schema used for NFL
- [ ] Confirm no changes were needed to shared storage, serving, or frontend code — if you found yourself editing `core/`, that's a signal the Phase 1 abstraction wasn't general enough
- [ ] Backfill 10 years, train a model, add it to the frontend

## Phase 3 — NBA + NCAA MBB adapters (stress-test volume)

- [x] Write the NBA adapter (ESPN's public endpoints, not nba_api — nba_api/stats.nba.com is blocked by bot protection from datacenter IPs, a real deployment risk; see `design/DATA_SOURCES.md`)
- [x] Write the NCAA MBB adapter (ESPN endpoints)
- [x] Confirm the ingestion schedule and DynamoDB throughput hold up under a much higher game density than NFL (back-to-backs, dozens of games per day in-season) — both sports' ingest uses `ThreadPoolExecutor` concurrency (unlike NFL's sequential loops) specifically for this; confirmed live against D1's real ~362-team/~150-game-Saturday volume for NCAA MBB
- [x] Backfill, train, and add NBA to the frontend — fully live end to end (see the `project-nba-onboarding` memory)
- [x] Backfill and train NCAA MBB (through season simulation + both postseason brackets) — backend fully live; frontend activation (flipping `sport_config.dart`'s `active: true` and fixing its `id` from `ncaa_mbb` to `ncaambb`) is the one step still outstanding, see the `project-ncaambb-onboarding` memory

**Beyond the original checklist**, both adapters grew a postseason simulation feature not originally scoped this early (see this file's own note on why NBA's bracket work got pulled forward rather than deferred to a later hardening pass, and `project-ncaambb-onboarding`/the `soft-moseying-lemur` plan for NCAA MBB's): NBA's play-in-aware playoff bracket plus a real in-season NBA Cup knockout bracket; NCAA MBB's is a genuinely different shape again — one full single-elimination bracket per conference tournament (not NBA's or NFL's single unified bracket) feeding a 68-team March Madness bracket (First Four, S-curve-seeded regions, Final Four), both real-vs-projected reconciled. Both sports also picked up a `LightGBMClassifierAdapter`/`LightGBMRegressorAdapter` candidate family (`library/ml/model_types.py`) not present for NFL/NCAAFB, added specifically for basketball's larger data volume.

## Phase 4 — Generalize orchestration

Pulled forward ahead of Phases 2/3: with NFL's real Terraform footprint in hand (13 EventBridge Scheduler resources for one sport alone, most of them per-training-target `for_each` maps with hand-picked cron slots), it was clear that generalizing orchestration after a second or third sport had already copied that pattern would mean unwinding several sports' worth of deployed infra at once instead of a one-sport refactor. All four items below are done.

- [x] Build the sport registry table (adapter reference, polling cadence, current model version) — done in Phase 0 (empty table) and populated for NFL in this pass (`Terraform/dynamodb-sport-registry.tf`); see `design/DATA_SCHEMA.md`'s registry section for the schema actually shipped, which added a `training_targets` list beyond what was originally sketched here
- [x] Replace the four per-sport EventBridge rules with a single Step Functions Map state driven by the registry — shipped as *two* state machines, not one (`Terraform/sfn-ingest-orchestrator.tf`, `Terraform/sfn-training-orchestrator.tf`), since ingest and training fan out differently (training needs a nested Map: per sport, then per training target within that sport). See `design/ARCHITECTURE.md`'s multi-sport section
- [x] Extract shared feature-engineering primitives (rolling windows, rating updates, rest/travel calculations) into a common library used by all head-to-head adapters — `library/features/common.py` (Elo, rolling averages, streaks, rest days, injury helpers) and `library/features/geo.py` (divisional/travel mechanism, parameterized by data rather than hardcoded to NFL's teams); `library/features/nfl.py` now holds only genuinely NFL-specific logic (QB/RB/WR leader identification, event/player feature assembly)
- [x] Build a shared backtesting harness that produces the same accuracy/calibration report regardless of which sport's model it's pointed at — already done ahead of schedule before this pass; see `library/ml/backtest.py` and `library/ml/training_common.py`, both sport-agnostic (take `sport` as an explicit parameter throughout)

## Phase 5 — PGA Tour adapter (field-event schema)

- [x] Extend the event schema to support N participants per event instead of exactly 2 (see `DATA_SCHEMA.md`) -- turned out to need no DynamoDB/Terraform change (schemaless tables, both orchestrator state machines already resolve every sport purely by name with no `event_type` branching) and exactly one real shared-code fix (`enrich_participants` hardcoded `entity_type="team"`, now parameterized); the real work was pinning down the field-event `result` shape against ESPN's actual golf leaderboard response rather than the earlier guessed sketch -- see `DATA_SCHEMA.md`'s field-event `participants` section
- [x] Write the PGA adapter against ESPN's golf endpoints, optionally layering in Data Golf if you want their published skill ratings as a feature -- Data Golf skipped (ESPN alone is sufficient, no gap it would fill yet); `library/http/pga.py` (PGAClient), `library/normalize/pga.py` (field-event normalizers -- a new module, not an extension of the shared `library/normalize/espn.py`, since a golfer's own entity IS the participant), `aws-lambdas/pga/{ingest,normalize,schedule-sync}`, and `data-backfills/pga/` all built and tested against real verified ESPN responses; found + fixed two more real edge cases beyond step 1's schema pass (`score.displayValue == "E"` for even par, and a not-yet-started tournament's `score: {"value": 0.0, "displayValue": "-"}` sentinel that would've corrupted `total_strokes`/`score_to_par` if passed through raw) -- see `DATA_SCHEMA.md`'s field-event section and `project-pga-onboarding` memory. Terraform (Lambda resources, IAM, EventBridge schedule, S3 notification prefix, ECS backfill task) and CI (`pga_data_pipeline.yml`, `pga_backfill.yml`, `pga_deploy.yml`) built ahead of schedule, along with the sport registry row itself (`event_type: "field"`, an always-in-season window -- PGA's real schedule runs almost continuously, confirmed live -- and an empty `training_targets` pending step 3)
- [x] Build a ranking-style model (multinomial classification or top-N probability) rather than reusing the win/loss classifier -- built as a top-10-finish-probability binary classifier (`top-10-probability`), not a genuine multinomial/softmax model: a real multi-class task would have needed new task-type support in `library/ml/backtest.py`/`model_types.py` (no adapter there produces a >2-class probability distribution today), a materially riskier change to shared infra every other sport's models also depend on, versus reusing the existing, proven binary-classification harness unchanged -- the checklist itself names "top-N probability" as a sufficient alternative, not just multinomial. `library/features/pga.py` (rolling per-golfer form: score-to-par, finish position, top-10/20 rate, finish rate, earnings -- built from `events.participants` directly, since a field-event sport has no separate player_game_stats table to pull from), `feature-engineering/pga/build_dataset.py`, `model-training/pga/train_top10_model.py` (same 5-candidate XGBoost/LogisticRegression/RandomForest/MLP/LightGBM tournament every other sport's flagship classifier uses, via the identical `run_backtest` harness). Also added two new event-item fields (`purse`, `is_major`) the model needed for field-strength context that no golfer-level rolling average alone can capture -- verified against real ESPN data, see `DATA_SCHEMA.md`. Terraform/CI for the new feature-engineering + training ECS tasks (`ecs-task-pga-feature-engineering.tf`, `ecs-task-pga-train-top10-model.tf`, `pga_ai_training.yml`, wired into `pga_deploy.yml`'s `infra` job) and the registry's `training_targets` entry all built same-day, ahead of the frontend step, once the user's own push/backfill/train plan made the gap concrete
- [ ] Wire into the frontend -- the sport registry row itself was pulled forward into step 2 above (needed for the ingest orchestrator to actually reach `pga-ingest`); this item is now frontend-only

**2026-08-25 pre-backfill audit + model expansion, beyond the original step 3 scope:** before running the first real backfill, audited the backfill script against real (not assumed) ESPN responses and found a genuine live crash bug -- Ryder Cup/Presidents Cup/WGC Match Play/Zurich Classic/The Match use scoring formats (`tournament.scoringSystem` != `"Medal"`) this project's normalizers were never built for, reproduced directly against real API responses. Fixed with `is_medal_scoring()`, checked by every caller. Same pass added course-fit features (`course_id` + course-scoped rolling history) and investigated (not built, current-snapshot-only endpoint) putting/fairways/GIR/driving-distance stats. Then, per an explicit follow-up ask, expanded from the original single top-10-probability model to 5 trained targets across 3 datasets: top-10 and top-5 finish probability, projected score-to-par (the basis for a serving-time "field finish order" ranking), a projected cut line (tournament grain), and per-round projected scores (rounds 1-4, one shared parameterized script). All Terraform/CI for the expanded model set built same-day -- see `docs/PGA_FEATURE_ENGINEERING.md` and the `project-pga-onboarding` memory for full detail.

## Phase 6 — F1 adapter (reuse the field-event pattern)

- [ ] Write the F1 adapter against Jolpica-F1
- [ ] Reuse PGA's field-event feature and model pattern, adjusted for qualifying position and constructor-level features
- [ ] Wire into the frontend, add to the sport registry

## Phase 7 — Hardening

- [ ] Review tag coverage in Cost Explorer — confirm you can actually see per-sport and per-component cost breakdowns, not just a lump total
- [ ] Add CloudWatch alarms for unexpected Lambda or Fargate error rates
- [ ] Set explicit log retention policies so CloudWatch Logs doesn't grow unbounded
- [ ] Document and test the runbook for adding a hypothetical sport #7 — if the registry-driven pattern actually works, this should take an afternoon, not a redesign
- [ ] Add a notification flow (e.g. SNS/email) for reviewing a newly trained model before promoting it — the sport registry's `current_model_version` pointer should only move to the new version after approval, not automatically on every retrain
