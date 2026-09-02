# Onboarding a New Sport

A runbook for adding sport #7 (or beyond). Every sport in this project follows the identical registry-driven shape described in `design/ARCHITECTURE.md` — onboarding is writing one adapter plus one Terraform stack under the `<project>-<sport>-<stage>` naming convention and adding one sport-registry row, never a change to shared orchestration, serving, or frontend shell code.

## Before you start

**Decide the event shape.** Every sport is either **head-to-head** (two teams/competitors — NFL, NCAA FB, NBA, NCAA MBB) or **field** (many entrants, ranked finish — PGA Tour, F1). This decision drives everything downstream: storage shape (`design/DATA_SCHEMA.md`'s two `participants` shapes), whether a `player_game_stats` table applies at all (head-to-head only — a field-event entity *is* the participant), and whether player-prop routes exist on the API (head-to-head only). If the new sport is field-event, budget real time for pinning down the real API response shape before writing any normalizer code — PGA and F1's field-event schemas both needed a genuine second pass once the real API response didn't match the originally-sketched shape.

**Pick a data source**, following `design/DATA_SOURCES.md`'s own priority order: free and keyless first (ESPN's unofficial site/core APIs have covered 4 of 6 sports so far), a free-tier keyed API second (CFBD for NCAA FB — see below for the shared-secret pattern), a purpose-built free API third (Jolpica-F1). Confirm the source actually has what you need — team/player identifiers, box scores or field results, and either a bulk schedule/calendar endpoint or a per-date scoreboard — before committing to it.

**If the source needs an API key**, it goes in the one shared Secrets Manager secret every paid source already uses (`var.third_party_api_key_secret_arn`), as a new JSON field (e.g. `<sport>_ingest_key`, `<sport>_backfill_key` if separate ingest/backfill accounts make sense) — never a new Terraform-managed secret. See `library/http/cfbd.py`'s `_resolve_api_key` for the pattern to copy.

## Naming convention

Every resource this sport owns follows `<project>-<sport>-<stage>` (e.g. `sports-predictor-ncaafb-ingest`). This is what lets both orchestrator state machines resolve a sport's Lambda/ECS task purely from the registry's `sport` string — no stored ARN per row, no branching in either state machine's own definition. Get this right from the first resource; it's the single convention every other piece of automation in this project (CI job names, IAM statement resource ARNs, the two orchestrators) assumes holds.

## Backend build order

This is the order every real onboarding has actually followed, each step buildable and testable independently:

1. **HTTP client** (`library/http/<sport>.py`, extending `EspnBaseClient`/`HttpClient` as appropriate) — `get_teams`/`get_scoreboard_for_date`/`get_summary`/`get_roster`-shaped methods, whatever the source actually offers. **Verify every field against a real captured response before writing a single line of normalizer code against it** — this project's own recurring lesson, re-learned on nearly every sport, is that a guessed field name or assumed shape (missing a nested wrapper, an unexpected sentinel value, a truncated default page size) fails silently or crashes downstream, not at the HTTP layer.
2. **Normalizer** (`library/normalize/<sport>.py`) — maps raw responses into `design/DATA_SCHEMA.md`'s entity/event shape. For a head-to-head sport, reuse `library/normalize/espn.py`'s shared functions directly if the source is ESPN; for a field-event sport, or a non-ESPN source, this is its own module (see `library/normalize/pga.py`, `f1.py`).
3. **Ingest Lambda** (`aws-lambdas/<sport>/ingest/handler.py`) — fetches the current day/week and writes raw JSON to S3 under `<sport>/...` prefixes. Never touches DynamoDB directly.
4. **Normalize Lambda** (`aws-lambdas/<sport>/normalize/handler.py`) — S3-event-triggered (add the prefix to `s3-raw-data-lake-notifications.tf`), upserts into `entities`/`events`/`player_game_stats`/`team_game_stats`.
5. **Backfill task** (`data-backfills/<sport>/backfill.py`) — a standalone Fargate task (public subnet, own IAM role) pulling full history in one pass, same raw-to-S3-then-upsert pattern as ingest, idempotent (skip-if-already-in-S3).
6. **Schedule-sync Lambda** (`aws-lambdas/<sport>/schedule-sync/handler.py`) — refreshes the *entire* season's calendar on its own schedule, independent of daily ingest. Skip this only if the source's own scoreboard call already returns the full season in one shot regardless of date queried (PGA's case — see its own schedule-sync docstring); every other sport needs it.
7. **Feature engineering** (`library/features/<sport>.py` + `feature-engineering/<sport>/build_dataset.py`) — reuse `library/features/common.py`'s Elo/rolling-average/rest-day primitives for a head-to-head sport; a field-event sport's rolling-form functions are its own module (`library/features/pga.py`, `f1.py`).
8. **Model training scripts** (`model-training/<sport>/train_*.py`) — one script per target, through the shared `library.ml.backtest.run_backtest` tournament (XGBoost/logistic-or-ElasticNet/random-forest/MLP, +LightGBM for a high-volume sport). Reuse `library/ml/model_types.py`'s adapters unchanged.
9. **Sport registry row** (`Terraform/dynamodb-sport-registry.tf`) — `event_type`, `season_start`/`season_end` (year-agnostic `MM-DD`, or the whole calendar year if the sport has no real off-season the way PGA doesn't), and the full `training_targets` list. This is what makes both orchestrators pick the sport up with zero code changes.
10. **Serving** — `library/serving/<sport>_reads.py` (`list_events` bounded to `RECENT_EVENTS_LIMIT`, same pattern every sport uses), then the three Lambdas: `aws-lambdas/<sport>/predict/` (event_prediction.py, live_features.py, model_loader.py — VPC-attached), `aws-lambdas/<sport>/predict-read/` (not VPC-attached), `aws-lambdas/<sport>/live-scores/` (not VPC-attached, own IAM role).

## Terraform

One file per resource, no modules, matching the existing per-sport file sets exactly:
- `lambda-<sport>-{ingest,normalize,schedule-sync,predict,predict-read,live-scores}.tf`
- `api-gateway-<sport>-predict.tf`, `api-gateway-<sport>-live-scores.tf`
- `iam-<sport>-backfill.tf`, `iam-<sport>-live-scores.tf`
- `ecs-task-<sport>-backfill.tf`, `ecs-task-<sport>-feature-engineering.tf`, `ecs-task-<sport>-train-<target>.tf` (one per training target)
- `scheduler-<sport>-{schedule-sync,live-scores,season-projection}.tf`
- `outputs-<sport>.tf`

Add the new sport's `Sport` tag value to `design/TAGGING_STRATEGY.md`'s allowed-values list, and add its 6-routed-path prefix to `cloudfront.tf`'s `local.api_path_prefixes`.

## CI/CD

One workflow file per deploy target, matching the existing pattern: `<sport>_data_pipeline.yml`, `<sport>_backfill.yml`, `<sport>_ai_training.yml`, `<sport>_ai_hosting.yml`, `<sport>_predict_deploy.yml`, `<sport>_predict_read_deploy.yml`, `<sport>_live_scores_deploy.yml`, `<sport>_deploy.yml`. Wire the build jobs into `app_deploy.yml`'s job graph — a new Dockerfile or container-image build isn't done until its build workflow is actually reachable from the top-level deploy, the same rule this project already applies to every new Lambda/schedule.

## Frontend activation

Add one `SportConfig` entry to `Source/front-end/lib/core/models/sport_config.dart`'s `kSports` list: `id` (must match the backend's own route prefix exactly — it's passed straight through as the API path segment), `displayName`, `eventShape`, `accentColor` (cyan for head-to-head, violet for field-event, per `design/FRONTEND_STYLE.md`), and `active: true`. That one flip is the entire frontend-activation step for a sport whose event shape already has a matching UI (head-to-head or plain field-event) — a genuinely new shape (like PGA's FedEx Cup points page, `usesFedexCupSeasonPage`) needs its own season-page route.

## Checklist

- [ ] Event shape decided, data source picked and its real response shape verified live
- [ ] HTTP client, normalizer, ingest Lambda, normalize Lambda, backfill task
- [ ] Schedule-sync Lambda (unless the source's own scoreboard call already returns the full season)
- [ ] Feature engineering module + Fargate task, training scripts for every target
- [ ] Sport registry row (`training_targets` complete, `season_start`/`season_end` correct)
- [ ] Serving reads module, predict/predict-read/live-scores Lambdas
- [ ] Every Terraform file in the per-sport set above, `Sport` tag added to `TAGGING_STRATEGY.md`, path prefix added to `cloudfront.tf`
- [ ] `iam-eventbridge-invoke.tf`'s `InvokeDirectLambdaJobs` statement includes this sport's `predict`/`predict-read`/`live-scores`/`schedule-sync` ARNs (see "Common pitfalls" below)
- [ ] `cloudwatch-alarms.tf`'s `alarm_all_sports` (and `alarm_schedule_sync_sports`, if this sport has schedule-sync) includes the new sport
- [ ] CI workflows created and wired into `app_deploy.yml`
- [ ] `sport_config.dart`'s `kSports` entry added with `active: true`, `id` matching the backend route prefix exactly
- [ ] Mobile pass on this sport's event list/detail/leaderboard widgets once live data is flowing (see `design/FRONTEND_STYLE.md`'s responsiveness section) — every sport onboarded so far has needed at least one overflow fix once live-score content actually appeared

## Common pitfalls

Real bugs found across the six onboardings so far, in the order they tend to bite:

- **EventBridge invoke-permission gap.** A new sport's `schedule-sync`/`live-scores`/`predict` Lambda gets its own `aws_scheduler_schedule` target, but `iam-eventbridge-invoke.tf`'s `InvokeDirectLambdaJobs` statement is a separate, manually-maintained resource list — missing an entry here doesn't fail `terraform apply` (both the schedule and the Lambda still create cleanly on their own), it fails silently at runtime with `AccessDenied` that only shows up in EventBridge's own retry logs. This has happened twice for real (PGA and NCAAFB's own `schedule-sync` functions).
- **`sport_config.dart` id mismatch.** The frontend `id` must match the backend's route prefix exactly — a mismatch 404s every API call the moment `active` flips to `true`, not before.
- **Entity/ID collision on a new entity_type.** If this sport introduces a second `entity_type` sharing the same raw-id numbering space as an existing one (e.g. a team-match-play sport's national-team ids vs. player ids), use the type-aware key builder (`entity_key(sport, id, entity_type)`) from day one — a low-digit team id colliding with a real athlete id has caused a real stale-guard bypass before (NBA).
- **CloudFront routing prefix.** Forgetting to add the sport to `local.api_path_prefixes` means every request to `/<sport>/*` falls through to the frontend's own default S3 behavior instead of API Gateway — a confusing 200-with-HTML response, not an obvious error.
- **Defensive-parsing sweeps need to cover the whole file, not just the field that crashed.** When a raw-data edge case (a sentinel value, a missing key, an unexpected type) breaks one parser, the same source's other fields sharing that shape are very likely to have the identical gap — a partial fix that patches only the symbol that actually crashed has caused a second, avoidable crash before.
- **Mobile overflow once live data starts flowing.** A sport's list/detail widgets are usually built and tested against scheduled/completed data first; the extra content a live event actually adds (a live score, a "LIVE" pill, a period/quarter/thru indicator, a live running order) is what has broken layout width budgets on real devices, every single time, across every sport onboarded so far. Test the live state at a narrow width before calling a sport's frontend done, not just the scheduled/completed ones.
