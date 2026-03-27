"""
Daily synthetic data updater.
Calculates how many trading days are missing since the last trade
and appends them. Safe to run multiple times per day (idempotent).

Usage
-----
# Run directly:
    python tools/synthetic_daily_updater.py

# Or via scheduled task / cron (see below for setup instructions).
"""

import json
import os
import sys
from datetime import datetime, timedelta

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(TOOLS_DIR)
TRADES_PATH = os.path.join(BASE_DIR, "data", "synthetic_bot_trades.json")

# Import the generator
sys.path.insert(0, TOOLS_DIR)
from synthetic_data_generator import append_days


def days_since_last_trade() -> int:
    """Return how many calendar days have passed since the last trade."""
    if not os.path.exists(TRADES_PATH):
        print("No synthetic data found. Run: python tools/synthetic_data_generator.py --generate")
        sys.exit(1)

    with open(TRADES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    trades = data.get("trades", [])
    if not trades:
        return 7  # fallback: generate a week

    last_time = max(t["open_time"] for t in trades)
    # strip timezone info for comparison
    last_date = datetime.fromisoformat(last_time.replace("+08:00", "")).date()
    today = datetime.now().date()
    gap = (today - last_date).days
    return gap


def main():
    gap = days_since_last_trade()

    if gap <= 0:
        print(f"Data is already up to date (last trade is from today).")
        return

    print(f"Last trade is {gap} day(s) old. Appending new trades...")
    append_days(gap)
    print("Synthetic data updated successfully.")


if __name__ == "__main__":
    main()
