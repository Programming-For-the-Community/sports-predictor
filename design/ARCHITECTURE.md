# Architecture

This is the one architecture document for the project — design rationale and as-built reference together, not split across `design/` and `docs/` (they used to be, and drifted out of sync with each other; consolidated here). **For the visual diagrams themselves — client request path, scheduled data pipeline, observability/cost guardrails — open [`design/architecture.html`](architecture.html) in a browser.** It's a real standalone page (AWS-category-colored, hand-built icons), not a Mermaid render, and it's the single source of truth for those three diagrams; update it alongside this doc whenever the infrastructure changes shape. Everything below is the prose that doesn't fit on a diagram: why each piece exists, the constraints that shaped it, and a plain-text walkthrough of each request/pipeline flow for anyone reading this in a terminal or a Git diff rather than a browser.

All six sports — NFL, NCAA Football, NBA, NCAA Men's Basketball, PGA Tour, and F1 — run through the identical architecture described here, registry-driven, differing only in which upstream data source each sport's own adapter calls. Two event shapes underpin everything: **head-to-head** (two teams/competitors — NFL, NCAA FB, NBA, NCAA MBB) and **field** (many entrants, ranked finish — PGA Tour, F1); see `design/DATA_SCHEMA.md` for how that split shows up in storage.

## Why these specific pieces

Every compute component is either event-triggered (Lambda) or runs only on a schedule and exits (Fargate task, or an EC2 fleet scaled to 0 between runs) — nothing is billed while idle. S3 holds anything large or rarely queried directly (raw pulls, model artifacts); DynamoDB holds anything a Lambda needs to read quickly by key. There's no SageMaker endpoint, no RDS instance — each would add a meaningful fixed monthly cost for a benefit this single-user project doesn't need.

**Predict vs. predict-read vs. live-scores.** Serving splits into three Lambdas per sport, not one, purely for cold-start isolation:
- **`{sport}-predict`** (container image, ML dependencies) — computes a fresh prediction from current DynamoDB/S3 state and writes an audit-trail row to the predictions table. The only one of the three that's VPC-attached (see "Networking" below).
- **`{sport}-predict-read`** (zip, no ML dependencies) — reads back what `predict` already wrote (or triggers an async refresh on a stale/missing cache entry). What API Gateway actually invokes for `/events`, `/models`, `/season`, and both `/predictions/...` routes — the hot path every page load hits, so it never pays a cold start for xgboost/scikit-learn/pandas. Not VPC-attached.
- **`{sport}-live-scores`** — cache-only, never writes to the predictions table, kept warm by its own 60-second EventBridge schedule (gated to events near kickoff or in progress) rather than invoked only on a browser request. Not VPC-attached.

**Registry-driven orchestration.** Two Step Functions state machines, not per-sport EventBridge rules — `ingest-orchestrator` (daily) and `training-orchestrator` (monthly) — each scan a sport registry table and fan out to whichever sports are currently in-season (each row's `season_start`/`season_end` window, checked fresh on every run via a shared `season-gate` Lambda). This replaced an earlier design that gave NFL alone 13 hand-cron EventBridge Scheduler resources — a pattern that would only have gotten more expensive to unwind the more sports copied it, so the refactor was pulled forward ahead of onboarding a second sport rather than done later. Onboarding a new sport today means deploying its own Lambdas/ECS task definitions under the `<project>-<sport>-<stage>` naming convention and adding one registry row — no change to either state machine's own definition.

## Client request path

1. Browser sends an HTTPS request to the app's one public domain (Route 53 alias → CloudFront; ACM certs issued in `us-east-1` specifically, since CloudFront requires that regardless of the stack's primary region). CloudFront sits behind a WAF WebACL running AWS's own curated IP-reputation managed rule, and is geo-restricted to a whitelist.
2. CloudFront routes by path: the default behavior (`/*`) serves the Flutter web build from the frontend S3 bucket; six ordered behaviors (`/{sport}/*`) forward instead to API Gateway's own custom regional domain — not the raw `execute-api` hostname, which is disabled entirely so no traffic can bypass CloudFront's TLS 1.2 floor.
3. API Gateway validates the request's JWT against the Cognito User Pool via a built-in `COGNITO_USER_POOLS` authorizer — a local signature/claims check against cached JWKS keys, not a network call to Cognito on every request (API Gateway also caches the authorizer result per-token for 5 minutes on top of that).
4. API Gateway invokes `predict-read`, `predict`, or `live-scores` depending on the route (see the three-Lambda split above).
5. The invoked Lambda reads whatever context it needs from DynamoDB (live feature history for `predict`; events + already-logged predictions for `predict-read`; cached live state for `live-scores`) and, if it's `predict`, the currently-promoted model artifact(s) from the model-artifacts S3 bucket.
6. `predict` deserializes and scores the model.
7. `predict` writes an audit record of the prediction it just made to the predictions table — write-only from its own perspective; `predict-read` is the one that reads that table back, for the predicted-vs-actual comparison on completed events.

## Scheduled data pipeline

**Ingestion**, daily: `ingest-orchestrator` scans the registry, checks each sport's season window via `season-gate`, and invokes that sport's `{sport}-ingest` Lambda by naming convention. Each adapter fetches that day's scoreboard/box scores from its own upstream API and writes raw JSON to the raw-data-lake S3 bucket under a per-sport prefix — nothing else; it never touches DynamoDB directly. Every `PutObject` under a sport's prefix triggers that sport's `{sport}-normalize` Lambda via an S3 event notification, which maps the raw JSON into the shared schema and upserts it into `entities`/`events`/`player_game_stats`/`team_game_stats`. A separate `{sport}-schedule-sync` Lambda per sport, on its own schedule, independently refreshes the *entire* season's calendar (not just the current day).

**One-time historical backfill**: `{sport}-backfill` is a standalone Fargate task per sport (`aws ecs run-task`, not orchestrated by Step Functions) that pulls a sport's full multi-year history in one pass. Runs in a public subnet with a public IP — not the private/Gateway-Endpoint path — because it needs to reach its upstream API directly.

**Training**, monthly, once in-season: `training-orchestrator` runs `{sport}-feature-engineering` (Fargate on-demand, reads full history, writes training Parquet to S3), then fans that sport's `training_targets` list out onto a shared **EC2 Spot/on-demand training fleet** (two Auto Scaling Groups, ECS tasks with the `EC2` launch type) via a Distributed Map. This replaced an all-Fargate training path entirely once a real canary run validated EC2 as both cheaper and reliable. Once every sport's map completes, the state machine explicitly scales both ASGs back to 0; independent of that, an `ec2-training-reaper` Lambda on its own 10-minute schedule is a backstop that terminates any idle training instance — the case the state-machine-level scale-down can't catch (a manually-stopped execution runs no further states at all).

## Storage

| Store | Holds |
|---|---|
| S3: Raw Data Lake | Every raw upstream API response, exactly as received, forever, partitioned by sport prefix |
| S3: Model Artifacts | Versioned model binaries, model cards, and training Parquet datasets, partitioned by sport |
| S3: Frontend | The built Flutter web app, served by CloudFront |
| DynamoDB: entities / events / player_game_stats / team_game_stats | The normalized schema every sport adapter writes to and every serving/training job reads from, partitioned by a `sport` key |
| DynamoDB: predictions | Audit log of every prediction ever served, write-only from each `{sport}-predict`'s perspective |
| DynamoDB: sport_registry | Drives both orchestrators' fan-out — season window, adapter reference, `training_targets` per sport; all 6 sports registered and active |

## Networking: no NAT Gateway, anywhere

Only `{sport}-predict` is VPC-attached (private subnets, no public IP) as a defense-in-depth boundary — it's reachable from outside the account, same as `predict-read`/`live-scores`, but it's the one that runs the actual model. `predict-read` and `live-scores` deliberately are **not** VPC-attached: neither ever calls anything but DynamoDB/S3 over the plain AWS API path, so the extra network hop would only add cold-start latency with no real security benefit. The private-subnet comment in `Terraform/subnet-private.tf` spells out why there's **no NAT Gateway** at all — it runs roughly $32/month before any data transfer, a meaningful fixed cost this single-user project doesn't need. `predict` reaches only S3 and DynamoDB, only via VPC Gateway Endpoints (free, no fixed cost) — it has **no route to the public internet at all**, not even a slow or rate-limited one.

This is fine for every route that only ever needs DynamoDB/S3, which is most of them. It becomes a real constraint the moment `predict` needs a live call to an external API — first hit by NCAA MBB's conference-membership resolution (there's no static, low-realignment-risk table for it the way NBA's division table works, and no per-event field for it the way NCAAFB's CFBD source provides one; see `NCAAMBB_FEATURE_ENGINEERING.md`'s "Conference membership" section). The fix, and the general pattern for this constraint going forward:

**S3-relay pattern:** a Lambda that already has ordinary internet egress (ingest, schedule-sync, live-scores — none of these are VPC-attached, since none is `predict`) makes the live call and caches the result to S3 under a scoped prefix; `predict` reads that cache instead of calling out itself. The read still travels over the existing S3 Gateway Endpoint (no security-group or NAT change needed) — the only new grant is a `s3:GetObject` IAM statement scoped to that one prefix (see `iam-lambda-inference.tf`'s `ReadConferenceMembershipCache` statement for the concrete example). Any future sport needing a similar live external lookup from `predict` should reach for this same pattern rather than punching a NAT Gateway hole for one call.

## Access control

The frontend sits at a public URL by design (so it's reachable from anywhere), but every API call requires authentication — there is no anonymous access path.

**Cognito User Pool.** One user pool, one app client, self-signup disabled. The project owner creates their own user manually (via the AWS console or CLI) rather than exposing a public registration flow — the only way to obtain a valid token is to already have credentials created that way.

**API Gateway Cognito authorizer.** API Gateway has a built-in integration for validating Cognito-issued JWTs on every request — no custom Lambda authorizer needed. Requests without a valid, unexpired token are rejected at the gateway, before they ever reach a Lambda.

**CloudFront + S3 for the Flutter Web app.** The compiled Flutter app is static and technically loads for anyone who hits the URL, but it's useless without logging in — every data call it makes requires the Cognito token, so an unauthenticated visitor sees a login screen and nothing else.

**WAF.** A WebACL in front of CloudFront runs AWS's own curated IP-reputation managed rule, blocking known-malicious IPs before they ever reach an origin — layered on top of, not instead of, the auth requirement above.

**Usage plan as a further layer.** The API Gateway stage is throttled account-wide (60 requests/second sustained, burst 100) regardless of authentication. Since the project owner is the only legitimate caller, any traffic pattern that would hit this limit is almost certainly not them — a cheap tripwire against scraping or abuse even if a token were ever compromised.

## External dependencies

- Each sport's upstream data API (ESPN, CFBD, Jolpica-F1, etc.) is unofficial/undocumented in most cases — every adapter treats it as a data source only, never assumes a documented contract will hold across seasons.
- **ECR** is pre-existing and shared across projects outside this Terraform stack (referenced via `var.ecr_repo_url`, not managed here) — every container image (each sport's `predict` Lambda, every Fargate feature-engineering/backfill task) pulls from it.

## Not shown on `architecture.html`

**IAM roles** — every compute resource runs under its own least-privilege role (`lambda_pipeline` for ingest/normalize/schedule-sync, `lambda_inference` shared by every sport's `predict`/`predict-read`, `lambda_live_scores`, `{sport}_backfill`, `ecs_pipeline`, `stepfunctions_orchestrator`, `eventbridge_invoke`) rather than one shared role. `predict-read` reuses `lambda_inference` rather than getting its own scoped-down role — everything it needs (S3 read on model artifacts, DynamoDB read on events/predictions) is already a strict subset of what that role grants `predict`. Not drawn as diagram nodes since IAM is a cross-cutting permissions concern, not a data-flow hop — see the `iam-*.tf` files for the exact policy each role carries.

**Cost controls** (`Terraform/budgets.tf`) — an AWS Budgets alert scoped to this project's cost-allocation tags (project-total and per-sport), emailing at 80%/100% actual spend and 100% forecasted spend. Observes account-wide spend passively rather than participating in any request or data flow, so it isn't drawn either — see `design/TAGGING_STRATEGY.md`.
