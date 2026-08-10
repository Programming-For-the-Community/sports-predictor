"""
Helpers for versioned model artifact storage in the model-artifacts S3
bucket. Each model (win-probability, score-margin, one per player-prop
stat) versions independently under its own <sport>/<model_name>/v{N}/
prefix -- see Terraform/s3-model-artifacts.tf. Shared here because every
sport's training task needs the same "what's the next version number"
and "where does this model's artifact go" logic, not just NFL's.
"""
import re

_VERSION_SEGMENT = re.compile(r"/v(\d+)/")


def model_artifact_prefix(sport: str, model_name: str) -> str:
    return f"{sport}/{model_name}/"


def next_model_version(existing_keys: list[str]) -> int:
    """existing_keys: keys already under this model's own prefix (see
    model_artifact_prefix) -- e.g. from S3Manager.list_keys(prefix).
    Returns 1 if no versions exist yet."""
    versions = [int(match.group(1)) for key in existing_keys if (match := _VERSION_SEGMENT.search(key))]
    return max(versions, default=0) + 1


def model_artifact_key(sport: str, model_name: str, version: int, filename: str) -> str:
    return f"{model_artifact_prefix(sport, model_name)}v{version}/{filename}"


def current_version_key(sport: str, model_name: str) -> str:
    """Key of the pointer file recording which version of this model is
    currently promoted to production -- lives directly under the model's
    prefix (not inside any v{N}/ folder), so next_model_version's /v(\\d+)/
    pattern never mistakes it for a version."""
    return f"{model_artifact_prefix(sport, model_name)}current.json"


def run_progress_key(sport: str, model_name: str, run_id: str) -> str:
    """Key of the resumable-progress breadcrumb for one tournament run
    (library.ml.backtest.run_backtest) -- lives under a top-level
    training-runs/ prefix, entirely separate from the model's own
    <sport>/<model_name>/ prefix used above, so it never shows up in
    next_model_version's version listing (list_keys(model_artifact_prefix(...)))
    and can't be mistaken for a version."""
    return f"training-runs/{sport}/{model_name}/{run_id}/progress.json"
