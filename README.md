# sports-predictor
AWS hosted AI driven application to predict sports outcomes while providing up-to-date information to ensure the models stay current

## Local Development Setup

### 1. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/Scripts/activate  # Windows (Bash)
# or
.venv\Scripts\Activate.ps1     # Windows (PowerShell)
# or
```

### 2. Install the shared library

The `Source/library` package contains shared AWS, HTTP, and schema utilities used across all backfill jobs and Lambdas. Install it in editable mode so local changes are picked up immediately:

```bash
pip install -e Source/library
```

### 3. Install job-specific dependencies

Each backfill job has its own `requirements.txt`. For example, to set up the NFL backfill:

```bash
pip install -r Source/data-backfills/nfl/requirements.txt
```

## Running Tests

From the repo root with the venv active:

```bash
pytest Source/tests/data-backfills/nfl/ -v
```

## Debugging Tests in VS Code

Create a `.vscode/launch.json` at the repo root with the configuration below. Each backfill job gets its own entry so you can select exactly which test folder to debug from the **Run and Debug** panel (`F5`).

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Pytest: NFL backfill tests",
            "type": "debugpy",
            "request": "launch",
            "module": "pytest",
            "args": [
                "Source/tests/data-backfills/nfl/",
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

## Running the NFL Backfill Task (Manual)

The backfill runs as a standalone Fargate task, not a service -- it runs to completion and stops, no always-on cost. It's currently launched manually from the AWS Console rather than a CLI command or scheduled trigger.

### Launching it

1. **ECS Console → Clusters** → open the cluster from the `nfl_cluster_name` Terraform output (default project name: `sports-predictor-cluster`).
2. **Run new Task** → Launch type **FARGATE** → Task definition family `sports-predictor-nfl-backfill`, latest revision.
3. **Networking** (this has to be picked manually every run -- task definitions have no network config slot; see the earlier discussion in this repo's history for why):
   - VPC: the project VPC
   - Subnets: the **public** subnets, not private
   - Security group: `sports-predictor-fargate-internet-egress`
   - Auto-assign public IP: **ENABLED** -- required. There's no NAT Gateway (deliberately, for cost), so this is the only way the task reaches ESPN's API.

### What to expect

- **Defaults**: a full run backfills seasons 2016–2025 in 5 concurrent batches of 2 seasons each. To backfill a narrower range instead (e.g. retrying just one season), expand **Container overrides → Environment variables** and override `START_SEASON`/`END_SEASON`/`BATCH_SIZE`/`REQUEST_DELAY_SECONDS` for that one run -- the task definition itself doesn't need to change.
- **Duration**: roughly 20-30 minutes for a full 2016–2025 run, by design estimate -- the ESPN request rate is capped globally (not multiplied by the 5 concurrent batches) to stay polite to an unofficial API. Not yet empirically confirmed against a real full run.
- **Progress**: CloudWatch Logs → log group `/ecs/sports-predictor-nfl-backfill`. Look for `Starting season <year>` / `Finished season <year>: N games processed, M failed` per season, and a final `Backfill complete in <seconds>s: N games processed, M failed` summary line.
- **Success**: the task stops with exit code `0` if every game loaded cleanly.
- **Partial failure**: exit code `1` means the run finished but some individual games failed (logged, not fatal to the whole run) -- a failure manifest is written to `s3://<raw-bucket>/nfl/backfill-failures/<timestamp>.json`. **Just re-run the task as-is**: already-loaded games are skipped (idempotent, checked against the raw S3 landing zone), so a re-run only retries what's actually missing.
- **Cost**: a few cents per full run at this task's size (1 vCPU / 2GB, Fargate bills per-second) -- well within the project's $15/month budget.
