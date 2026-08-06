"""
S3 key for the cached season projection (standings + player-prop
leaderboards) written weekly by the scheduled compute path in
Source/aws-lambdas/nfl/predict/handler.py and read by GET /{sport}/season
(Source/aws-lambdas/nfl/predict-read/handler.py, via
library.serving.nfl_reads.get_season_projection). Lives in the same
model-artifacts bucket as versioned models (Terraform/s3-model-artifacts.tf)
but under its own top-level prefix, not nested under f"{sport}/" --
nfl_reads.list_models scans f"{sport}/" and treats every top-level segment
under it as a model name, so nesting there would surface
"season-projection" as a bogus, always-unpromoted model.
"""


def season_projection_key(sport: str) -> str:
    return f"season-projections/{sport}/latest.json"
