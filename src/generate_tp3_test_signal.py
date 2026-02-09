#!/usr/bin/env python3
"""
Generate TP3 HIT test signals to simulate the edge case where the bot
must close ALL positions immediately when "TP3 HIT" is received,
regardless of TP/SL settings.

This tests the safety exit feature implemented for incomplete signal messages.
The traders may send partial messages with only TPs as they update during the trade,
and when "TP3 HIT" arrives, the bot should exit for safety.
"""

import argparse
import asyncio
from datetime import datetime
from typing import Optional

from telethon import TelegramClient

# Telegram config
api_id = 34597981
api_hash = "2cd59609b6cacb56da261e43fdb897ea"
TEST_CHANNEL = -1003732211798 # test channel


def build_initial_signal(side: str) -> str:
    """Build a signal without explicit TP/SL (testing edge case)."""
    side = side.upper()
    return f"""EGN GOLD
XAU USD {side} NOW 5000 - 4997
"""


def build_tp_updates(include_prices: bool = False) -> list[str]:
    """
    Build TP update messages that arrive progressively during the trade.
    
    Args:
        include_prices: If True, include prices with TP hits (realistic).
                       If False, only send "TP1 HIT", "TP2 HIT", etc (edge case).
    """
    if include_prices:
        # Realistic scenario with prices
        return [
            "EGN GOLD\nXAU USD BUY NOW 5001 - 4997\nTP1 5004\nTP2 5006\nTP3 5008",
            "TP1 HIT",
            "Close some profit\nTP1 5004 HIT",
            "TP2 HIT",
            "Secure some profit\nTP2 5006 HIT",
            "TP3 HIT",  # SAFETY EXIT - closes all positions
        ]
    else:
        # Edge case: incomplete messages with only TP hits
        return [
            "TP1 HIT",
            "TP2 HIT", 
            "TP3 HIT",  # SAFETY EXIT - closes all positions
        ]


async def send_signal_sequence(
    mode: str,
    send: bool,
    initial_delay: int = 0,
    tp_delay: int = 2,
) -> None:
    """
    Send a sequence of signals simulating the trade lifecycle.
    
    Args:
        mode: "realistic" (with prices) or "edge" (minimal messages)
        send: Whether to actually send to Telegram
        initial_delay: Delay before sending initial signal (seconds)
        tp_delay: Delay between TP updates (seconds)
    """
    if not send:
        print("\n✅ Preview mode - use --send to post signals to Telegram")
        return

    client = TelegramClient("zinra_test_session_telethon", api_id=api_id, api_hash=api_hash)

    try:
        await client.start()
        print("[✅] Telegram client connected")

        # Step 1: Send initial BUY signal (or SELL)
        print(f"\n[STEP 1] Waiting {initial_delay}s before sending initial signal...")
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)

        initial_signal = build_initial_signal("BUY")
        message = await client.send_message(TEST_CHANNEL, initial_signal)
        print(f"[✅] Initial signal sent (Message ID: {message.id})")
        print(f"   Content: {initial_signal.replace(chr(10), ' ')}")

        # Step 2: Send TP update sequence
        tp_updates = build_tp_updates(include_prices=(mode == "realistic"))
        
        print(f"\n[STEP 2] Sending {len(tp_updates)} TP updates ({mode} mode)...")
        print(f"         Waiting {tp_delay}s between each update")

        for idx, tp_msg in enumerate(tp_updates, start=1):
            await asyncio.sleep(tp_delay)
            
            update_message = await client.send_message(TEST_CHANNEL, tp_msg)
            
            # Visual feedback
            is_tp3 = "TP3 HIT" in tp_msg
            marker = "🚨 " if is_tp3 else "   "
            print(f"{marker}[Update {idx}] {tp_msg.replace(chr(10), ' ')[:60]}...")
            
            if is_tp3:
                print(f"🚨 [CRITICAL] TP3 HIT sent - Bot should close ALL positions now!")
                break

        print(f"\n[✅] Test sequence complete!")
        print(f"    Check bot logs to verify all positions were closed on TP3 HIT")

    except Exception as e:
        print(f"[❌] Error: {e}")
    finally:
        await client.disconnect()


async def send_standalone_tp3_hit(send: bool) -> None:
    """
    Send a standalone "TP3 HIT" message to test if bot closes positions
    even when TP3 HIT arrives as an isolated message.
    """
    if not send:
        print("\n✅ Preview mode - use --send to post signal to Telegram")
        return

    client = TelegramClient("zinra_test_session_telethon", api_id=api_id, api_hash=api_hash)

    try:
        await client.start()
        print("[✅] Telegram client connected")

        tp3_msg = "TP3 HIT"
        message = await client.send_message(TEST_CHANNEL, tp3_msg)
        
        print(f"[✅] Standalone TP3 HIT sent!")
        print(f"    Message ID: {message.id}")
        print(f"    Content: {tp3_msg}")
        print(f"    Bot should close ALL open positions regardless of TP/SL settings")

    except Exception as e:
        print(f"[❌] Error: {e}")
    finally:
        await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate TP3 HIT safety exit test signals.",
        epilog="""
Examples:
  # Preview realistic scenario (with prices in updates)
  python generate_tp3_test_signal.py realistic

  # Preview edge case scenario (minimal messages)
  python generate_tp3_test_signal.py edge

  # Send realistic scenario to Telegram
  python generate_tp3_test_signal.py realistic --send

  # Send standalone TP3 HIT
  python generate_tp3_test_signal.py standalone --send

  # Custom delays between messages
  python generate_tp3_test_signal.py edge --send --initial-delay 5 --tp-delay 3
        """
    )
    
    parser.add_argument(
        "mode",
        choices=["realistic", "edge", "standalone"],
        help="Test scenario type"
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send signals to Telegram test channel"
    )
    parser.add_argument(
        "--initial-delay",
        type=int,
        default=0,
        help="Delay before sending initial signal (seconds)"
    )
    parser.add_argument(
        "--tp-delay",
        type=int,
        default=2,
        help="Delay between TP updates (seconds)"
    )

    args = parser.parse_args()

    print("=" * 70)
    print("🚨 TP3 HIT SAFETY EXIT TEST SIGNAL GENERATOR 🚨".center(70))
    print("=" * 70)
    print()
    print("Purpose: Test bot's ability to close ALL positions when TP3 HIT")
    print("         is received, regardless of TP/SL settings.")
    print()
    print("=" * 70)
    print()

    if args.mode == "standalone":
        print("[MODE] Standalone TP3 HIT")
        print("       Sends only a TP3 HIT message with no initial trade signal")
        print("       Tests if bot closes positions even on isolated TP3 HIT")
        print()
        print(f"[CONFIG] Send: {args.send}")
        print()
        
        if args.send:
            print("Sending standalone TP3 HIT...")
        asyncio.run(send_standalone_tp3_hit(args.send))
    else:
        print(f"[MODE] {args.mode.title()} Scenario")
        if args.mode == "realistic":
            print("       Sends BUY signal, then TP1/TP2/TP3 HIT updates")
            print("       Simulates actual trade progression with TP prices")
        else:  # edge
            print("       Sends isolated TP1 HIT, TP2 HIT, TP3 HIT messages")
            print("       Tests edge case where signal is incomplete/fragmented")
        print()
        print(f"[CONFIG] Send: {args.send}")
        print(f"[CONFIG] Initial Delay: {args.initial_delay}s")
        print(f"[CONFIG] TP Update Delay: {args.tp_delay}s")
        print()

        print("[EXPECTED BOT BEHAVIOR]")
        print("  1. On receiving the signal, bot opens a position")
        print("  2. On TP1 HIT: Bot may optionally record or track")
        print("  3. On TP2 HIT: Bot may optionally record or track")
        print("  4. On TP3 HIT: Bot MUST close ALL positions immediately")
        print("     (Regardless of whether TP/SL were set)")
        print()

        if args.send:
            print("Sending signal sequence...")
        asyncio.run(send_signal_sequence(
            mode=args.mode,
            send=args.send,
            initial_delay=args.initial_delay,
            tp_delay=args.tp_delay,
        ))

    print()
    print("=" * 70)
    print("TEST COMPLETE".center(70))
    print("=" * 70)
    print()
    print("To verify:")
    print("  1. Check bot logs for TP3 HIT detection")
    print("  2. Verify all positions were closed with 'TP3 HIT - SAFETY EXIT'")
    print("  3. Check MT5 platform for confirmed closed positions")
    print()


if __name__ == "__main__":
    main()
