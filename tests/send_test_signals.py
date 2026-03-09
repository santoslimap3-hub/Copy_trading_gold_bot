"""
Comprehensive Test Signal Sender for Gold Copy-Trading Bot
==========================================================
Sends signals to the TEST Telegram channel to verify every code path
in the bot. Run this WHILE the bot is running and watch the bot console.

Usage:
    python tests/send_test_signals.py [--test N] [--dry-run] [--list]

Options:
    --test N    Run only test number N (e.g. --test 3)
    --dry-run   Print messages instead of sending them
    --list      List all tests and exit

IMPORTANT:
    - Make sure the bot is running BEFORE you start this script
    - Each test pauses to let the bot react — watch the bot console
    - Some tests place real pending orders on MT5 — clean up after!
    - Tests are designed for XAUUSD at ~$5150-5200 price range
      Adjust PRICE_BASE if gold is at a very different level
"""

import asyncio
import sys
import os
import time
import argparse

# Fix Windows console encoding for emoji/unicode characters
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Add parent dir so we can import shared config
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telethon import TelegramClient
import MetaTrader5 as mt5

# ─── Config (must match bot_v2.py) ───
API_ID = 34597981
API_HASH = "2cd59609b6cacb56da261e43fdb897ea"
TEST_CHANNEL_ID = -1003817819872
SESSION_FILE = os.path.join(os.path.dirname(__file__), "test_sender_session")
SYMBOL = "XAUUSD"
MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

# ─── Price configuration ───
# Fallback only — the script fetches the live price from MT5 at startup.
PRICE_BASE_FALLBACK = 5170


def get_live_price() -> float:
    """Fetch the current XAUUSD bid price from MT5."""
    if not mt5.initialize(path=MT5_PATH):
        return 0.0
    tick = mt5.symbol_info_tick(SYMBOL)
    mt5.shutdown()
    if tick is None:
        return 0.0
    return round(float(tick.bid), 2)


# ═══════════════════════════════════════════════════════════════════════
# TEST DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════

def build_tests(p: int):
    """
    Build all test scenarios.  `p` = PRICE_BASE.

    Each test is a dict:
      - name: description
      - steps: list of (action, delay_before, text_or_callable)
        action: "send" (new msg), "edit" (edit previous msg), "wait" (just wait)
    """
    tests = []

    # ──────────────────────────────────────────────────────────────────
    # TEST 1: Basic BUY signal → zone via edit → SL/TP via edit
    # Expected: signal buffered → zone parsed → limit placed → SL/TP applied
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "BUY signal → zone edit → SL/TP edit (happy path)",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW"),
            ("wait", 5, "Waiting for bot to buffer signal..."),
            ("edit", 3, f"XAU USD BUY NOW  {p+2} - {p-2}\n\n🥇TP1 {p+6}\n🥈TP2 {p+8}\n🥉TP3 {p+10}\n🏅TP4 {p+12}\n      TP5 {p+20}\n\n🚫SL {p-10}"),
            ("wait", 10, "Waiting for bot to place limit & apply SL/TP..."),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 2: Basic SELL signal → zone via edit → SL/TP via edit
    # Expected: same flow but SELL side
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "SELL signal → zone edit → SL/TP edit (happy path)",
        "steps": [
            ("send", 0, f"XAU USD SELL NOW"),
            ("wait", 5, "Waiting for bot to buffer signal..."),
            ("edit", 3, f"XAU USD SELL NOW  {p-2} - {p+2}\n\n🥇TP1 {p-6}\n🥈TP2 {p-8}\n🥉TP3 {p-10}\n🏅TP4 {p-12}\n      TP5 {p-20}\n\n🚫SL {p+10}"),
            ("wait", 10, "Waiting for bot to place limit & apply SL/TP..."),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 3: BUY with zone in initial message (no edit needed)
    # Expected: limit placed immediately — no buffering
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "BUY with zone in initial message (immediate limit)",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW  {p+2} - {p-2}\n\n🥇TP1 {p+6}\n🥈TP2 {p+8}\n\n🚫SL {p-10}"),
            ("wait", 10, "Bot should place limit immediately — no buffering"),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 4: Signal → edit with SL/TP but NO zone yet → second edit with zone
    # Expected: buffer signal → first edit just stores SL/TP → second edit triggers limit
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "Signal → edit (SL/TP only, no zone) → edit (zone added)",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW"),
            ("wait", 5, "Bot buffers signal..."),
            ("edit", 3, f"XAU USD BUY NOW\n\n🥇TP1 {p+6}\n\n🚫SL {p-10}"),
            ("wait", 5, "Edit has SL/TP but no zone — bot should log 'no zone yet'"),
            ("edit", 3, f"XAU USD BUY NOW  {p+2} - {p-2}\n\n🥇TP1 {p+6}\n\n🚫SL {p-10}"),
            ("wait", 10, "Zone now present — should place limit order"),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 5: TP1 HIT closure cancels pending orders
    # Expected: limit placed, then "TP1 HIT" → order cancelled
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "TP1 HIT closure cancels pending limit order",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW"),
            ("wait", 5, None),
            ("edit", 3, f"XAU USD BUY NOW  {p+2} - {p-2}\n\n🥇TP1 {p+6}\n\n🚫SL {p-10}"),
            ("wait", 8, "Limit should be placed..."),
            ("send", 2, f"TP1 HIT ✅"),
            ("wait", 5, "Bot should cancel the pending order"),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 6: SL HIT closure cancels pending orders
    # Expected: limit placed, then "SL HIT" → order cancelled
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "SL HIT closure cancels pending limit order",
        "steps": [
            ("send", 0, f"XAU USD SELL NOW"),
            ("wait", 5, None),
            ("edit", 3, f"XAU USD SELL NOW  {p-2} - {p+2}\n\n🥇TP1 {p-6}\n\n🚫SL {p+10}"),
            ("wait", 8, "Limit should be placed..."),
            ("send", 2, f"SL HIT ❌"),
            ("wait", 5, "Bot should cancel the pending sell order"),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 7: "Profit" message should NOT cancel anything
    # Expected: limit stays alive — "Profit" is just a status update
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "'Profit' message does NOT cancel orders (regression test)",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW"),
            ("wait", 5, None),
            ("edit", 3, f"XAU USD BUY NOW  {p+2} - {p-2}\n\n🥇TP1 {p+6}\n\n🚫SL {p-10}"),
            ("wait", 8, "Limit should be placed..."),
            ("send", 2, f"Profit 🎉"),
            ("wait", 3, "Bot should IGNORE this — order must stay alive"),
            ("send", 2, f"Close profit $10"),
            ("wait", 3, "Bot should IGNORE this too"),
            ("send", 2, f"TP1 HIT ✅"),
            ("wait", 5, "NOW it should cancel — TP HIT is a real closure"),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 8: TP1,2,3,4 HIT (multi-TP closure)
    # Expected: closure detected, pending cancelled
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "Multi-TP closure: 'TP1 ,2,3,4 HIT'",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW"),
            ("wait", 5, None),
            ("edit", 3, f"XAU USD BUY NOW  {p+2} - {p-2}\n\n🥇TP1 {p+6}\n\n🚫SL {p-10}"),
            ("wait", 8, None),
            ("send", 2, f"TP1 ,2,3,4 HIT ✅✅✅"),
            ("wait", 5, "Bot should recognise multi-TP closure"),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 9: Standalone SL/TP message (no prior signal — update last position)
    # Expected: bot finds most recent position and updates SL/TP
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "Standalone SL/TP message updates most recent position",
        "steps": [
            ("send", 0, f"TP 5200\n\nSL {p-15}"),
            ("wait", 8, "Bot should find most recent position and update SL/TP"),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 10: Price is BELOW zone → should use BUY STOP (not BUY LIMIT)
    # Zone is far above current price — BUY LIMIT would be rejected
    # Expected: bot detects price < limit_price and uses ORDER_TYPE_BUY_STOP
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "BUY with zone above price → BUY STOP auto-switch",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW"),
            ("wait", 5, None),
            # Zone well above likely current price
            ("edit", 3, f"XAU USD BUY NOW  {p+60} - {p+55}\n\n🥇TP1 {p+70}\n\n🚫SL {p+45}"),
            ("wait", 10, "Bot should place BUY STOP (zone above current price)"),
            # Clean up
            ("send", 2, f"SL HIT"),
            ("wait", 5, None),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 11: Price is ABOVE zone → should use SELL STOP (not SELL LIMIT)
    # Expected: bot detects price > limit_price and uses ORDER_TYPE_SELL_STOP
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "SELL with zone below price → SELL STOP auto-switch",
        "steps": [
            ("send", 0, f"XAU USD SELL NOW"),
            ("wait", 5, None),
            # Zone well below likely current price
            ("edit", 3, f"XAU USD SELL NOW  {p-55} - {p-60}\n\n🥇TP1 {p-70}\n\n🚫SL {p-45}"),
            ("wait", 10, "Bot should place SELL STOP (zone below current price)"),
            # Clean up
            ("send", 2, f"SL HIT"),
            ("wait", 5, None),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 12: Zone too wide (>30 points) → should be rejected
    # Expected: zone parse returns None, signal stays buffered, eventually times out
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "Zone too wide (>30 pts) → rejected, stays buffered",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW"),
            ("wait", 5, None),
            ("edit", 3, f"XAU USD BUY NOW  {p+50} - {p}\n\n🥇TP1 {p+60}\n\n🚫SL {p-10}"),
            ("wait", 5, "Zone is 50pts wide — bot should reject and log warning"),
            # Clean up buffer
            ("send", 2, f"SL HIT"),
            ("wait", 5, None),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 13: Zone too narrow (<0.5 pts) → should be rejected
    # Expected: zone parse returns None
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "Zone too narrow (<0.5 pts) → rejected",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW"),
            ("wait", 5, None),
            ("edit", 3, f"XAU USD BUY NOW  {p}.10 - {p}.20\n\n🥇TP1 {p+5}\n\n🚫SL {p-5}"),
            ("wait", 5, "Zone is 0.1pts — should be rejected"),
            ("send", 2, f"SL HIT"),
            ("wait", 5, None),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 14: Multiple rapid signals → each should be buffered separately
    # Expected: 3 separate buffered signals with different msg_ids
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "Multiple rapid signals → all buffered independently",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW"),
            ("send", 2, f"XAU USD SELL NOW"),
            ("send", 2, f"XAU USD BUY NOW"),
            ("wait", 5, "Bot should have 3 buffered signals with different msg_ids"),
            # Clean up
            ("send", 2, f"TP1 HIT"),
            ("wait", 5, None),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 15: Non-signal messages → should be ignored
    # Expected: bot logs "not recognized" and ignores
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "Non-signal messages ignored (noise filter)",
        "steps": [
            ("send", 0, "Good morning everyone! Market looking bullish today 🚀"),
            ("wait", 3, None),
            ("send", 2, "Remember to set your buy zones"),
            ("wait", 3, None),
            ("send", 2, f"Gold is at {p} right now"),
            ("wait", 3, "All 3 messages should be ignored — no BUY/SELL NOW"),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 16: SL/TP update via edit on pending limit order
    # Expected: pending order modified with new SL/TP
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "SL/TP update via edit on pending limit order",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW"),
            ("wait", 5, None),
            ("edit", 3, f"XAU USD BUY NOW  {p+2} - {p-2}\n\n🥇TP1 {p+6}\n\n🚫SL {p-10}"),
            ("wait", 8, "Limit placed..."),
            # Now edit again to change SL/TP
            ("edit", 3, f"XAU USD BUY NOW  {p+2} - {p-2}\n\n🥇TP1 {p+8}\n\n🚫SL {p-12}"),
            ("wait", 8, "Bot should modify the pending order SL/TP"),
            # Clean up
            ("send", 2, f"TP1 HIT"),
            ("wait", 5, None),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 17: Signal with reversed zone numbers (high - low vs low - high)
    # Expected: bot normalises with min/max — works either way
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "Zone numbers reversed (high - low) → still parsed correctly",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW"),
            ("wait", 5, None),
            # Zone: high number first
            ("edit", 3, f"XAU USD BUY NOW  {p-2} - {p+2}\n\n🥇TP1 {p+6}\n\n🚫SL {p-10}"),
            ("wait", 8, "Bot should normalise zone correctly regardless of order"),
            ("send", 2, f"SL HIT"),
            ("wait", 5, None),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 18: Zone wait timeout (signal buffered, no zone edit comes)
    # Expected: after ZONE_WAIT_TIMEOUT (120s), buffered signal times out
    # NOTE: This test takes ~130 seconds!
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "Zone wait timeout (no zone edit — 130s test!)",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW"),
            ("wait", 130, "Waiting 130s for ZONE_WAIT_TIMEOUT (120s)... go get a coffee ☕"),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 19: BUY signal with worst entry = zone_high (verify new logic)  
    # Zone close to price so limit/stop gets placed correctly
    # Expected: limit at zone_high (worst entry), NOT zone_low (best entry)
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "BUY worst entry = zone_high (not zone_low)",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW"),
            ("wait", 5, None),
            # Zone slightly above/around price so order is placeable
            ("edit", 3, f"XAU USD BUY NOW  {p+3} - {p-1}\n\n🥇TP1 {p+8}\n\n🚫SL {p-8}"),
            ("wait", 10, f"Bot should place limit at ${p+3} (zone_high/worst), NOT ${p-1} (zone_low/best)"),
            ("send", 2, f"SL HIT"),
            ("wait", 5, None),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 20: SELL signal with worst entry = zone_low (verify new logic)
    # Expected: limit at zone_low (worst entry), NOT zone_high (best entry)
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "SELL worst entry = zone_low (not zone_high)",
        "steps": [
            ("send", 0, f"XAU USD SELL NOW"),
            ("wait", 5, None),
            ("edit", 3, f"XAU USD SELL NOW  {p-1} - {p+3}\n\n🥇TP1 {p-8}\n\n🚫SL {p+8}"),
            ("wait", 10, f"Bot should place limit at ${p-1} (zone_low/worst), NOT ${p+3} (zone_high/best)"),
            ("send", 2, f"SL HIT"),
            ("wait", 5, None),
        ],
    })

    # ──────────────────────────────────────────────────────────────────
    # TEST 21: Multiple TP closures variant — "TP 2 HIT"
    # Expected: closure detected
    # ──────────────────────────────────────────────────────────────────
    tests.append({
        "name": "'TP 2 HIT' variant closure format",
        "steps": [
            ("send", 0, f"XAU USD BUY NOW"),
            ("wait", 5, None),
            ("edit", 3, f"XAU USD BUY NOW  {p+2} - {p-2}\n\n🥇TP1 {p+6}\n\n🚫SL {p-10}"),
            ("wait", 8, None),
            ("send", 2, f"TP 2 HIT"),
            ("wait", 5, "Should trigger closure"),
        ],
    })

    return tests


# ═══════════════════════════════════════════════════════════════════════
# TEST RUNNER
# ═══════════════════════════════════════════════════════════════════════

async def run_test(client, test: dict, test_num: int, dry_run: bool = False):
    """Run a single test, sending/editing messages to the test channel."""
    print(f"\n{'='*70}")
    print(f"  TEST {test_num}: {test['name']}")
    print(f"{'='*70}")

    last_msg = None  # track the last sent message for edits

    for i, (action, delay, text_or_note) in enumerate(test["steps"]):
        if delay > 0:
            if action == "wait":
                note = text_or_note or f"Waiting {delay}s..."
                print(f"  ⏳ {note}")
            await asyncio.sleep(delay if not dry_run else 0.1)

        if action == "send":
            if dry_run:
                print(f"  📤 [DRY-RUN] SEND: {text_or_note[:80]}")
                last_msg = None
            else:
                print(f"  📤 SEND: {text_or_note[:80]}")
                last_msg = await client.send_message(TEST_CHANNEL_ID, text_or_note)
                print(f"       → msg_id={last_msg.id}")

        elif action == "edit":
            if last_msg is None:
                if dry_run:
                    print(f"  ✏️  [DRY-RUN] EDIT: {text_or_note[:80]}")
                else:
                    print(f"  ⚠️  Cannot edit — no previous message (skipped)")
            else:
                if dry_run:
                    print(f"  ✏️  [DRY-RUN] EDIT msg_id={last_msg.id if last_msg else '?'}: {text_or_note[:80]}")
                else:
                    print(f"  ✏️  EDIT msg_id={last_msg.id}: {text_or_note[:80]}")
                    await client.edit_message(TEST_CHANNEL_ID, last_msg.id, text_or_note)

    print(f"  ✅ Test {test_num} complete")
    print()


async def main():
    parser = argparse.ArgumentParser(description="Send test signals to Telegram test channel")
    parser.add_argument("--test", type=int, help="Run only a specific test number")
    parser.add_argument("--dry-run", action="store_true", help="Print messages instead of sending")
    parser.add_argument("--list", action="store_true", help="List all tests and exit")
    parser.add_argument("--price", type=float, default=0, help="Override base price (default: fetch live from MT5)")
    parser.add_argument("--pause", type=int, default=5, help="Seconds to pause between tests (default: 5)")
    args = parser.parse_args()

    # Determine base price: CLI override -> live MT5 -> fallback
    if args.price > 0:
        base_price = args.price
        print(f"  Using manual price override: ${base_price}")
    else:
        print("  Fetching live XAUUSD price from MT5...")
        base_price = get_live_price()
        if base_price > 0:
            print(f"  Live price: ${base_price}")
        else:
            base_price = PRICE_BASE_FALLBACK
            print(f"  Could not get live price, using fallback: ${base_price}")

    # Round to nearest integer for clean zone numbers
    base_price = int(round(base_price))
    tests = build_tests(base_price)

    if args.list:
        print(f"\n{'='*70}")
        print(f"  AVAILABLE TESTS ({len(tests)} total)  [base price: ${base_price}]")
        print(f"{'='*70}")
        for i, t in enumerate(tests, 1):
            note = " SLOW (130s)" if "130s" in str(t) else ""
            print(f"  {i:2d}. {t['name'].replace(chr(8594), '->')}{note}")
        print()
        return

    print(f"\n{'='*70}")
    print(f"  GOLD BOT -- COMPREHENSIVE TEST SIGNAL SENDER")
    print(f"{'='*70}")
    print(f"  Test channel: {TEST_CHANNEL_ID}")
    print(f"  Base price:   ${base_price} (live from MT5)")
    print(f"  Dry run:      {args.dry_run}")
    print(f"  Tests:        {'#' + str(args.test) if args.test else f'ALL ({len(tests)})'}")
    print(f"{'='*70}\n")

    if not args.dry_run:
        print("  Connecting to Telegram...")
        client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
        await client.start()
        print("  ✅ Connected!\n")

        # Verify we can send to the test channel
        try:
            entity = await client.get_entity(TEST_CHANNEL_ID)
            print(f"  Channel: {getattr(entity, 'title', 'Unknown')}")
        except Exception as e:
            print(f"  ⚠️  Could not resolve test channel: {e}")
            print("     Make sure you're a member of the test channel")
            return
    else:
        client = None

    # Run tests
    if args.test:
        if 1 <= args.test <= len(tests):
            await run_test(client, tests[args.test - 1], args.test, args.dry_run)
        else:
            print(f"  ❌ Test {args.test} does not exist (1-{len(tests)} available)")
            return
    else:
        # Confirm before running all
        if not args.dry_run:
            print(f"  ⚠️  About to run ALL {len(tests)} tests against LIVE test channel.")
            print(f"     This will send real messages and may place pending orders on MT5!")
            resp = input("     Continue? (y/N): ").strip().lower()
            if resp != "y":
                print("  Aborted.")
                return
            print()

        for i, test in enumerate(tests, 1):
            if args.test and i != args.test:
                continue
            await run_test(client, test, i, args.dry_run)
            # Pause between tests so bot can clean up
            if i < len(tests):
                print(f"  --- Pause {args.pause}s before next test ---")
                await asyncio.sleep(args.pause if not args.dry_run else 0.1)

    print(f"\n{'='*70}")
    print(f"  ALL TESTS COMPLETE")
    print(f"  Check the bot console for detailed logs.")
    print(f"  Check data/entry_stats_777.json for recorded stats.")
    print(f"{'='*70}\n")

    if client:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
