"""
S3 key for a sport's model-performance scorecard -- how each promoted model
has actually performed this season and last period -- written daily by the
shared model-performance Lambda and read by GET /{sport}/model-performance.
Same model-artifacts bucket as versioned models and season projections, under
its own top-level prefix (not nested under f"{sport}/", which list_models
scans and treats every top-level segment of as a model name).
"""


def model_performance_key(sport: str) -> str:
    return f"model-performance/{sport}/latest.json"
