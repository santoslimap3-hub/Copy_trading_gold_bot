import argparse
import random
import asyncio
from datetime import datetime

import MetaTrader5 as mt5
from telethon import TelegramClient

SYMBOL = "XAUUSD"

# Telegram config
api_id = 34597981
api_hash = "2cd59609b6cacb56da261e43fdb897ea"
TEST_CHANNEL = -1003860296364  # test channel


def fmt(price: float) -> str:
    return f"{price:.2f}"


def build_signal(side: str, mid: float, range_size: float, tp_step: float, tp_count: int, sl_dist: float) -> str:
    side = side.upper()

    entry_high = mid + range_size
    entry_low = mid - range_size

    if side == "BUY":
        sl = entry_low - sl_dist
        tp_start = entry_high + tp_step
        tp_sign = 1
    else:
        sl = entry_high + sl_dist
        tp_start = entry_low - tp_step
        tp_sign = -1

    tps = [tp_start + tp_sign * tp_step * i for i in range(tp_count)]

    lines = [
        "EGN GOLD",
        f"XAU USD {side} NOW",
        "",
        f"{fmt(entry_high)} - {fmt(entry_low)}",
        "",
    ]

    for idx, tp in enumerate(tps, start=1):
        lines.append(f"TP{idx} {fmt(tp)}")

    lines.extend([
        "",
        f"SL {fmt(sl)}",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ])

    return "\n".join(lines)


async def send_to_telegram(signal_text: str, send: bool) -> None:
    """Send initial signal, then edit with full details after 5 seconds."""
    if not send:
        print("\n[INFO] Use --send to post this signal to Telegram")
        return
    
    # Use separate session to avoid lock conflicts with script.py
    client = TelegramClient("zinra_test_session", api_id, api_hash)
    
    try:
        await client.start()
        print("[INFO] Telegram client started")
        
        # Extract side from signal
        side = "BUY" if "BUY" in signal_text else "SELL"
        
        # Step 1: Send initial message
        initial_msg = f"EGN GOLD\nXAU USD {side} NOW"
        message = await client.send_message(TEST_CHANNEL, initial_msg)
        print(f"[STEP 1] Sent initial signal: {initial_msg}")
        print(f"[STEP 1] Message ID: {message.id}")
        
        # Step 2: Wait 5 seconds
        print("[STEP 2] Waiting 5 seconds before editing...")
        await asyncio.sleep(5)
        
        # Step 3: Edit message with full details
        await client.edit_message(TEST_CHANNEL, message.id, signal_text)
        print(f"[STEP 3] Message edited with full signal details")
        print(f"[SUCCESS] Signal sent and edited successfully!")
        
    except Exception as e:
        print(f"[ERROR] Failed: {e}")
    finally:
        await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a test Telegram signal message from current market price.")
    parser.add_argument("--side", choices=["buy", "sell", "random"], default="random")
    parser.add_argument("--range", type=float, default=2.50, help="Entry range half-width (price units)")
    parser.add_argument("--tp-step", type=float, default=2.00, help="TP step distance")
    parser.add_argument("--tp-count", type=int, default=5, help="Number of TP levels")
    parser.add_argument("--sl-dist", type=float, default=3.00, help="SL distance from entry range")
    parser.add_argument("--send", action="store_true", help="Send the signal to Telegram test channel")

    args = parser.parse_args()

    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")

    try:
        if not mt5.symbol_select(SYMBOL, True):
            raise SystemExit(f"Symbol not available: {SYMBOL}")

        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            raise SystemExit(f"No tick data for {SYMBOL}")

        mid = (tick.bid + tick.ask) / 2.0

        side = args.side
        if side == "random":
            side = random.choice(["buy", "sell"])

        signal_text = build_signal(
            side=side,
            mid=mid,
            range_size=args.range,
            tp_step=args.tp_step,
            tp_count=args.tp_count,
            sl_dist=args.sl_dist,
        )

        print(signal_text)
        
        # Send to Telegram if requested
        asyncio.run(send_to_telegram(signal_text, args.send))
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()

