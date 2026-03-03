#!/usr/bin/env python3
"""
Signal Simulator — Test all bot code paths in minutes instead of waiting a week.

Sends realistic signal messages to your TEST Telegram channel, then edits them
with SL/TP values, mimicking exactly what the signal provider does.

HOW TO USE:
  1. Start your bot (bot_v2.py or bot_v2_second_account.py) on a DEMO MT5 account
  2. Run this script:  python tools/test_signal_simulator.py
  3. Watch the bot's logs to verify each scenario works correctly
  4. Check data/entry_stats_777.json (or 779) for recorded stats

TEST SCENARIOS:
  1. BUY signal with zone — price likely ABOVE zone → should place limit order
  2. Wait for edit with SL/TP → should update pending order or filled position
  3. SELL signal with zone — price likely BELOW zone → should place limit order
  4. Wait for edit with SL/TP
  5. Signal WITHOUT zone → should fall back to market order
  6. Signal with zone where price is IN ZONE → should do market order immediately
  7. Edge case: signal then rapid SL hit → should cancel via invalidation

Each test pauses so you can observe the bot's behavior in real-time.
"""

import asyncio
import sys
import os
import time

# Add src/ to path so we can import from it if needed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from telethon import TelegramClient

# ===================== CONFIG =====================
API_ID = 34597981
API_HASH = "2cd59609b6cacb56da261e43fdb897ea"
TEST_CHANNEL_ID = -1003817819872  # Same test channel the bot listens to
SESSION_FILE = os.path.join(os.path.dirname(__file__), "..", "src", "test_simulator_session")

# How long to wait between actions (seconds) — increase if you want more time to observe
PAUSE_SHORT = 3       # Between send and edit
PAUSE_MEDIUM = 10     # Between test scenarios
PAUSE_LONG = 20       # For scenarios that need limit order processing time


def get_xauusd_price():
    """Try to get current XAUUSD price from MT5 for realistic zones."""
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return None
        tick = mt5.symbol_info_tick("XAUUSD")
        mt5.shutdown()
        if tick:
            return float(tick.ask)
    except Exception:
        pass
    return None


async def main():
    print("=" * 70)
    print("SIGNAL SIMULATOR — Bot Integration Test")
    print("=" * 70)
    print()

    # Try to get current price for realistic signals
    price = get_xauusd_price()
    if price:
        print(f"Current XAUUSD price: ${price:.2f}")
    else:
        price = 2880.00  # Fallback
        print(f"Could not get live price — using fallback: ${price:.2f}")
    print()

    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()
    print("Telegram connected!")
    print()

    # Verify we can access the test channel
    try:
        entity = await client.get_entity(TEST_CHANNEL_ID)
        title = getattr(entity, "title", "Unknown")
        print(f"Test channel: {title} (ID: {TEST_CHANNEL_ID})")
    except Exception as e:
        print(f"ERROR: Cannot access test channel: {e}")
        print("Make sure your account is an admin of the test channel.")
        return

    print()
    print("Starting test scenarios in 5 seconds...")
    print("Watch your bot's terminal for reactions!")
    print()
    await asyncio.sleep(5)

    # Track sent messages for editing later
    sent_messages = {}

    # ══════════════════════════════════════════════════════════════════
    # TEST 1: BUY with zone — price ABOVE zone → limit order expected
    # ══════════════════════════════════════════════════════════════════
    print("=" * 70)
    print("TEST 1: BUY signal with zone (price above zone → limit order)")
    print("=" * 70)

    zone_high = round(price - 2, 2)  # Zone below current price
    zone_low = round(price - 6, 2)
    tp1 = round(price + 5, 2)
    sl = round(price - 12, 2)

    signal_text = f"""XAUUSD BUY NOW

{zone_high} - {zone_low}"""

    print(f"  Sending: {repr(signal_text)}")
    print(f"  Zone: ${zone_low} - ${zone_high} (current price ${price:.2f} is above)")
    print(f"  Expected: Bot places BUY LIMIT at ${zone_high}")
    msg = await client.send_message(TEST_CHANNEL_ID, signal_text)
    sent_messages["test1"] = msg
    print(f"  Sent! (msg_id={msg.id})")
    print()

    print(f"  Waiting {PAUSE_SHORT}s then sending edit with SL/TP...")
    await asyncio.sleep(PAUSE_SHORT)

    edit_text = f"""XAUUSD BUY NOW

{zone_high} - {zone_low}

TP1 {tp1}
TP2 {round(tp1 + 3, 2)}
TP3 {round(tp1 + 6, 2)}
SL {sl}"""

    await client.edit_message(TEST_CHANNEL_ID, msg.id, edit_text)
    print(f"  Edited msg {msg.id} with SL=${sl} TP1=${tp1}")
    print(f"  Expected: Bot updates SL/TP on pending order or filled position")
    print()

    print(f"  Waiting {PAUSE_MEDIUM}s for bot to process...")
    await asyncio.sleep(PAUSE_MEDIUM)

    # ══════════════════════════════════════════════════════════════════
    # TEST 2: SELL with zone — price BELOW zone → limit order expected
    # ══════════════════════════════════════════════════════════════════
    print("=" * 70)
    print("TEST 2: SELL signal with zone (price below zone → limit order)")
    print("=" * 70)

    sell_zone_low = round(price + 2, 2)  # Zone above current price
    sell_zone_high = round(price + 6, 2)
    sell_tp1 = round(price - 5, 2)
    sell_sl = round(price + 12, 2)

    signal_text = f"""XAUUSD SELL NOW

{sell_zone_high} - {sell_zone_low}"""

    print(f"  Sending SELL signal with zone ${sell_zone_low}-${sell_zone_high}")
    print(f"  Expected: Bot places SELL LIMIT at ${sell_zone_low}")
    msg = await client.send_message(TEST_CHANNEL_ID, signal_text)
    sent_messages["test2"] = msg
    print(f"  Sent! (msg_id={msg.id})")
    print()

    print(f"  Waiting {PAUSE_SHORT}s then editing with SL/TP...")
    await asyncio.sleep(PAUSE_SHORT)

    edit_text = f"""XAUUSD SELL NOW

{sell_zone_high} - {sell_zone_low}

TP1 {sell_tp1}
TP2 {round(sell_tp1 - 3, 2)}
TP3 {round(sell_tp1 - 6, 2)}
SL {sell_sl}"""

    await client.edit_message(TEST_CHANNEL_ID, msg.id, edit_text)
    print(f"  Edited with SL=${sell_sl} TP1=${sell_tp1}")
    print()

    print(f"  Waiting {PAUSE_MEDIUM}s for bot to process...")
    await asyncio.sleep(PAUSE_MEDIUM)

    # ══════════════════════════════════════════════════════════════════
    # TEST 3: BUY with NO zone → market order fallback
    # ══════════════════════════════════════════════════════════════════
    print("=" * 70)
    print("TEST 3: BUY signal WITHOUT zone (market order fallback)")
    print("=" * 70)

    signal_text = "XAUUSD BUY NOW"

    print(f"  Sending: {repr(signal_text)}")
    print(f"  Expected: Bot opens market order immediately (no zone to parse)")
    msg = await client.send_message(TEST_CHANNEL_ID, signal_text)
    sent_messages["test3"] = msg
    print(f"  Sent! (msg_id={msg.id})")
    print()

    tp1_t3 = round(price + 5, 2)
    sl_t3 = round(price - 8, 2)

    print(f"  Waiting {PAUSE_SHORT}s then editing with SL/TP...")
    await asyncio.sleep(PAUSE_SHORT)

    edit_text = f"""XAUUSD BUY NOW

TP1 {tp1_t3}
TP2 {round(tp1_t3 + 3, 2)}
SL {sl_t3}"""

    await client.edit_message(TEST_CHANNEL_ID, msg.id, edit_text)
    print(f"  Edited with SL=${sl_t3} TP1=${tp1_t3}")
    print()

    print(f"  Waiting {PAUSE_MEDIUM}s...")
    await asyncio.sleep(PAUSE_MEDIUM)

    # ══════════════════════════════════════════════════════════════════
    # TEST 4: BUY with zone where price is INSIDE zone → market order
    # ══════════════════════════════════════════════════════════════════
    print("=" * 70)
    print("TEST 4: BUY signal with zone where price is INSIDE → market order")
    print("=" * 70)

    # Refresh price
    fresh_price = get_xauusd_price() or price
    in_zone_high = round(fresh_price + 3, 2)
    in_zone_low = round(fresh_price - 3, 2)

    signal_text = f"""XAUUSD BUY NOW

{in_zone_high} - {in_zone_low}"""

    print(f"  Zone: ${in_zone_low} - ${in_zone_high} (price ${fresh_price:.2f} is INSIDE)")
    print(f"  Expected: Bot opens market order immediately")
    msg = await client.send_message(TEST_CHANNEL_ID, signal_text)
    sent_messages["test4"] = msg
    print(f"  Sent! (msg_id={msg.id})")
    print()

    print(f"  Waiting {PAUSE_SHORT}s then editing with SL/TP...")
    await asyncio.sleep(PAUSE_SHORT)

    tp1_t4 = round(fresh_price + 5, 2)
    sl_t4 = round(fresh_price - 8, 2)

    edit_text = f"""XAUUSD BUY NOW

{in_zone_high} - {in_zone_low}

TP1 {tp1_t4}
TP2 {round(tp1_t4 + 3, 2)}
SL {sl_t4}"""

    await client.edit_message(TEST_CHANNEL_ID, msg.id, edit_text)
    print(f"  Edited with SL=${sl_t4} TP1=${tp1_t4}")
    print()

    print(f"  Waiting {PAUSE_MEDIUM}s...")
    await asyncio.sleep(PAUSE_MEDIUM)

    # ══════════════════════════════════════════════════════════════════
    # TEST 5: Invalidation test — BUY with far zone, SL close to price
    # ══════════════════════════════════════════════════════════════════
    print("=" * 70)
    print("TEST 5: Invalidation — limit order with tight SL (should cancel)")
    print("=" * 70)

    fresh_price = get_xauusd_price() or price
    inv_zone_high = round(fresh_price - 10, 2)  # Zone far below
    inv_zone_low = round(fresh_price - 14, 2)
    inv_sl = round(fresh_price - 0.5, 2)  # SL very close — almost certainly already hit
    inv_tp1 = round(fresh_price + 10, 2)

    signal_text = f"""XAUUSD BUY NOW

{inv_zone_high} - {inv_zone_low}"""

    print(f"  Zone: ${inv_zone_low} - ${inv_zone_high} (far below price ${fresh_price:.2f})")
    print(f"  Expected: Bot places limit order")
    msg = await client.send_message(TEST_CHANNEL_ID, signal_text)
    sent_messages["test5"] = msg
    print(f"  Sent! (msg_id={msg.id})")
    print()

    print(f"  Waiting {PAUSE_SHORT}s then editing with tight SL to trigger invalidation...")
    await asyncio.sleep(PAUSE_SHORT)

    edit_text = f"""XAUUSD BUY NOW

{inv_zone_high} - {inv_zone_low}

TP1 {inv_tp1}
SL {inv_sl}"""

    await client.edit_message(TEST_CHANNEL_ID, msg.id, edit_text)
    print(f"  Edited with SL=${inv_sl} (above current price — should trigger invalidation!)")
    print(f"  Expected: limit_order_monitor detects price > SL for BUY → cancels order")
    print()

    print(f"  Waiting {PAUSE_SHORT}s to observe invalidation in bot logs...")
    await asyncio.sleep(PAUSE_SHORT)

    # ══════════════════════════════════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("ALL TESTS COMPLETE")
    print("=" * 70)
    print()
    print("Check your bot's terminal for:")
    print("  Test 1: 'LIMIT ORDER PLACED' + 'UPDATING REAL SL/TP1'")
    print("  Test 2: 'LIMIT ORDER PLACED' (SELL) + SL/TP update")
    print("  Test 3: 'POSITION OPENED' (market order, no zone)")
    print("  Test 4: 'price already in zone → market order' + 'POSITION OPENED'")
    print("  Test 5: 'LIMIT ORDER INVALIDATED' (SL hit while pending)")
    print()
    print("Also check:")
    print("  - data/entry_stats_777.json (or 779) for recorded stats")
    print("  - MT5 terminal for positions and order history")
    print()
    print("IMPORTANT: Close any test positions manually in MT5 after testing!")
    print()

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
