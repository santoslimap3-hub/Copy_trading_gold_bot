#!/usr/bin/env python3
"""Quick R:R analysis for last 2 weeks of mom's account."""

import json
from datetime import datetime

with open("data/bot_trades.json") as f:
    data = json.load(f)

# Last 2 weeks = trades from Feb 16 onward (March 2 - 14 days)
cutoff = datetime(2026, 2, 16)
cutoff_ts = cutoff.timestamp()

# Use the "deals" section which has both entry (entry=0) and closed (entry=1)
all_deals = data.get("deals", [])
entry_deals = [t for t in all_deals if t.get("entry") == 0]
closed_deals = [t for t in all_deals if t.get("entry") == 1]

# Fallback: if "deals" is empty, use "trades" section
if not all_deals:
    entry_deals = [t for t in data["trades"] if t.get("entry") == 0]
    closed_deals = [t for t in data["trades"] if t.get("entry") == 1]

print(f"Total deals: {len(all_deals)}")
print(f"Entry deals: {len(entry_deals)}, Closed deals: {len(closed_deals)}")
print()

# Build position map
close_map = {}
for d in closed_deals:
    pid = d.get("position_id")
    if pid:
        close_map[pid] = d

# Also check orders for SL/TP info
orders = data.get("orders", [])
order_map = {}  # position_id -> order with SL/TP
for o in orders:
    pid = o.get("ticket")  # order ticket = position_id for entry orders
    if pid and o.get("sl"):
        order_map[pid] = o

# Match entries to closes, filter by last 2 weeks
trades = []
for e in entry_deals:
    pid = e.get("position_id")
    c = close_map.get(pid)
    if c and e["time"] >= cutoff_ts:
        # Try to find SL/TP from orders
        order = order_map.get(pid, {})
        trades.append({
            "time": e["time_str"],
            "side": e["type"],
            "entry": e["price"],
            "close": c["price"],
            "profit": c["profit"],
            "comment": c.get("comment", ""),
            "volume": e["volume"],
            "sl": order.get("sl", 0),
            "tp": order.get("tp", 0),
        })

print(f"Trades in last 2 weeks (since {cutoff.date()}): {len(trades)}")
print()

wins = [t for t in trades if t["profit"] > 0]
losses = [t for t in trades if t["profit"] < 0]
be = [t for t in trades if t["profit"] == 0]

total_won = sum(t["profit"] for t in wins)
total_lost = sum(abs(t["profit"]) for t in losses)
avg_win = total_won / len(wins) if wins else 0
avg_loss = total_lost / len(losses) if losses else 0

print(f"Wins: {len(wins)} | Losses: {len(losses)} | Breakeven: {len(be)}")
print(f"Win rate: {len(wins)/len(trades)*100:.1f}%" if trades else "No trades")
print(f"Total won:  ${total_won:.2f}")
print(f"Total lost: ${total_lost:.2f}")
print(f"Net P/L:    ${total_won - total_lost:.2f}")
print()
print(f"Avg win:  ${avg_win:.2f}")
print(f"Avg loss: ${avg_loss:.2f}")
if avg_loss > 0:
    print(f"R:R (avg win / avg loss): 1:{avg_loss/avg_win:.2f}" if avg_win > 0 else "No wins")
    print(f"  = {avg_win/avg_loss:.2f}R")
    print(f"Profit factor: {total_won/total_lost:.2f}")
print()

# Also calculate R:R in pips (price movement per trade)
print("--- Per-trade breakdown ---")
print(f"{'Time':>20s}  {'Side':>4s}  {'Entry':>10s}  {'Close':>10s}  {'Pips':>8s}  {'P/L':>10s}  {'Result':>6s}  Comment")
for t in trades:
    if t["side"] == "BUY":
        pips = t["close"] - t["entry"]
    else:
        pips = t["entry"] - t["close"]
    tag = "WIN" if t["profit"] > 0 else "LOSS" if t["profit"] < 0 else "BE"
    print(f"  {t['time']:>20s}  {t['side']:>4s}  ${t['entry']:>9.2f}  ${t['close']:>9.2f}  {pips:>+8.2f}  ${t['profit']:>9.2f}  {tag:>6s}  {t['comment']}")

# Win/loss pip analysis
print()
win_pips = []
loss_pips = []
for t in trades:
    if t["side"] == "BUY":
        pips = t["close"] - t["entry"]
    else:
        pips = t["entry"] - t["close"]
    if t["profit"] > 0:
        win_pips.append(pips)
    elif t["profit"] < 0:
        loss_pips.append(abs(pips))

if win_pips and loss_pips:
    avg_win_pips = sum(win_pips) / len(win_pips)
    avg_loss_pips = sum(loss_pips) / len(loss_pips)
    print(f"Avg winning move:  {avg_win_pips:+.2f} pips")
    print(f"Avg losing move:   {avg_loss_pips:.2f} pips")
    print(f"R:R in pips:       1:{avg_loss_pips/avg_win_pips:.2f}" if avg_win_pips > 0 else "")
    print(f"  = {avg_win_pips/avg_loss_pips:.2f}R")
