import sys
import os

# Make the ncaambb backfill modules importable without installing them as a package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "data-backfills", "ncaambb"))
