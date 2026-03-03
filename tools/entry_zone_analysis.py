#!/usr/bin/env python3
"""
Entry Zone Retroactive Analysis Tool
=====================================

Scrapes Telegram channel history, extracts entry zones from signals,
matches them to actual bot trades from MT5, and calculates:
  - How often the bot's fill price was inside vs outside the entry zone
  - Average slippage (distance between fill and nearest zone boundary)
  - Whether price ever reached the zone after the signal (using MT5 tick/candle data)

Usage:
    python tools/entry_zone_analysis.py

Outputs:
    data/entry_zone_analysis.json   — full structured results
    Console report with statistics
"""

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

# Add src/ to path so we can use existing modules
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
sys.path.insert(0, SRC_DIR)

from telethon import TelegramClient

# Try to import MT5 for candle data (optional — analysis works without it)
try:
    import MetaTrader5 as mt5
    HAS_MT5 = True
except ImportError:
    HAS_MT5 = False

# ===================== CONFIGURATION =====================
API_ID = 34597981
API_HASH = "2cd59609b6cacb56da261e43fdb897ea"
CHANNEL_ID = -1003349563414  # Main trading channel
TEST_CHANNEL_ID = -1003817819872  # Test channel
ALL_CHANNEL_IDS = [CHANNEL_ID, TEST_CHANNEL_ID]
SESSION_FILE = os.path.join(SRC_DIR, "trading_bot_session_account_1")

SYMBOL = "XAUUSD"
BOT_MAGIC_NUMBERS = [777, 779]

BOT_TRADES_FILE = os.path.join(PROJECT_ROOT, "data", "bot_trades.json")
OUTPUT_FILE = os.path.join(PROJECT_ROOT, "data", "entry_zone_analysis.json")

# How many seconds tolerance when matching a signal to a trade
MATCH_WINDOW_SECONDS = 600  # 10 minutes (accounts for signal delay + execution)

# How many minutes of candle data to check after entry
POST_ENTRY_CANDLE_MINUTES = 30

# Telegram returns UTC timestamps; MT5 may use server time.
# Set this to the offset in hours: MT5_time = UTC + UTC_OFFSET_HOURS
# Common: UTC+2 (EET) or UTC+3 (EEST) for most forex brokers.
# Set to 0 if your MT5 timestamps are already UTC.
# Set to None to auto-detect from the first matched signal.
UTC_OFFSET_HOURS = None  # Will auto-detect

# Maximum messages to fetch from each channel
MAX_MESSAGES_PER_CHANNEL = 10000

# ===================== REGEX PATTERNS =====================

# Matches "XAUUSD BUY NOW" or "XAU USD BUY NOW" (with optional space/newline)
RE_SIGNAL = re.compile(r"(?:XAUUSD|XAU\s*USD)\s+(BUY|SELL)\s+NOW", re.IGNORECASE)

# Matches entry zone like "5396 - 5392" or "5396-5392" or "5396 -5392"
# Typically appears on its own line after the BUY/SELL NOW line
RE_ENTRY_ZONE = re.compile(
    r"(\d{4,5}(?:\.\d{1,2})?)\s*[-–—]\s*(\d{4,5}(?:\.\d{1,2})?)",
    re.IGNORECASE
)

# TP and SL patterns (same as bot)
RE_TP_LEVELS = re.compile(r"TP\s*(\d+)\s+([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
RE_STOP_LOSS = re.compile(r"SL\s+([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


# ===================== SIGNAL PARSER =====================

def parse_signal_message(text: str) -> Optional[Dict]:
    """
    Parse a full signal message (after edit).
    Returns dict with: side, zone_high, zone_low, tp_levels, sl, or None.
    """
    if not text:
        return None

    # Must contain a BUY/SELL signal
    signal_match = RE_SIGNAL.search(text)
    if not signal_match:
        return None

    side = signal_match.group(1).upper()

    result = {
        "side": side,
        "zone_high": None,
        "zone_low": None,
        "tp_levels": {},
        "sl": None,
        "raw_text": text[:300],
    }

    # Parse entry zone
    zone_match = RE_ENTRY_ZONE.search(text)
    if zone_match:
        val1 = float(zone_match.group(1))
        val2 = float(zone_match.group(2))
        # Both must be plausible gold prices (> 1000)
        if val1 >= 1000 and val2 >= 1000:
            zh = max(val1, val2)
            zl = min(val1, val2)
            zone_width = zh - zl
            # Sanity: typical entry zones are $2-$10 wide. Reject absurd zones.
            if zone_width <= 30:
                result["zone_high"] = zh
                result["zone_low"] = zl
            else:
                # Likely a mis-parse (e.g. "4930 - 4034" typo in original signal)
                result["zone_parse_rejected"] = True
                result["zone_rejected_reason"] = f"Width ${zone_width:.0f} exceeds $30 max"

    # Parse TP levels
    for tp_match in RE_TP_LEVELS.finditer(text):
        tp_num = int(tp_match.group(1))
        tp_price = float(tp_match.group(2))
        if tp_price >= 1000:
            result["tp_levels"][tp_num] = tp_price

    # Parse SL
    sl_match = RE_STOP_LOSS.search(text)
    if sl_match:
        sl_price = float(sl_match.group(1))
        if sl_price >= 1000:
            result["sl"] = sl_price

    return result


# ===================== LOAD BOT TRADES =====================

def load_bot_trades() -> Tuple[List[Dict], List[Dict]]:
    """Load entry deals and closed deals from bot_trades.json"""
    if not os.path.exists(BOT_TRADES_FILE):
        print(f"ERROR: {BOT_TRADES_FILE} not found. Run bot_trades_exporter.py first.")
        sys.exit(1)

    with open(BOT_TRADES_FILE, "r") as f:
        data = json.load(f)

    all_deals = data.get("deals", [])
    closed_deals = data.get("trades", [])

    # Entry deals have entry == 0 (DEAL_ENTRY_IN)
    entry_deals = [d for d in all_deals if d.get("entry") == 0]
    entry_deals.sort(key=lambda d: d["time"])

    print(f"Loaded {len(entry_deals)} entry deals and {len(closed_deals)} closed deals")
    return entry_deals, closed_deals


# ===================== BUILD TRADE LOOKUP =====================

def build_trade_lookup(entry_deals: List[Dict], closed_deals: List[Dict]) -> List[Dict]:
    """
    Build a list of enriched trade records with:
      - entry_time, entry_price, side, ticket, position_id
      - close_price, profit (from closed deals)
    """
    # Build a position_id -> closed deal map
    close_map = {}
    for d in closed_deals:
        pid = d.get("position_id")
        if pid:
            close_map[pid] = d

    trades = []
    for d in entry_deals:
        if d.get("magic") not in BOT_MAGIC_NUMBERS:
            continue

        pid = d.get("position_id")
        close_deal = close_map.get(pid, {})

        trade = {
            "ticket": d["ticket"],
            "position_id": pid,
            "entry_time": d["time"],
            "entry_time_str": d["time_str"],
            "entry_price": d["price"],
            "side": d["type"],  # "BUY" or "SELL"
            "magic": d["magic"],
            "volume": d["volume"],
            "close_price": close_deal.get("price"),
            "close_profit": close_deal.get("profit"),
            "close_comment": close_deal.get("comment", ""),
        }
        trades.append(trade)

    trades.sort(key=lambda t: t["entry_time"])
    print(f"Built {len(trades)} enriched bot trade records")
    return trades


# ===================== TELEGRAM SCRAPER =====================

async def fetch_channel_signals() -> List[Dict]:
    """
    Fetch messages from all trading channels.
    Returns list of parsed signal dicts with timestamps.
    """
    print(f"\nConnecting to Telegram (session: {SESSION_FILE})...")
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()
    print("Telegram connected!")

    signals = []
    total_msg_count = 0
    total_signal_count = 0

    for channel_id in ALL_CHANNEL_IDS:
        try:
            entity = await client.get_entity(channel_id)
            title = getattr(entity, "title", str(channel_id))
            print(f"\nFetching from: {title} (ID: {channel_id})...")
        except Exception as e:
            print(f"Warning: Could not access channel {channel_id}: {e}")
            continue

        msg_count = 0
        signal_count = 0

        async for message in client.iter_messages(channel_id, limit=MAX_MESSAGES_PER_CHANNEL):
            msg_count += 1
            text = message.raw_text or message.message or ""
            if not text:
                continue

            parsed = parse_signal_message(text)
            if parsed is None:
                continue

            signal_count += 1
            msg_time = message.date.timestamp()  # UTC

            signals.append({
                "msg_id": message.id,
                "channel_id": channel_id,
                "msg_time_utc": msg_time,
                "msg_time_str": message.date.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "edited": message.edit_date is not None,
                "edit_time_str": message.edit_date.strftime("%Y-%m-%d %H:%M:%S UTC") if message.edit_date else None,
                **parsed,
            })

        print(f"  Scanned {msg_count} messages, found {signal_count} signals")
        total_msg_count += msg_count
        total_signal_count += signal_count

    await client.disconnect()

    print(f"\nTotal: {total_msg_count} messages scanned, {total_signal_count} signals found")
    with_zone = sum(1 for s in signals if s["zone_high"] is not None)
    without_zone = total_signal_count - with_zone
    print(f"  With entry zone: {with_zone}")
    print(f"  Without entry zone: {without_zone}")

    # Oldest first
    signals.sort(key=lambda s: s["msg_time_utc"])
    return signals


# ===================== MATCH SIGNALS TO TRADES =====================

def auto_detect_utc_offset(signals: List[Dict], trades: List[Dict]) -> float:
    """
    Try multiple UTC offsets and find the one that produces the most matches.
    Returns the best offset in seconds.
    """
    print("\nAuto-detecting UTC offset between Telegram and MT5...")
    print("  Formula: adjusted_signal_time = Telegram_UTC + offset  →  should ≈ MT5_time")
    best_offset = 0
    best_matches = 0
    all_results = []

    # Test offsets from -12h to +12h in 1-hour steps (covers ALL timezones)
    for offset_hours in range(-12, 13):
        offset_seconds = offset_hours * 3600
        match_count = 0

        for trade in trades:
            entry_time = trade["entry_time"]
            for signal in signals:
                # Apply offset: MT5_time = UTC_time + offset
                adjusted_signal_time = signal["msg_time_utc"] + offset_seconds
                delta = abs(entry_time - adjusted_signal_time)
                if delta <= 180:  # tight 3-min window for detection
                    if signal["side"] == trade["side"]:
                        match_count += 1
                        break

        all_results.append((offset_hours, match_count))
        if match_count > best_matches:
            best_matches = match_count
            best_offset = offset_seconds

    # Show all non-zero offsets for transparency
    print("  Offset scan results (matches at 3min window):")
    for oh, mc in all_results:
        marker = " <<<< BEST" if oh == best_offset / 3600 else ""
        if mc > 0:
            print(f"    UTC{'+' if oh >= 0 else ''}{oh:3d}h → {mc:3d} matches{marker}")

    offset_hours = best_offset / 3600
    print(f"\n  Best offset: UTC{'+' if offset_hours >= 0 else ''}{offset_hours:.0f}h ({best_matches} matches)")

    # Show sample time comparisons for verification
    print(f"\n  Sample time comparisons (first 5 matched pairs at best offset):")
    print(f"  {'Telegram (UTC)':>25s}  {'TG + offset':>25s}  {'MT5 raw as UTC':>25s}  {'delta':>6s}  {'MT5 display (local)':>25s}")
    sample_count = 0
    for trade in trades:
        if sample_count >= 5:
            break
        entry_time = trade["entry_time"]
        for signal in signals:
            adjusted = signal["msg_time_utc"] + best_offset
            delta = abs(entry_time - adjusted)
            if delta <= 180 and signal["side"] == trade["side"]:
                from datetime import datetime as _dt, timezone as _tz
                tg_str = _dt.fromtimestamp(signal["msg_time_utc"], tz=_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
                adj_str = _dt.fromtimestamp(adjusted, tz=_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
                mt5_raw_str = _dt.fromtimestamp(entry_time, tz=_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
                mt5_local_str = trade["entry_time_str"]
                print(f"    {tg_str}  {adj_str}  {mt5_raw_str}  Δ{delta:.0f}s  {mt5_local_str}")
                sample_count += 1
                break

    offset_h = best_offset / 3600
    print(f"\n  NOTE: MT5 raw timestamps are in broker server time (UTC{'+' if offset_h >= 0 else ''}{offset_h:.0f}),")
    print(f"        while 'MT5 display (local)' shows your PC's local timezone.")
    print(f"        The 6h visual gap you see between Telegram app & MT5 terminal")
    print(f"        = your local timezone - broker server timezone. Already accounted for.")

    return best_offset


def match_signals_to_trades(
    signals: List[Dict], trades: List[Dict], utc_offset_seconds: float = 0
) -> List[Dict]:
    """
    Match each bot trade to the closest signal by timestamp.
    utc_offset_seconds: added to Telegram UTC time to get MT5 time.
    Returns a list of matched records.
    """
    print(f"\nMatching {len(trades)} trades to {len(signals)} signals...")
    print(f"  UTC offset applied: {utc_offset_seconds/3600:.0f}h")
    print(f"  Match window: {MATCH_WINDOW_SECONDS}s")

    matched = []
    unmatched_trades = []

    for trade in trades:
        entry_time = trade["entry_time"]
        best_signal = None
        best_delta = float("inf")

        for signal in signals:
            adjusted_time = signal["msg_time_utc"] + utc_offset_seconds
            delta = abs(entry_time - adjusted_time)
            if delta < best_delta:
                best_delta = delta
                best_signal = signal

        if best_signal and best_delta <= MATCH_WINDOW_SECONDS:
            # Verify direction matches
            if best_signal["side"] == trade["side"]:
                record = {
                    **trade,
                    "signal": best_signal,
                    "match_delta_seconds": round(best_delta, 1),
                }
                matched.append(record)
            else:
                # Direction mismatch — still record it but flag
                record = {
                    **trade,
                    "signal": best_signal,
                    "match_delta_seconds": round(best_delta, 1),
                    "direction_mismatch": True,
                }
                matched.append(record)
        else:
            unmatched_trades.append(trade)

    print(f"  Matched: {len(matched)} | Unmatched: {len(unmatched_trades)}")
    if unmatched_trades:
        print(f"  Unmatched trades (no signal within {MATCH_WINDOW_SECONDS}s):")
        for t in unmatched_trades[:10]:
            print(f"    {t['entry_time_str']} | {t['side']} | ${t['entry_price']}")
        if len(unmatched_trades) > 10:
            print(f"    ... and {len(unmatched_trades) - 10} more")

    return matched


# ===================== ANALYZE ENTRY ZONES =====================

def analyze_entry_zones(matched: List[Dict], total_bot_trades: int = 0) -> Dict:
    """
    For each matched trade, determine:
      - Was the fill price inside the entry zone?
      - How far outside (slippage)?
      - Direction of slippage (worse or better than zone?)
    """
    print(f"\nAnalyzing {len(matched)} matched trades...")

    results = []
    stats = {
        "total_matched": len(matched),
        "has_zone": 0,
        "no_zone": 0,
        "inside_zone": 0,
        "outside_zone": 0,
        "outside_above_zone": 0,  # filled above zone (bad for BUY)
        "outside_below_zone": 0,  # filled below zone (bad for SELL)
        "slippage_values": [],    # dollars of slippage (positive = worse entry)
        "direction_mismatches": 0,
    }

    for record in matched:
        signal = record["signal"]
        entry_price = record["entry_price"]
        side = record["side"]

        analysis = {
            "ticket": record["ticket"],
            "entry_time": record["entry_time_str"],
            "side": side,
            "entry_price": entry_price,
            "zone_high": signal.get("zone_high"),
            "zone_low": signal.get("zone_low"),
            "tp1": signal.get("tp_levels", {}).get(1),
            "sl": signal.get("sl"),
            "match_delta_s": record["match_delta_seconds"],
            "close_profit": record.get("close_profit"),
            "close_comment": record.get("close_comment", ""),
            "msg_id": signal.get("msg_id"),
            "signal_text_preview": signal.get("raw_text", "")[:100],
        }

        if record.get("direction_mismatch"):
            stats["direction_mismatches"] += 1
            analysis["direction_mismatch"] = True
            analysis["inside_zone"] = None
            analysis["slippage"] = None
            results.append(analysis)
            continue

        zone_high = signal.get("zone_high")
        zone_low = signal.get("zone_low")

        if zone_high is None or zone_low is None:
            stats["no_zone"] += 1
            analysis["inside_zone"] = None
            analysis["slippage"] = None
            analysis["note"] = "No entry zone in signal"
            results.append(analysis)
            continue

        stats["has_zone"] += 1

        # Check if fill was inside the zone
        if zone_low <= entry_price <= zone_high:
            analysis["inside_zone"] = True
            analysis["slippage"] = 0.0
            stats["inside_zone"] += 1
        else:
            analysis["inside_zone"] = False
            stats["outside_zone"] += 1

            if entry_price > zone_high:
                # Filled above zone
                slippage = entry_price - zone_high
                stats["outside_above_zone"] += 1
                analysis["slippage_direction"] = "above_zone"

                if side == "BUY":
                    analysis["slippage_quality"] = "WORSE"  # paid more
                else:
                    analysis["slippage_quality"] = "BETTER"  # sold higher  
            else:
                # Filled below zone
                slippage = zone_low - entry_price
                stats["outside_below_zone"] += 1
                analysis["slippage_direction"] = "below_zone"

                if side == "SELL":
                    analysis["slippage_quality"] = "WORSE"  # sold lower
                else:
                    analysis["slippage_quality"] = "BETTER"  # bought lower

            analysis["slippage"] = round(slippage, 2)
            stats["slippage_values"].append(slippage)

        results.append(analysis)

    # Calculate aggregate slippage stats
    slippages = stats["slippage_values"]
    if slippages:
        stats["avg_slippage"] = round(sum(slippages) / len(slippages), 2)
        stats["max_slippage"] = round(max(slippages), 2)
        stats["min_slippage"] = round(min(slippages), 2)
        stats["median_slippage"] = round(sorted(slippages)[len(slippages) // 2], 2)
    else:
        stats["avg_slippage"] = 0
        stats["max_slippage"] = 0
        stats["min_slippage"] = 0
        stats["median_slippage"] = 0

    if stats["has_zone"] > 0:
        stats["inside_zone_pct"] = round(stats["inside_zone"] / stats["has_zone"] * 100, 1)
        stats["outside_zone_pct"] = round(stats["outside_zone"] / stats["has_zone"] * 100, 1)
    else:
        stats["inside_zone_pct"] = 0
        stats["outside_zone_pct"] = 0

    # Analyze: among outside-zone trades, how many were worse vs better entries?
    worse_entries = [r for r in results if r.get("slippage_quality") == "WORSE"]
    better_entries = [r for r in results if r.get("slippage_quality") == "BETTER"]
    stats["worse_entry_count"] = len(worse_entries)
    stats["better_entry_count"] = len(better_entries)
    if worse_entries:
        stats["avg_worse_slippage"] = round(
            sum(r["slippage"] for r in worse_entries) / len(worse_entries), 2
        )
    else:
        stats["avg_worse_slippage"] = 0

    # PnL comparison: inside-zone trades vs outside-zone trades
    inside_profits = [r["close_profit"] for r in results if r.get("inside_zone") is True and r.get("close_profit") is not None]
    outside_profits = [r["close_profit"] for r in results if r.get("inside_zone") is False and r.get("close_profit") is not None]

    if inside_profits:
        stats["inside_zone_avg_profit"] = round(sum(inside_profits) / len(inside_profits), 2)
        stats["inside_zone_total_profit"] = round(sum(inside_profits), 2)
        stats["inside_zone_win_rate"] = round(sum(1 for p in inside_profits if p > 0) / len(inside_profits) * 100, 1)
    else:
        stats["inside_zone_avg_profit"] = 0
        stats["inside_zone_total_profit"] = 0
        stats["inside_zone_win_rate"] = 0

    if outside_profits:
        stats["outside_zone_avg_profit"] = round(sum(outside_profits) / len(outside_profits), 2)
        stats["outside_zone_total_profit"] = round(sum(outside_profits), 2)
        stats["outside_zone_win_rate"] = round(sum(1 for p in outside_profits if p > 0) / len(outside_profits) * 100, 1)
    else:
        stats["outside_zone_avg_profit"] = 0
        stats["outside_zone_total_profit"] = 0
        stats["outside_zone_win_rate"] = 0

    # Remove the raw list from stats (not JSON-friendly for printing)
    del stats["slippage_values"]

    # Track aggregate data quality counters
    stats["total_bot_trades"] = total_bot_trades

    # Count duplicate signal matches (one signal -> multiple trades)
    msg_ids = [r.get("msg_id") for r in results if r.get("msg_id")]
    dup_ids = set(m for m in msg_ids if msg_ids.count(m) > 1)
    stats["duplicate_signal_matches"] = len(dup_ids)

    # Count suspicious matches (>120s delta)
    stats["suspicious_matches"] = sum(
        1 for r in results if r.get("match_delta_s", 0) > 120
    )

    return {"stats": stats, "trades": results}


# ===================== MT5 CANDLE DATA (OPTIONAL) =====================

def check_price_reached_zone_mt5(matched_with_zones: List[Dict]) -> List[Dict]:
    """
    For each trade where we entered OUTSIDE the zone, check if price
    ever reached the zone in the next N minutes using MT5 1-min candles.
    
    Requires MT5 to be initialized.
    """
    if not HAS_MT5:
        print("\nMT5 not available — skipping candle reachability analysis")
        return matched_with_zones

    print(f"\nInitializing MT5 for candle data analysis...")
    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()} — skipping candle analysis")
        return matched_with_zones

    print("MT5 initialized - checking if price reached zone after each trade...")

    enriched = 0
    for record in matched_with_zones:
        if record.get("inside_zone") is not False:
            continue  # only check outside-zone trades

        zone_high = record.get("zone_high")
        zone_low = record.get("zone_low")
        if zone_high is None or zone_low is None:
            continue

        entry_time = record.get("entry_time")  # this is the entry_time_str
        # We need the unix timestamp — find it from the parent data
        # Use the entry_time_str to create a datetime
        try:
            # Parse ISO format
            if "T" in str(entry_time):
                dt = datetime.fromisoformat(str(entry_time))
            else:
                dt = datetime.fromtimestamp(float(entry_time))
        except (ValueError, TypeError):
            continue

        # Fetch 1-minute candles starting from trade entry for N minutes
        rates = mt5.copy_rates_from(SYMBOL, mt5.TIMEFRAME_M1, dt, POST_ENTRY_CANDLE_MINUTES)

        if rates is None or len(rates) == 0:
            record["zone_reached_after_entry"] = None
            record["zone_reach_note"] = "No candle data available"
            continue

        enriched += 1
        side = record.get("side", "BUY")
        zone_reached = False
        minutes_to_reach = None

        for i, rate in enumerate(rates):
            candle_low = float(rate[3])   # low
            candle_high = float(rate[2])   # high

            # Check if the candle overlaps the zone
            if candle_low <= zone_high and candle_high >= zone_low:
                zone_reached = True
                minutes_to_reach = i + 1
                break

        record["zone_reached_after_entry"] = zone_reached
        if zone_reached:
            record["minutes_to_reach_zone"] = minutes_to_reach

    mt5.shutdown()
    print(f"Candle analysis done for {enriched} outside-zone trades")
    return matched_with_zones


# ===================== REPORT =====================

def print_report(analysis: Dict):
    """Print a human-readable analysis report to console."""
    stats = analysis["stats"]
    trades = analysis["trades"]

    print("\n" + "=" * 70)
    print("  ENTRY ZONE ANALYSIS REPORT")
    print("=" * 70)

    print(f"\n  Total matched trades:       {stats['total_matched']}")
    print(f"  Trades with entry zone:     {stats['has_zone']}")
    print(f"  Trades without entry zone:  {stats['no_zone']}")
    if stats["direction_mismatches"] > 0:
        print(f"  Direction mismatches:       {stats['direction_mismatches']}")

    print(f"\n  --- ZONE FILL ANALYSIS (n={stats['has_zone']}) ---")
    print(f"  Filled INSIDE zone:    {stats['inside_zone']:>3d}  ({stats['inside_zone_pct']:.1f}%)")
    print(f"  Filled OUTSIDE zone:   {stats['outside_zone']:>3d}  ({stats['outside_zone_pct']:.1f}%)")
    print(f"    - Above zone:        {stats['outside_above_zone']:>3d}")
    print(f"    - Below zone:        {stats['outside_below_zone']:>3d}")

    print(f"\n  --- SLIPPAGE (outside-zone trades only) ---")
    print(f"  Avg slippage:          ${stats['avg_slippage']:.2f}")
    print(f"  Max slippage:          ${stats['max_slippage']:.2f}")
    print(f"  Min slippage:          ${stats['min_slippage']:.2f}")
    print(f"  Median slippage:       ${stats['median_slippage']:.2f}")
    print(f"  Worse entry (vs zone): {stats['worse_entry_count']} trades (avg ${stats['avg_worse_slippage']:.2f})")
    print(f"  Better entry (vs zone):{stats['better_entry_count']} trades")

    print(f"\n  --- P&L COMPARISON ---")
    print(f"  Inside-zone trades:")
    print(f"    Avg profit:  ${stats['inside_zone_avg_profit']:.2f}")
    print(f"    Total profit:${stats['inside_zone_total_profit']:.2f}")
    print(f"    Win rate:    {stats['inside_zone_win_rate']:.1f}%")
    print(f"  Outside-zone trades:")
    print(f"    Avg profit:  ${stats['outside_zone_avg_profit']:.2f}")
    print(f"    Total profit:${stats['outside_zone_total_profit']:.2f}")
    print(f"    Win rate:    {stats['outside_zone_win_rate']:.1f}%")

    # Check for zone reachability data
    outside_with_candle = [
        t for t in trades
        if t.get("inside_zone") is False and t.get("zone_reached_after_entry") is not None
    ]
    if outside_with_candle:
        reached = sum(1 for t in outside_with_candle if t["zone_reached_after_entry"])
        not_reached = len(outside_with_candle) - reached
        pct_reached = reached / len(outside_with_candle) * 100 if outside_with_candle else 0

        print(f"\n  --- POST-ENTRY ZONE REACHABILITY ({POST_ENTRY_CANDLE_MINUTES}min window) ---")
        print(f"  Price reached zone:    {reached:>3d}  ({pct_reached:.1f}%)")
        print(f"  Price never reached:   {not_reached:>3d}  ({100 - pct_reached:.1f}%)")

        if reached > 0:
            reach_times = [t["minutes_to_reach_zone"] for t in outside_with_candle if t.get("minutes_to_reach_zone")]
            if reach_times:
                print(f"  Avg time to reach zone: {sum(reach_times)/len(reach_times):.1f} minutes")
                print(f"  Fastest:               {min(reach_times)} min")
                print(f"  Slowest:               {max(reach_times)} min")

    # Print per-trade detail table
    print(f"\n  --- PER-TRADE DETAILS ---")
    print(f"  {'Time':<20s} {'Side':<5s} {'Fill':>10s} {'Zone':>21s} {'In?':>5s} {'Slip':>8s} {'Quality':>8s} {'P&L':>8s} {'Zone?':>6s}")
    print(f"  {'-'*20} {'-'*5} {'-'*10} {'-'*21} {'-'*5} {'-'*8} {'-'*8} {'-'*8} {'-'*6}")

    for t in trades:
        time_str = t["entry_time"][:16] if t.get("entry_time") else "?"
        side = t.get("side", "?")
        fill = f"${t['entry_price']:.2f}" if t.get("entry_price") else "?"
        zh = t.get("zone_high")
        zl = t.get("zone_low")
        zone_str = f"${zl:.0f} - ${zh:.0f}" if zh and zl else "N/A"

        inside = t.get("inside_zone")
        if inside is True:
            in_str = "YES"
        elif inside is False:
            in_str = "NO"
        else:
            in_str = "?"

        slip = f"${t['slippage']:.2f}" if t.get("slippage") is not None and t["slippage"] > 0 else "-"
        quality = t.get("slippage_quality", "-")
        pnl = f"${t['close_profit']:.2f}" if t.get("close_profit") is not None else "?"

        reached = t.get("zone_reached_after_entry")
        if reached is True:
            reach_str = f"{t.get('minutes_to_reach_zone','?')}m"
        elif reached is False:
            reach_str = "NEVER"
        else:
            reach_str = "-"

        print(f"  {time_str:<20s} {side:<5s} {fill:>10s} {zone_str:>21s} {in_str:>5s} {slip:>8s} {quality:>8s} {pnl:>8s} {reach_str:>6s}")

    # Data quality warnings
    print(f"\n  --- DATA QUALITY NOTES ---")
    total_bot_trades = stats.get("total_bot_trades", "?")
    print(f"  Total bot trades in MT5: {total_bot_trades}")
    print(f"  Matched to Telegram signals: {stats['total_matched']}")
    print(f"  Had parseable entry zone: {stats['has_zone']}")
    
    dup_count = stats.get("duplicate_signal_matches", 0)
    if dup_count:
        print(f"  WARNING: {dup_count} signals matched to >1 trade (rapid re-entries)")
    
    mismatch_count = stats.get("direction_mismatches", 0)
    if mismatch_count:
        print(f"  WARNING: {mismatch_count} direction mismatches (excluded from zone stats)")

    reject_count = stats.get("zone_parse_rejected", 0)
    if reject_count:
        print(f"  WARNING: {reject_count} zones rejected (width >$30, likely typo in signal)")
    
    suspicious_count = stats.get("suspicious_matches", 0)
    if suspicious_count:
        print(f"  WARNING: {suspicious_count} matches with >120s delta (may be wrong match)")
    
    no_candle = stats.get("no_candle_data", 0)
    if no_candle:
        print(f"  WARNING: {no_candle} trades had no MT5 candle data (too old for history?)")

    print(f"\n  Candle data: Checked FORWARD from entry using copy_rates_range()")
    print(f"  Entry candle (minute 0) excluded to avoid false positives")

    print("\n" + "=" * 70)


def save_results(analysis: Dict):
    """Save full analysis results to JSON."""
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    output = {
        "metadata": {
            "created_at": datetime.now().isoformat(),
            "channel_id": CHANNEL_ID,
            "magic_numbers": BOT_MAGIC_NUMBERS,
            "match_window_seconds": MATCH_WINDOW_SECONDS,
            "post_entry_candle_minutes": POST_ENTRY_CANDLE_MINUTES,
        },
        "stats": analysis["stats"],
        "trades": analysis["trades"],
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nResults saved to: {OUTPUT_FILE}")


# ===================== MAIN =====================

async def async_main():
    print("=" * 70)
    print("  ENTRY ZONE RETROACTIVE ANALYSIS")
    print("=" * 70)

    # Step 1: Load bot trades from JSON
    print("\n[1/5] Loading bot trades from MT5 export...")
    entry_deals, closed_deals = load_bot_trades()
    trades = build_trade_lookup(entry_deals, closed_deals)

    if not trades:
        print("No bot trades found. Exiting.")
        return

    # Step 2: Fetch signals from Telegram
    print("\n[2/5] Fetching signals from Telegram channel history...")
    signals = await fetch_channel_signals()

    if not signals:
        print("No signals found in channel. Exiting.")
        return

    # Step 3: Auto-detect timezone offset and match signals to trades
    print("\n[3/5] Matching signals to trades...")

    # Auto-detect or use configured UTC offset
    if UTC_OFFSET_HOURS is None:
        utc_offset_seconds = auto_detect_utc_offset(signals, trades)
    else:
        utc_offset_seconds = UTC_OFFSET_HOURS * 3600
        print(f"  Using configured UTC offset: {UTC_OFFSET_HOURS}h")

    matched = match_signals_to_trades(signals, trades, utc_offset_seconds)

    if not matched:
        print("No signal-trade matches found. Exiting.")
        return

    # Step 4: Analyze entry zones
    print("\n[4/5] Analyzing entry zones...")
    analysis = analyze_entry_zones(matched, total_bot_trades=len(trades))

    # Step 4b: Check if price reached zone after entry (MT5 candle data)
    # Only for outside-zone trades
    if HAS_MT5:
        outside_zone_trades = [t for t in analysis["trades"] if t.get("inside_zone") is False]
        if outside_zone_trades:
            # Create records with proper unix timestamps for candle lookup
            for t in analysis["trades"]:
                if t.get("inside_zone") is False:
                    # Find matching entry deal for unix timestamp
                    for deal in entry_deals:
                        if deal["ticket"] == t["ticket"]:
                            t["_entry_unix"] = deal["time"]
                            break

            # Enrich with candle data
            for t in analysis["trades"]:
                if t.get("inside_zone") is False and t.get("_entry_unix"):
                    t["entry_time"] = t["entry_time"]  # keep string
                    # We'll use _entry_unix in the candle check
            
            analysis["trades"] = check_price_reached_zone_mt5_v2(
                analysis["trades"], entry_deals
            )

    # Step 5: Report
    print("\n[5/5] Generating report...")
    print_report(analysis)
    save_results(analysis)


def check_price_reached_zone_mt5_v2(trade_results: List[Dict], entry_deals: List[Dict]) -> List[Dict]:
    """
    For trades outside the zone, check if price ever reached the zone
    in the N minutes AFTER entry using MT5 1-minute candle data.

    IMPORTANT: Uses copy_rates_range(from_time, to_time) which correctly
    returns candles FORWARD from from_time to to_time.
    (copy_rates_from returns candles BACKWARDS and was a bug in v1.)

    Skips the first candle (index 0) because that's the entry candle itself —
    the entry candle's range may touch the zone via wicks even though the
    fill price was outside it.
    """
    if not HAS_MT5:
        return trade_results

    print(f"\nInitializing MT5 for candle data analysis...")
    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()} — skipping candle analysis")
        return trade_results

    print("MT5 initialized - checking post-entry price action (FORWARD)...")

    # Build ticket -> unix time map
    ticket_time_map = {d["ticket"]: d["time"] for d in entry_deals}

    checked = 0
    no_data_count = 0
    for record in trade_results:
        if record.get("inside_zone") is not False:
            continue

        zone_high = record.get("zone_high")
        zone_low = record.get("zone_low")
        if zone_high is None or zone_low is None:
            continue

        unix_time = ticket_time_map.get(record["ticket"])
        if unix_time is None:
            continue

        dt_from = datetime.fromtimestamp(unix_time)
        dt_to = datetime.fromtimestamp(unix_time + POST_ENTRY_CANDLE_MINUTES * 60)

        # copy_rates_range returns candles within [from, to] inclusive
        rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M1, dt_from, dt_to)

        if rates is None or len(rates) == 0:
            record["zone_reached_after_entry"] = None
            record["zone_reach_note"] = "No candle data available"
            no_data_count += 1
            continue

        checked += 1
        zone_reached = False
        minutes_to_reach = None

        # Skip index 0 (entry candle) — start from index 1
        for i, rate in enumerate(rates):
            if i == 0:
                continue  # Skip entry candle

            candle_low = float(rate[3])   # low
            candle_high = float(rate[2])  # high

            # Check if candle overlaps the zone
            if candle_low <= zone_high and candle_high >= zone_low:
                zone_reached = True
                minutes_to_reach = i  # i=1 means 1 minute after entry
                break

        record["zone_reached_after_entry"] = zone_reached
        if zone_reached:
            record["minutes_to_reach_zone"] = minutes_to_reach

    mt5.shutdown()
    print(f"Candle analysis done for {checked} outside-zone trades")
    if no_data_count:
        print(f"  WARNING: {no_data_count} trades had no candle data (too old for broker history?)")
    return trade_results


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
