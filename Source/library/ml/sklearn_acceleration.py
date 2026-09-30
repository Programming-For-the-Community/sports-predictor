"""
Intel's scikit-learn extension (sklearnex), applied when it's installed.
Imports nothing from scikit-learn itself, so a training script can call
patch_sklearn_if_available() before any sklearn import -- the patch only
affects sklearn modules imported after it runs. XGBoost and LightGBM have
their own native optimization and aren't affected either way.
"""


def patch_sklearn_if_available() -> bool:
    """True when sklearnex was found and applied."""
    try:
        from sklearnex import patch_sklearn
    except ImportError:
        return False
    patch_sklearn()
    return True
