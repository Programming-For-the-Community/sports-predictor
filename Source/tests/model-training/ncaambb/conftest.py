import sys
import os

# Make the ncaambb model-training module importable without installing it as a package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "model-training", "ncaambb"))
