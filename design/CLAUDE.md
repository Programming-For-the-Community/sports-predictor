# Multi-Sport Prediction Platform — Project Context

This file gives Claude Code (or any future contributor) the context needed to work on this repository without re-deriving prior decisions. Read this first, then the relevant doc in `/docs` for the area you're touching.

## What this is

A personal-use AI/ML platform that predicts outcomes and statistics for six sports: NFL, NCAA Football, NBA, NCAA Men's Basketball, PGA Tour, and Formula 1. It runs on AWS with a serverless-first architecture, prioritizing low cost over scale, since it is built for a single user — not a public product, even though the frontend is reachable at a public URL.

## Core architectural decisions

1. **Two data shapes, not six bespoke pipelines.** Every sport is either a head-to-head event (two teams/competitors, binary or score-based outcome — NFL, NCAA FB, NBA, NCAA MBB) or a field event (many entrants, ranked finish — PGA Tour, F1). Storage schema and orchestration are built around these two shapes; only the per-sport adapter (data source, feature engineering, model) varies.
2. **One model per sport, shared plumbing.** There is no single cross-sport model — "predict NFL win probability" and "predict PGA top-10 finish" are different statistical problems with different targets and base rates. What's shared is the storage schema, orchestration, the inference Lambda's response contract, and common feature-engineering primitives (rolling averages, rating systems).
3. **Registry-driven onboarding.** New sports are added by writing one adapter module and adding one row to the sport registry (see `docs/DATA_SCHEMA.md`) — not by changing shared orchestration or serving code.
4. **Cost-first, not scale-first.** No anticipated public traffic. Every service choice favors on-demand/serverless pricing (Lambda, DynamoDB on-demand, Fargate for scheduled jobs) over always-on compute.
5. **Public URL, locked down.** The frontend and API are reachable on a public endpoint for convenience, but every API call requires a valid Cognito-issued token tied to the project owner's account. See `docs/ARCHITECTURE.md` for the access control design.

## Build order

See `docs/PROJECT_PLAN.md` for the full checklist. Short version: get NFL working end to end first (proof of architecture), then NCAA FB (validates the head-to-head adapter actually generalizes), then NBA + NCAA MBB together (stress-tests pipeline volume and cadence), then refactor into the registry/Step Functions pattern, then add PGA Tour and F1 last since they require extending the schema to field events rather than head-to-head matchups.

## Where to look for what

- `docs/ARCHITECTURE.md` — system diagrams (single-sport and multi-sport), access control design, service rationale
- `docs/PROJECT_PLAN.md` — phased implementation checklist
- `docs/DATA_SOURCES.md` — free data sources per sport, update cadence, auth requirements
- `docs/DATA_SCHEMA.md` — entity/event schema, DynamoDB table design, sport registry shape
- `docs/TAGGING_STRATEGY.md` — AWS resource tagging convention for cost tracking in Billing/Cost Explorer

## Conventions

- Every sport adapter implements the same interface: `fetch()`, `normalize()`, `build_features()`, `train()`, `predict()`.
- Every AWS resource is tagged per `docs/TAGGING_STRATEGY.md` at creation time, via infrastructure-as-code, not applied manually after the fact.
- Models are gradient-boosted trees (XGBoost or LightGBM) trained as scheduled Fargate tasks, never as always-on SageMaker endpoints.
- Head-to-head sports produce a win probability and predicted margin. Field-event sports (PGA, F1) produce a probability distribution across finishing positions — don't try to force these into the same output shape.

## Repo structure (target)

```
/adapters
  /nfl/{ingest,normalize,features,train,predict}.py
  /ncaa_fb/{...}
  /nba/{...}
  /ncaa_mbb/{...}
  /pga/{...}
  /f1/{...}
/core
  schema.py        # shared entity/event table helpers
  storage.py        # shared S3/DynamoDB helpers
  registry.py        # loads the sport registry, drives the Step Functions map
/infra              # CDK/Terraform for shared infrastructure
/frontend           # React SPA
/docs               # this documentation set
```
