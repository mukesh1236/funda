"""Run the daily collection + validation pipeline once, from the CLI.

Usage (from project root):
    python -m scripts.run_daily
"""
import logging
import os
import sys

# UTF-8 stdout on Windows so the digest never hits cp1252 charmap errors.
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Allow `python scripts/run_daily.py` as well as `-m scripts.run_daily`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.jobs import run_daily  # noqa: E402

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    digest = run_daily()
    print(
        f"\nDone. {digest['new_recommendations']} new recs, "
        f"{digest['targets_hit']} targets hit."
    )
