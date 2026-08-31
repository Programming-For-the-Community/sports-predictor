import sys
import os

# Make the f1 feature-engineering module importable without installing it as a package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "feature-engineering", "f1"))
