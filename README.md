# sports-predictor

A personal-use AI/ML platform that predicts outcomes and statistics for six sports — NFL, NCAA Football, NBA, NCAA Men's Basketball, PGA Tour, and Formula 1 — running end to end on AWS with a serverless-first, cost-first architecture. Every sport is either a **head-to-head event** (two teams/competitors — NFL, NCAA FB, NBA, NCAA MBB) or a **field event** (many entrants, ranked finish — PGA Tour, F1); storage schema and orchestration are built around those two shapes, with only the per-sport data source, feature engineering, and models varying. New sports onboard by writing one adapter and adding one row to a sport registry table, not by changing shared orchestration or serving code — all six sports run through the identical registry-driven pipeline today.

Built for a single user, not a public product — the frontend and API sit at a public URL for convenience, but every API call requires a Cognito-issued token tied to the project owner's account, and every service choice favors on-demand/serverless pricing over always-on compute.

## Documentation

**[design/](design/)** — architecture and design decisions, not day-to-day operational detail:
- [Architecture](design/ARCHITECTURE.md) — system diagrams (single-sport and multi-sport), the no-NAT-Gateway networking constraint and its S3-relay workaround, access control design, service rationale
- [Data Schema](design/DATA_SCHEMA.md) — entity/event/prediction schema, DynamoDB table design, sport registry shape, S3 key conventions
- [Data Sources](design/DATA_SOURCES.md) — free data source per sport, update cadence, auth requirements, API key handling
- [Tagging Strategy](design/TAGGING_STRATEGY.md) — AWS resource tagging convention for cost tracking in Billing/Cost Explorer
- [Frontend Style](design/FRONTEND_STYLE.md) — the Flutter Web app's "Arena" visual language (design tokens, components, data-viz conventions)

**[docs/](docs/)** — as-built reference for what's actually deployed:
- [AWS Architecture](docs/AWS_ARCHITECTURE.md) — current-state AWS architecture diagrams: client request path, scheduled data pipeline, observability/cost guardrails
- [Sport Predictor API](docs/SPORT_PREDICTOR_API.md) — every live API Gateway route and request/response shape, per sport
- Feature engineering, one doc per sport — what each sport's trained-model features are and where they come from: [NFL](docs/NFL_FEATURE_ENGINEERING.md), [NCAAFB](docs/NCAAFB_FEATURE_ENGINEERING.md), [NBA](docs/NBA_FEATURE_ENGINEERING.md), [NCAAMBB](docs/NCAAMBB_FEATURE_ENGINEERING.md), [PGA](docs/PGA_FEATURE_ENGINEERING.md), [F1](docs/F1_FEATURE_ENGINEERING.md)

## Repo layout

```
Source/
  aws-lambdas/       # one dir per sport, plus shared/ -- ingest, normalize, predict, predict-read, live-scores, schedule-sync
  library/           # shared AWS/HTTP/schema/ML utilities, installed in editable mode (see below)
  data-backfills/    # one-time historical backfill jobs, run as standalone Fargate tasks
  feature-engineering/  # Fargate entrypoints building training Parquet datasets per sport
  model-training/    # training scripts per sport/target, run on the EC2 training fleet
  front-end/         # Flutter Web app
  tests/             # mirrors the structure above
Terraform/           # all infrastructure -- one file per resource/resource-group, no modules
design/, docs/       # see Documentation above
```

## Local Development Setup

### 1. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/Scripts/activate  # Windows (Bash)
# or
.venv\Scripts\Activate.ps1     # Windows (PowerShell)
```

### 2. Install the shared library

The `Source/library` package contains shared AWS, HTTP, schema, and ML utilities used across every sport's Lambdas, backfill jobs, and training scripts. Install it in editable mode so local changes are picked up immediately:

```bash
pip install -e Source/library
```

### 3. Install job-specific dependencies

Each backfill/training job has its own `requirements.txt`. For example, to set up the NFL backfill:

```bash
pip install -r Source/data-backfills/nfl/requirements.txt
```

## Running Tests

From the repo root with the venv active, run the whole suite or scope to one area:

```bash
pytest Source/tests/ -v                          # everything
pytest Source/tests/library/ -v                  # shared library only
pytest Source/tests/aws-lambdas/nfl/ -v           # one sport's Lambdas
```

## Debugging Tests in VS Code

Create a `.vscode/launch.json` at the repo root with the configuration below. Add one entry per area you debug often so you can pick exactly which folder to run from the **Run and Debug** panel (`F5`).

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Pytest: NFL Lambda tests",
            "type": "debugpy",
            "request": "launch",
            "module": "pytest",
            "args": [
                "Source/tests/aws-lambdas/nfl/",
                "-v"
            ],
            "cwd": "${workspaceFolder}",
            "python": "${workspaceFolder}/.venv/Scripts/python.exe",
            "justMyCode": true
        }
    ]
}
```

Set a breakpoint in any test file, select the matching configuration from the dropdown, and press `F5`. To run a single test you can also append `-k test_name` to the `args` array, or use VS Code's **Test Explorer** (`Ctrl+Shift+P` → "Testing: Focus on Test Explorer View") and right-click any test to choose **Debug Test**.

## Running a Backfill Task (Manual)

Every sport's backfill runs as a standalone Fargate task, not a service -- it runs to completion and stops, no always-on cost. Launched manually from the AWS Console rather than a CLI command or scheduled trigger (safe to re-run at any time -- every write is an upsert, and an already-loaded item is skipped).

### Launching it

1. **ECS Console → Clusters** → open the cluster from the `cluster_name` Terraform output (default: `sports-predictor-cluster`).
2. **Run new Task** → Launch type **FARGATE** → Task definition family `sports-predictor-<sport>-backfill` (e.g. `sports-predictor-nfl-backfill`), latest revision.
3. **Networking** (picked manually every run -- task definitions have no network config slot):
   - VPC: the project VPC
   - Subnets: the **public** subnets, not private
   - Security group: `sports-predictor-fargate-internet-egress`
   - Auto-assign public IP: **ENABLED** -- required. There's no NAT Gateway (deliberately, for cost), so this is the only way the task reaches its sport's upstream API.

### What to expect

- **Defaults**: each sport's own default season range and batch concurrency (see that sport's own backfill script). To backfill a narrower range instead (e.g. retrying just one season), expand **Container overrides → Environment variables** for that one run -- the task definition itself doesn't need to change.
- **Progress**: CloudWatch Logs → log group `/ecs/sports-predictor-<sport>-backfill`. Look for a per-season/per-tournament/per-race progress line and a final summary line (items processed, failed).
- **Success**: the task stops with exit code `0` if everything loaded cleanly.
- **Partial failure**: exit code `1` means the run finished but some individual items failed (logged, not fatal to the whole run) -- a failure manifest is written to S3. Just re-run the task as-is: already-loaded items are skipped, so a re-run only retries what's actually missing.
- **Cost**: a few cents per full run at this task's size (Fargate bills per-second) -- well within the project's budget alarm.
