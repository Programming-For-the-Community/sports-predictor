"""
SageMaker script-mode entry point for the one-off GPU training benchmark
(see this directory's README.md). Installs a fresh, version-pinned
pandas/pyarrow/scikit-learn/scikit-learn-intelex/xgboost stack matching
production's own pin (Source/model-training/ncaafb/requirements.txt) --
not trusting whatever SageMaker's built-in XGBoost framework container
happens to bundle by default, since library/ml/model_types.py's GPU path
needs xgboost>=2.0's device= API, not the older gpu_hist tree_method some
SageMaker XGBoost container versions still default to. Including
scikit-learn-intelex too, not just xgboost, matters for a fair
comparison -- RandomForestRegressorAdapter/ElasticNetAdapter/
MLPRegressorAdapter are plain CPU scikit-learn with no GPU path at all
(see the README), and production's Fargate tasks already get intelex's
CPU speedup; leaving it out here would make their share of the run
artificially slower than production, not just XGBoost's share faster.

Everything else is deliberately unchanged: this imports and runs the
real, unmodified train_player_prop_model.main() (bundled alongside this
file via invoke.py's `dependencies`) so the benchmark measures the actual
production training path, not a simplified stand-in.
"""
import subprocess
import sys

_PINNED_REQUIREMENTS = [
    "pandas>=2.2",
    "pyarrow>=16.0",
    "scikit-learn>=1.4",
    "scikit-learn-intelex>=2024.0",
    "xgboost>=2.0",
]


def _install_pinned_requirements() -> None:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *_PINNED_REQUIREMENTS], check=True)


if __name__ == "__main__":
    _install_pinned_requirements()

    import train_player_prop_model

    train_player_prop_model.main()
