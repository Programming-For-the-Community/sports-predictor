import sys
import os

# Make the ncaafb feature-engineering module importable without installing
# it as a package -- same pattern as tests/feature-engineering/nfl/conftest.py.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "feature-engineering", "ncaafb"))
