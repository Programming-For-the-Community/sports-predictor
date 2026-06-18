# Project Plan

A phased checklist in implementation order. Each phase assumes the previous one is functionally complete — don't start Phase 2 before Phase 1's model is actually predicting and displaying in the frontend. The point of this ordering is to prove the architecture on the easiest case before spending effort generalizing it.

## Phase 0 — Foundations (shared infrastructure)

- [ ] Define entity/event schema (see `DATA_SCHEMA.md`)
- [ ] Set up IAM roles for Lambda, Fargate, and Step Functions with least-privilege access to the specific tables/buckets they need
- [ ] Create S3 buckets: raw data lake, model artifacts, frontend hosting
- [ ] Create DynamoDB tables: entities, events, predictions (sport registry table comes in Phase 4)
- [ ] Apply the tagging strategy to every resource at creation time (see `TAGGING_STRATEGY.md`) — don't defer this, retrofitting tags later means re-auditing every resource
- [ ] Set up Cognito User Pool + App Client, create your own user, disable self-signup
- [ ] Stand up API Gateway with a Cognito authorizer attached (no routes yet — just the auth scaffold)
- [ ] Set an AWS Budget alert at $10–15/month as a misconfiguration tripwire
- [ ] Initialize repo structure (`/adapters`, `/core`, `/infra`, `/frontend`, `/docs`) and commit this documentation set

## Phase 1 — NFL adapter (proof of architecture)

- [ ] Write the NFL ingest function pulling from nflverse
- [ ] Write the normalize function mapping nflverse data into the entity/event schema
- [ ] Backfill 10 years of historical NFL data
- [ ] Build feature engineering: rolling averages, an Elo-style rating, home/away and rest-day splits
- [ ] Train the first XGBoost win-probability model, store the artifact in S3
- [ ] Write the inference Lambda and wire it to an API Gateway route
- [ ] Build a minimal React frontend showing one sport's predictions
- [ ] Confirm the Cognito login gate works end to end on the live URL — log out, confirm the API rejects unauthenticated calls, log back in, confirm it works

## Phase 2 — NCAA FB adapter (validate generalization)

- [ ] Register for a CFBD API key
- [ ] Write NCAA FB ingest/normalize functions against the same schema used for NFL
- [ ] Confirm no changes were needed to shared storage, serving, or frontend code — if you found yourself editing `core/`, that's a signal the Phase 1 abstraction wasn't general enough
- [ ] Backfill 10 years, train a model, add it to the frontend

## Phase 3 — NBA + NCAA MBB adapters (stress-test volume)

- [ ] Write the NBA adapter (nba_api or balldontlie)
- [ ] Write the NCAA MBB adapter (ESPN endpoints or hoopR)
- [ ] Confirm the ingestion schedule and DynamoDB throughput hold up under a much higher game density than NFL (back-to-backs, dozens of games per day in-season)
- [ ] Backfill, train, and add both to the frontend

## Phase 4 — Generalize orchestration

- [ ] Build the sport registry table (adapter reference, polling cadence, current model version)
- [ ] Replace the four per-sport EventBridge rules with a single Step Functions Map state driven by the registry
- [ ] Extract shared feature-engineering primitives (rolling windows, rating updates, rest/travel calculations) into a common library used by all head-to-head adapters
- [ ] Build a shared backtesting harness that produces the same accuracy/calibration report regardless of which sport's model it's pointed at

## Phase 5 — PGA Tour adapter (field-event schema)

- [ ] Extend the event schema to support N participants per event instead of exactly 2 (see `DATA_SCHEMA.md`)
- [ ] Write the PGA adapter against ESPN's golf endpoints, optionally layering in Data Golf if you want their published skill ratings as a feature
- [ ] Build a ranking-style model (multinomial classification or top-N probability) rather than reusing the win/loss classifier
- [ ] Wire into the frontend, add to the sport registry

## Phase 6 — F1 adapter (reuse the field-event pattern)

- [ ] Write the F1 adapter against Jolpica-F1
- [ ] Reuse PGA's field-event feature and model pattern, adjusted for qualifying position and constructor-level features
- [ ] Wire into the frontend, add to the sport registry

## Phase 7 — Hardening

- [ ] Review tag coverage in Cost Explorer — confirm you can actually see per-sport and per-component cost breakdowns, not just a lump total
- [ ] Add CloudWatch alarms for unexpected Lambda or Fargate error rates
- [ ] Set explicit log retention policies so CloudWatch Logs doesn't grow unbounded
- [ ] Document and test the runbook for adding a hypothetical sport #7 — if the registry-driven pattern actually works, this should take an afternoon, not a redesign
