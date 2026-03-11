#!/usr/bin/env python3
"""
Bot Trades Exporter – Mom's Account
Retrieves all trades placed by the bot from MT5 and exports them to JSON.
Each entry in the output represents one complete trade (open → close),
built by pairing DEAL_ENTRY_IN deals with DEAL_ENTRY_OUT deals by position_id.
"""

import sys
import os

# Re-use the shared exporter class
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from bot_trades_exporter import BotTradesExporter

# ===================== CONFIGURATION =====================
BOT_MAGIC_NUMBERS = [779]
DAYS_BACK = 365
OUTPUT_FILE = os.path.join(PROJECT_ROOT, "data", "bot_trades_moms_account.json")

# Positions to exclude (test trades placed manually)
BLACKLISTED_POSITIONS = {40371931, 40371975, 40371949, 40371717}


# ===================== MAIN =====================

def main():
    exporter = BotTradesExporter(
        magic_numbers=BOT_MAGIC_NUMBERS,
        blacklisted_positions=BLACKLISTED_POSITIONS,
    )

    try:
        if not exporter.connect_mt5():
            return False

        if not exporter.fetch_and_group_deals(days_back=DAYS_BACK):
            print("Error retrieving deals")
            return False

        count = exporter.build_completed_trades()

        if count > 0:
            exporter.save_to_json(output_file=OUTPUT_FILE)
            exporter.print_summary()
        else:
            print("No completed bot trades found to export")

        return True

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        exporter.shutdown()


if __name__ == "__main__":
    exit(0 if main() else 1)
