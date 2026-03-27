"""
Synthetic XAUUSD trade-data generator.
Produces perfectly matched synthetic_signal_records.json and synthetic_bot_trades.json.

Usage
-----
# Full regeneration (3 months ending today):
    python tools/synthetic_data_generator.py --generate

# Append one day of new trades (run daily via scheduler):
    python tools/synthetic_data_generator.py --append

# Append N days of trades:
    python tools/synthetic_data_generator.py --append --days 3
"""

import argparse
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone

# ── paths ────────────────────────────────────────────────────────────────
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(TOOLS_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
SIGNAL_PATH = os.path.join(DATA_DIR, "synthetic_signal_records.json")
TRADES_PATH = os.path.join(DATA_DIR, "synthetic_bot_trades.json")

# ── tunables ─────────────────────────────────────────────────────────────
MAGIC = 780
SYMBOL = "XAUUSD"
VOLUME = 0.06
STARTING_BALANCE = 1000.0          # notional account balance at data start
WIN_RATE = 0.66                    # target win rate
BUY_BIAS = 0.58                    # slight buy bias for bullish trend
SIGNALS_PER_DAY_MIN = 3
SIGNALS_PER_DAY_MAX = 4
ENTRY_RATE = 0.45                  # only ~45% of signals result in a bot trade
TP_DIST_MIN = 3.0                  # dollars from entry to TP
TP_DIST_MAX = 14.0
SL_DIST_MIN = 8.0
SL_DIST_MAX = 18.0
ZONE_WIDTH = 5.0                   # zone width in dollars
TREND_DAILY_DRIFT = 0.35           # slight daily bullish drift ($)
VOLATILITY = 8.0                   # daily price noise ($)

# ── helpers ──────────────────────────────────────────────────────────────
_tz = timezone(timedelta(hours=8))  # MT5 broker timezone (UTC+8)
_ticket_counter = 900_000_000


def _next_ticket():
    global _ticket_counter
    _ticket_counter += random.randint(1, 500)
    return _ticket_counter


def _next_msg_id(current):
    return current + random.choice([2, 4])


def _rand_time_in_session(date: datetime) -> datetime:
    """Return a random market-hours timestamp on *date* (Mon-Fri, 01:00-22:00 UTC → 09:00-06:00+1 UTC+8)."""
    hour = random.randint(9, 23)
    minute = random.randint(0, 59)
    second = random.randint(0, 59)
    return date.replace(hour=hour, minute=minute, second=second, microsecond=0, tzinfo=_tz)


def _is_weekday(d: datetime) -> bool:
    return d.weekday() < 5


def _gold_price(day_index: int, base_price: float) -> float:
    """Simulate a slightly bullish gold price path."""
    trend = TREND_DAILY_DRIFT * day_index
    noise = random.gauss(0, VOLATILITY)
    cycle = 15 * math.sin(2 * math.pi * day_index / 45)  # ~45 day cycle
    return round(base_price + trend + noise + cycle, 2)


def _build_tp_levels(entry: float, side: str) -> dict:
    """Generate 5 TP levels."""
    levels = {}
    for i in range(1, 6):
        dist = round(random.uniform(TP_DIST_MIN, TP_DIST_MAX) * (i * 0.55 + 0.45), 2)
        if side == "BUY":
            levels[str(i)] = round(entry + dist, 2)
        else:
            levels[str(i)] = round(entry - dist, 2)
    return levels


def _generate_one_signal(dt: datetime, mid_price: float, msg_id: int, entered: bool):
    """Generate a signal record and optionally a matched bot trade.

    Returns (sig_record, trade_record_or_None).
    """
    side = "BUY" if random.random() < BUY_BIAS else "SELL"
    is_win = random.random() < WIN_RATE

    # signal arrives a bit before entry
    signal_time = dt.replace(tzinfo=None)
    signal_price = round(mid_price + random.uniform(-2, 2), 2)

    # zone
    if side == "BUY":
        zone_low = round(signal_price - random.uniform(1, ZONE_WIDTH), 2)
        zone_high = round(zone_low + ZONE_WIDTH, 2)
        worst_edge = zone_low
    else:
        zone_high = round(signal_price + random.uniform(1, ZONE_WIDTH), 2)
        zone_low = round(zone_high - ZONE_WIDTH, 2)
        worst_edge = zone_high

    sig_to_worst = round(signal_price - worst_edge, 2)
    inside = "inside" if ((side == "BUY" and signal_price <= zone_high) or
                          (side == "SELL" and signal_price >= zone_low)) else "outside"

    # SL & TP (always present on the signal regardless of entry)
    entry_price = round(random.uniform(zone_low, zone_high), 2)
    sl_dist = round(random.uniform(SL_DIST_MIN, SL_DIST_MAX), 2)
    sl_price = round(entry_price - sl_dist, 2) if side == "BUY" else round(entry_price + sl_dist, 2)
    tp_levels = _build_tp_levels(entry_price, side)

    tracking_end = (signal_time + timedelta(hours=random.randint(2, 8))).isoformat()

    # ── NO ENTRY path ────────────────────────────────────────────────
    if not entered:
        no_entry_reason = random.choice(["zone_wait_timeout", "price_never_reached_zone"])
        entry_events = [
            {"time": signal_time.isoformat(), "type": "signal_buffered"},
            {"time": (signal_time + timedelta(seconds=random.randint(5, 20))).isoformat(),
             "type": "zone_from_edit"},
            {"time": (signal_time + timedelta(minutes=random.randint(30, 240))).isoformat(),
             "type": no_entry_reason},
        ]

        # Signal still tracks price even without bot entry (no leading 0).
        # Always ends in -1 (SL) or 5 (TP5).
        no_entry_win = random.random() < WIN_RATE
        if no_entry_win:
            no_entry_seq = list(range(1, 6))                       # 1,2,3,4,5
            no_entry_best = [[tp_levels[str(i)], f"tp{i}"] for i in range(1, 6)]
        else:
            # how far did price get before SL?
            max_tp_before_sl = random.choices([0, 1, 2, 3], weights=[50, 25, 15, 10])[0]
            no_entry_seq = list(range(1, max_tp_before_sl + 1)) + [-1]
            no_entry_best = []
            for i in range(1, 6):
                if i <= max_tp_before_sl:
                    no_entry_best.append([tp_levels[str(i)], f"tp{i}"])
                else:
                    no_entry_best.append([None, f"tp{i}"])

        no_entry_level_times = []
        t = signal_time + timedelta(minutes=random.randint(5, 20))
        for _ in no_entry_seq:
            no_entry_level_times.append(t.isoformat())
            t += timedelta(minutes=random.randint(8, 45))

        sig_record = {
            "msg_id": msg_id,
            "side": side,
            "signal_time": signal_time.isoformat(),
            "signal_price": signal_price,
            "zone": [zone_low, zone_high],
            "signal_to_worst_edge": [sig_to_worst, inside],
            "tp_levels": tp_levels,
            "sl_price": sl_price,
            "price_entered_zone": False,
            "price_extreme_at_zone_arrival": None,
            "best_prices_per_tp": no_entry_best,
            "outcome_sequence": no_entry_seq,
            "bot_entries": {
                str(MAGIC): {
                    "entered": False,
                    "entry_type": no_entry_reason,
                    "entry_price": None,
                    "ticket": None,
                }
            },
            "entry_events": entry_events,
            "tracking_active": False,
            "tracking_complete": True,
            "tracking_started_at": signal_time.isoformat(),
            "tracking_ended_at": tracking_end,
            "last_updated": tracking_end,
            "_level_times": no_entry_level_times,
        }
        return sig_record, None

    # ── ENTRY path ───────────────────────────────────────────────────
    entry_dt = dt + timedelta(seconds=random.randint(15, 90))

    # outcome: win / loss / breakeven-after-TP1
    BE_RATE = 0.15  # 15% of entered trades hit TP1 then reverse to BE
    roll = random.random()

    if roll < BE_RATE:
        # ── hit TP1 then reversed to breakeven ──
        outcome_type = "BE"
        tp_hit = 1
        close_price = entry_price  # closed at breakeven
        close_reason = "EXPERT"
        close_comment = "BE after TP1"
        raw_profit = 0.0
    elif is_win:
        outcome_type = "WIN"
        tp_hit = random.choices([1, 2, 3, 4, 5], weights=[35, 30, 20, 10, 5])[0]
        close_price = tp_levels[str(tp_hit)]
        close_reason = "TP"
        close_comment = f"[tp {close_price:.2f}]"
        price_diff = abs(close_price - entry_price)
        raw_profit = round(price_diff * VOLUME * 100, 2)
    else:
        outcome_type = "LOSS"
        tp_hit = 0
        close_price = sl_price
        close_reason = "SL"
        close_comment = f"[sl {close_price:.2f}]"
        price_diff = abs(close_price - entry_price)
        raw_profit = round(price_diff * VOLUME * 100, 2) * -1

    # close time (15 min to 6 hours after entry)
    close_dt = entry_dt + timedelta(seconds=random.randint(900, 21600))

    open_ticket = _next_ticket()
    close_ticket = _next_ticket()

    # ── build outcome_sequence ───────────────────────────────────────
    # 0=entry zone, 1-5=TP levels, -1=SL, -2=BE (waypoint only)
    # MUST always end in -1 (SL) or 5 (TP5).
    outcome_seq = [0]  # entered zone
    if outcome_type == "BE":
        # hit TP1, came back to BE, then eventually hit SL or recovered to TP5
        be_final = random.choices([-1, 5], weights=[70, 30])[0]
        if be_final == 5:
            outcome_seq.extend([1, -2, 1, 2, 3, 4, 5])
        else:
            outcome_seq.extend([1, -2, -1])
    elif outcome_type == "WIN":
        for lvl in range(1, 6):           # always track through to TP5
            outcome_seq.append(lvl)
    else:
        # how far before SL?
        max_tp_before_sl = random.choices([0, 1, 2, 3], weights=[50, 25, 15, 10])[0]
        for lvl in range(1, max_tp_before_sl + 1):
            outcome_seq.append(lvl)
        outcome_seq.append(-1)

    # ── best_prices_per_tp ───────────────────────────────────────────
    best_prices = []
    # determine highest TP reached in the sequence
    max_tp_reached = max((x for x in outcome_seq if x > 0), default=0)
    for i in range(1, 6):
        if i <= max_tp_reached:
            best_prices.append([tp_levels[str(i)], f"tp{i}"])
        else:
            best_prices.append([None, f"tp{i}"])

    # ── _level_times ─────────────────────────────────────────────────
    level_times = []
    t = signal_time + timedelta(minutes=random.randint(5, 20))
    for _ in outcome_seq:
        level_times.append(t.isoformat())
        t += timedelta(minutes=random.randint(8, 45))

    # ── entry events ─────────────────────────────────────────────────
    entry_events = [
        {"time": signal_time.isoformat(), "type": "signal_buffered"},
        {"time": (signal_time + timedelta(seconds=random.randint(5, 20))).isoformat(),
         "type": "zone_from_edit"},
        {"time": entry_dt.replace(tzinfo=None).isoformat(),
         "type": "market_entry_in_zone"},
    ]

    # ── signal record ────────────────────────────────────────────────
    sig_record = {
        "msg_id": msg_id,
        "side": side,
        "signal_time": signal_time.isoformat(),
        "signal_price": signal_price,
        "zone": [zone_low, zone_high],
        "signal_to_worst_edge": [sig_to_worst, inside],
        "tp_levels": tp_levels,
        "sl_price": sl_price,
        "price_entered_zone": True,
        "price_extreme_at_zone_arrival": round(signal_price + random.uniform(-1, 1), 2),
        "best_prices_per_tp": best_prices,
        "outcome_sequence": outcome_seq,
        "bot_entries": {
            str(MAGIC): {
                "entered": True,
                "entry_type": "market",
                "entry_price": entry_price,
                "ticket": open_ticket,
            }
        },
        "entry_events": entry_events,
        "tracking_active": False,
        "tracking_complete": True,
        "tracking_started_at": signal_time.isoformat(),
        "tracking_ended_at": tracking_end,
        "last_updated": tracking_end,
        "_level_times": level_times,
    }

    # ── bot trade ────────────────────────────────────────────────────
    trade_record = {
        "position_id": open_ticket,
        "magic": MAGIC,
        "symbol": SYMBOL,
        "direction": side,
        "volume": VOLUME,
        "open_time": entry_dt.isoformat(),
        "open_price": entry_price,
        "open_ticket": open_ticket,
        "open_comment": f"Zone {side}",
        "close_time": close_dt.isoformat(),
        "close_price": close_price,
        "close_ticket": close_ticket,
        "close_comment": close_comment,
        "close_reason": close_reason,
        "profit": raw_profit,
        "commission": 0.0,
        "swap": 0.0,
        "net_profit": raw_profit,
    }

    return sig_record, trade_record


def _compute_summary(trades: list) -> dict:
    """Build the summary block from a list of trades."""
    total = len(trades)
    buys = sum(1 for t in trades if t["direction"] == "BUY")
    sells = total - buys
    wins = [t for t in trades if t["net_profit"] > 0]
    losses = [t for t in trades if t["net_profit"] < 0]
    be = [t for t in trades if t["net_profit"] == 0]
    gross_profit = round(sum(t["net_profit"] for t in wins), 2)
    gross_loss = round(sum(t["net_profit"] for t in losses), 2)
    net = round(gross_profit + gross_loss, 2)

    reasons = {}
    for t in trades:
        r = t["close_reason"]
        reasons[r] = reasons.get(r, 0) + 1

    return {
        "total_trades": total,
        "buy_trades": buys,
        "sell_trades": sells,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "breakeven_trades": len(be),
        "win_rate_percent": round(len(wins) / total * 100, 2) if total else 0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_pnl": net,
        "total_commission": 0.0,
        "total_swap": 0.0,
        "avg_net_per_trade": round(net / total, 2) if total else 0,
        "avg_win": round(gross_profit / len(wins), 2) if wins else 0,
        "avg_loss": round(abs(gross_loss) / len(losses), 2) if losses else 0,
        "profit_factor": round(gross_profit / abs(gross_loss), 2) if gross_loss != 0 else 999.99,
        "close_reason_breakdown": reasons,
    }


def _save(signals: dict, trades: list, now: datetime):
    """Write both JSON files atomically."""
    sig_payload = {
        "version": 1,
        "last_updated": now.isoformat(),
        "records": signals,
    }

    trade_payload = {
        "metadata": {
            "created_at": now.isoformat(),
            "symbol": SYMBOL,
            "magic_numbers": [MAGIC],
            "days_back": 90,
        },
        "summary": _compute_summary(trades),
        "trades": trades,
    }

    for path, data in [(SIGNAL_PATH, sig_payload), (TRADES_PATH, trade_payload)]:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    print(f"  -> {len(trades)} trades / {len(signals)} signals written")
    print(f"  -> {SIGNAL_PATH}")
    print(f"  -> {TRADES_PATH}")


# ── main commands ────────────────────────────────────────────────────────
def generate_full(months: int = 3):
    """Generate *months* of synthetic data ending today."""
    now = datetime.now()
    start_date = (now - timedelta(days=months * 30)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    base_price = 2950.0  # starting gold price

    signals = {}
    trades = []
    msg_id = 3800
    day_index = 0
    current = start_date

    while current.date() <= now.date():
        if not _is_weekday(current):
            current += timedelta(days=1)
            day_index += 1
            continue

        mid = _gold_price(day_index, base_price)
        n_signals = random.randint(SIGNALS_PER_DAY_MIN, SIGNALS_PER_DAY_MAX)

        for _ in range(n_signals):
            dt = _rand_time_in_session(current)
            entered = random.random() < ENTRY_RATE
            sig, trade = _generate_one_signal(dt, mid, msg_id, entered)
            signals[str(msg_id)] = sig
            if trade is not None:
                trades.append(trade)
            msg_id = _next_msg_id(msg_id)

        current += timedelta(days=1)
        day_index += 1

    # sort trades by open_time
    trades.sort(key=lambda t: t["open_time"])

    _save(signals, trades, now)
    summary = _compute_summary(trades)
    print(f"\n  Win rate: {summary['win_rate_percent']}%")
    print(f"  Net P&L: ${summary['net_pnl']}")
    print(f"  Profit factor: {summary['profit_factor']}")


def append_days(n_days: int = 1):
    """Append *n_days* of new trades to existing synthetic data."""
    # load existing
    if not os.path.exists(SIGNAL_PATH) or not os.path.exists(TRADES_PATH):
        print("ERROR: No existing synthetic data found. Run --generate first.")
        sys.exit(1)

    with open(SIGNAL_PATH, "r", encoding="utf-8") as f:
        sig_data = json.load(f)
    with open(TRADES_PATH, "r", encoding="utf-8") as f:
        trade_data = json.load(f)

    signals = sig_data["records"]
    trades = trade_data["trades"]

    # find the latest trade date and msg_id
    if trades:
        last_date_str = max(t["open_time"] for t in trades)
        last_date = datetime.fromisoformat(last_date_str.replace("+08:00", ""))
    else:
        last_date = datetime.now() - timedelta(days=1)

    max_msg = max(int(k) for k in signals) if signals else 3800

    # figure out base_price from latest trade
    if trades:
        base_price = trades[-1]["open_price"]
    else:
        base_price = 3050.0

    msg_id = _next_msg_id(max_msg)
    new_count = 0

    for d in range(1, n_days + 1):
        target_date = (last_date + timedelta(days=d)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # skip weekends
        if not _is_weekday(target_date):
            continue

        # slight drift per appended day
        mid = round(base_price + random.gauss(TREND_DAILY_DRIFT * d, VOLATILITY * 0.5), 2)
        n_signals = random.randint(SIGNALS_PER_DAY_MIN, SIGNALS_PER_DAY_MAX)

        for _ in range(n_signals):
            dt = _rand_time_in_session(target_date)
            entered = random.random() < ENTRY_RATE
            sig, trade = _generate_one_signal(dt, mid, msg_id, entered)
            signals[str(msg_id)] = sig
            if trade is not None:
                trades.append(trade)
            msg_id = _next_msg_id(msg_id)
            new_count += 1

    trades.sort(key=lambda t: t["open_time"])
    _save(signals, trades, datetime.now())
    print(f"  Appended {new_count} new trades for {n_days} day(s)")


# ── CLI ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Synthetic XAUUSD data generator")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--generate", action="store_true",
                       help="Full regeneration (3 months ending today)")
    group.add_argument("--append", action="store_true",
                       help="Append new day(s) of trades")
    parser.add_argument("--days", type=int, default=1,
                        help="Number of days to append (default: 1)")
    parser.add_argument("--months", type=int, default=3,
                        help="Months of data for --generate (default: 3)")
    args = parser.parse_args()

    random.seed()  # truly random each run

    if args.generate:
        print(f"Generating {args.months} months of synthetic data...")
        generate_full(args.months)
    else:
        print(f"Appending {args.days} day(s) of synthetic trades...")
        append_days(args.days)

    print("Done.")
