"""
Deep audit of candle reachability logic.

Key question: The distribution shows 27/50 reached at minute 30 and 16 at minute 29.
This is EXTREMELY suspicious - it means 43/50 (86%) reached the zone in the last
2 minutes of the 30-minute window. That's almost certainly a bug.

Hypothesis: copy_rates_from returns candles BEFORE the given time, not AFTER.
MT5 docs: copy_rates_from(symbol, timeframe, date_from, count)
- Returns bars starting from date_from into the PAST (most recent first).
- So we're checking 30 minutes BEFORE entry, not after!

This would completely invalidate the 96.2% claim.
"""
import MetaTrader5 as mt5
from datetime import datetime

SYMBOL = "XAUUSD"

print("Initializing MT5...")
if not mt5.initialize():
    print(f"MT5 init failed: {mt5.last_error()}")
    exit(1)

# Test: fetch 5 candles from a known time
test_time = datetime(2026, 2, 27, 17, 32, 0)  # A known trade time
print(f"\nTest: copy_rates_from({SYMBOL}, M1, {test_time}, 5)")

rates = mt5.copy_rates_from(SYMBOL, mt5.TIMEFRAME_M1, test_time, 5)
if rates is not None:
    print(f"Got {len(rates)} candles:")
    for r in rates:
        dt = datetime.fromtimestamp(r[0])
        print(f"  {dt} | O={r[1]:.2f} H={r[2]:.2f} L={r[3]:.2f} C={r[4]:.2f}")
    
    # Check: are these BEFORE or AFTER test_time?
    first_candle_time = datetime.fromtimestamp(rates[0][0])
    last_candle_time = datetime.fromtimestamp(rates[-1][0])
    
    print(f"\nAsked for: {test_time}")
    print(f"First candle: {first_candle_time}")
    print(f"Last candle: {last_candle_time}")
    
    if first_candle_time >= test_time:
        print(">>> Candles are AFTER the requested time - CORRECT behavior")
    elif last_candle_time <= test_time:
        print(">>> Candles are ALL BEFORE the requested time - BUG IN ANALYSIS!")
    else:
        print(">>> Candles span the requested time")
else:
    print("No data returned")

# Also test copy_rates_from_pos which is guaranteed to work forward
print("\n\nTest: copy_rates_from_pos for forward-looking candles")
print("copy_rates_from_pos(symbol, timeframe, start_pos, count)")
print("start_pos=0 means the current/latest bar")

# Better approach: use copy_rates_range
print("\n\nTest: copy_rates_range for explicit forward window")
from_time = datetime(2026, 2, 27, 17, 32, 0)
to_time = datetime(2026, 2, 27, 18, 2, 0)  # 30 min later
rates2 = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M1, from_time, to_time)
if rates2 is not None:
    print(f"Got {len(rates2)} candles from range:")
    for r in rates2[:5]:
        dt = datetime.fromtimestamp(r[0])
        print(f"  {dt} | O={r[1]:.2f} H={r[2]:.2f} L={r[3]:.2f} C={r[4]:.2f}")
    print(f"  ... (showing first 5 of {len(rates2)})")
    first_dt = datetime.fromtimestamp(rates2[0][0])
    last_dt = datetime.fromtimestamp(rates2[-1][0])
    print(f"  First: {first_dt}")
    print(f"  Last:  {last_dt}")
else:
    print("No range data returned")

mt5.shutdown()
