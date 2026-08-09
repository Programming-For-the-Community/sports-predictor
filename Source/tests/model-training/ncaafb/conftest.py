import sys
import os

# Make the ncaafb model-training modules importable without installing
# them as a package -- same pattern as tests/model-training/nfl/conftest.py.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "model-training", "ncaafb"))
