import asyncio
import sys

import MetaTrader5 as mt5
from telethon import TelegramClient

SYMBOL = "XAUUSD"
MT5_PATH = r"C:\MTAccount2\terminal64.exe"

API_ID = 34597981
API_HASH = "2cd59609b6cacb56da261e43fdb897ea"
SESSION_FILE = "send_test_signal_session"
CHANNEL_ID = -1003817819872


def get_gold_price() -> float:
    if not mt5.initialize(path=MT5_PATH):
        print(f"MT5 initialization failed: {mt5.last_error()}")
        mt5.shutdown()
        sys.exit(1)

    tick = mt5.symbol_info_tick(SYMBOL)
    mt5.shutdown()

    if tick is None:
        print(f"Failed to get tick for {SYMBOL}")
        sys.exit(1)

    return (tick.bid + tick.ask) / 2

def round_to_half(x):
    return round(x * 2) / 2

async def main():
    price = get_gold_price()

    zone = [round_to_half(price - 2), round_to_half(price + 2)]
    sl = round_to_half(zone[0] - 3)
    tp1 = round_to_half(zone[1] + 1)
    tp2 = round_to_half(tp1 + 1)
    tp3 = round_to_half(tp2 + 1)
    tp4 = round_to_half(tp3 + 1)
    tp5 = round_to_half(tp4 + 2)

    async with TelegramClient(SESSION_FILE, API_ID, API_HASH) as client:
        sent = await client.send_message(CHANNEL_ID, "XAUUSD BUY NOW")
        await asyncio.sleep(5)
        await client.edit_message(CHANNEL_ID, sent.id, f"XAUUSD BUY NOW {zone[0]}-{zone[1]}")
        await asyncio.sleep(5)
        await client.edit_message(CHANNEL_ID, sent.id, f"XAUUSD BUY NOW {zone[0]}-{zone[1]}\n\ntp1 {tp1}\ntp2 {tp2}\ntp3 {tp3}\ntp4 {tp4}\ntp5 {tp5}\nsl {sl}")


asyncio.run(main())
