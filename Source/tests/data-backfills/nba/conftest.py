import sys
import os

# Make the nba backfill modules importable without installing them as a package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "data-backfills", "nba"))
# Shared hand-built ESPN payloads (../_espn_payloads.py)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
