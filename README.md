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
source .venv/bin/activate      # Mac/Linux
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
