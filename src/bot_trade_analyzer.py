#!/usr/bin/env python3
"""
Bot Trade PnL Analyzer & Dashboard
Displays trading statistics and PnL analysis from bot_trades.json
"""

import json
import os
import sys
from datetime import datetime
from typing import Optional

def load_trades(filepath: str = "bot_trades.json") -> dict:
    """Load trade data from file"""
    if not os.path.exists(filepath):
        print(f"[ERROR] File not found: {filepath}")
        return {}
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Error loading file: {e}")
        return {}


def print_header(text: str):
    """Print formatted header"""
    border = "=" * 80
    print(f"\n{border}")
    print(f"  {text}")
    print(f"{border}\n")


def print_section(text: str):
    """Print formatted section"""
    print(f"\n{text}")
    print("-" * len(text))


def format_currency(value: float) -> str:
    """Format value as currency with color coding"""
    sign = "$" if value >= 0 else "-$"
    color = "32" if value >= 0 else "31"  # Green for positive, red for negative
    return f"\033[{color}m{sign}{abs(value):.2f}\033[0m"


def format_percent(value: float) -> str:
    """Format value as percentage with color coding"""
    color = "32" if value >= 0 else "31"  # Green for positive, red for negative
    return f"\033[{color}m{value:.2f}%\033[0m"


def display_summary(data: dict):
    """Display overall trading summary"""
    summary = data.get("summary", {})
    
    print_header("BOT TRADING SUMMARY")
    
    print(f"Total Trades (Closed):    {summary.get('total_trades', 0)}")
    print(f"Pending Trades:           {summary.get('pending_trades', 0)}")
    print(f"Total Wins:               {summary.get('total_wins', 0)}")
    print(f"Total Losses:             {summary.get('total_losses', 0)}")
    print(f"Win Rate:                 {format_percent(summary.get('win_rate_percent', 0))}")
    print()
    print(f"Total P&L:                {format_currency(summary.get('total_pnl', 0))}")
    print(f"Average Win:              {format_currency(summary.get('avg_win', 0))}")
    print(f"Average Loss:             {format_currency(summary.get('avg_loss', 0))}")
    print(f"Largest Win:              {format_currency(summary.get('largest_win', 0))}")
    print(f"Largest Loss:             {format_currency(summary.get('largest_loss', 0))}")
    print(f"Max Drawdown:             {format_currency(summary.get('max_drawdown', 0))}")
    print()
    print(f"Last Updated:             {summary.get('last_updated', 'N/A')}")


def display_open_trades(trades: list):
    """Display currently open trades"""
    open_trades = [t for t in trades if t.get("status") == "OPEN"]
    
    if not open_trades:
        print("No open trades")
        return
    
    print_section(f"OPEN TRADES ({len(open_trades)})")
    
    for i, trade in enumerate(open_trades, 1):
        print(f"\n{i}. Ticket {trade['ticket']}")
        print(f"   Side:      {trade['side']}")
        print(f"   Entry:     ${trade['entry_price']:.5f}")
        print(f"   Lot Size:  {trade['lot_size']:.4f}")
        print(f"   Stop Loss: ${trade['stop_loss']:.5f}")
        tps = trade.get('take_profits', {})
        if tps:
            tp_str = " | ".join([f"TP{k}: ${v:.5f}" for k, v in sorted(tps.items())])
            print(f"   Targets:   {tp_str}")
        print(f"   Opened:    {trade['opened_at']}")


def display_closed_trades(trades: list, limit: Optional[int] = 20):
    """Display recently closed trades"""
    closed_trades = [t for t in trades if t.get("status") == "CLOSED"]
    
    if not closed_trades:
        print("No closed trades")
        return
    
    # Sort by close time (most recent first)
    closed_trades = sorted(closed_trades, key=lambda x: x.get('closed_at', ''), reverse=True)
    
    if limit:
        closed_trades = closed_trades[:limit]
    
    print_section(f"RECENT CLOSED TRADES (showing {len(closed_trades)})")
    
    for i, trade in enumerate(closed_trades, 1):
        pnl = trade.get('pnl', 0)
        pnl_str = format_currency(pnl)
        win_loss = "[WIN]" if pnl > 0 else "[LOSS]" if pnl < 0 else "[EVEN]"
        
        print(f"\n{i}. Ticket {trade['ticket']} | {win_loss} | P&L: {pnl_str}")
        print(f"   Side:      {trade['side']}")
        print(f"   Entry:     ${trade['entry_price']:.5f} | Close: ${trade['close_price']:.5f}")
        print(f"   Lot Size:  {trade['lot_size']:.4f}")
        print(f"   Opened:    {trade['opened_at']}")
        print(f"   Closed:    {trade['closed_at']} ({trade.get('close_reason', 'N/A')})")
        if trade.get('tp_hit'):
            print(f"   TP Hit:    TP{trade['tp_hit']}")
        print(f"   Risk/Reward: {trade.get('risk_reward', 0):.2f}")


def display_statistics(data: dict):
    """Display detailed trading statistics"""
    trades = data.get("trades", [])
    closed_trades = [t for t in trades if t.get("status") == "CLOSED" and "pnl" in t]
    
    if not closed_trades:
        print("Not enough data for statistics")
        return
    
    print_section("DETAILED STATISTICS")
    
    # P&L distribution
    pnls = [t['pnl'] for t in closed_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    breakevens = [p for p in pnls if p == 0]
    
    print(f"Wins:              {len(wins)}")
    print(f"Losses:            {len(losses)}")
    print(f"Breakevens:        {len(breakevens)}")
    print()
    
    if wins:
        print(f"Total Wins P&L:    {format_currency(sum(wins))}")
        print(f"Avg Win:           {format_currency(sum(wins) / len(wins))}")
    
    if losses:
        print(f"Total Losses P&L:  {format_currency(sum(losses))}")
        print(f"Avg Loss:          {format_currency(sum(losses) / len(losses))}")
    
    # Close reason distribution
    print()
    close_reasons = {}
    for trade in closed_trades:
        reason = trade.get('close_reason', 'UNKNOWN')
        close_reasons[reason] = close_reasons.get(reason, 0) + 1
    
    print(f"Close Reasons:")
    for reason, count in sorted(close_reasons.items(), key=lambda x: x[1], reverse=True):
        print(f"  {reason}:  {count}")
    
    # TP hit distribution
    tp_hits = {}
    for trade in closed_trades:
        if trade.get('tp_hit'):
            tp = f"TP{trade['tp_hit']}"
            tp_hits[tp] = tp_hits.get(tp, 0) + 1
    
    if tp_hits:
        print()
        print(f"TP Hit Distribution:")
        for tp, count in sorted(tp_hits.items()):
            percentage = (count / len(closed_trades)) * 100
            print(f"  {tp}:  {count} ({percentage:.1f}%)")


def display_by_side(trades: list):
    """Display statistics by trade side (BUY vs SELL)"""
    closed_trades = [t for t in trades if t.get("status") == "CLOSED" and "pnl" in t]
    
    if not closed_trades:
        return
    
    print_section("STATISTICS BY SIDE")
    
    for side in ["BUY", "SELL"]:
        side_trades = [t for t in closed_trades if t.get('side') == side]
        if not side_trades:
            continue
        
        pnls = [t['pnl'] for t in side_trades]
        wins = len([p for p in pnls if p > 0])
        losses = len([p for p in pnls if p < 0])
        total_pnl = sum(pnls)
        win_rate = (wins / len(side_trades) * 100) if side_trades else 0
        
        print(f"\n{side} Trades:")
        print(f"  Count:     {len(side_trades)}")
        print(f"  Wins:      {wins}")
        print(f"  Losses:    {losses}")
        print(f"  Win Rate:  {format_percent(win_rate)}")
        print(f"  Total P&L: {format_currency(total_pnl)}")
        print(f"  Avg P&L:   {format_currency(total_pnl / len(side_trades))}")


def find_trades_file():
    """Find bot_trades.json in common locations"""
    possible_paths = [
        "src/bot_trades.json",  # Project root structure (priority)
        os.path.join(os.path.dirname(__file__), "bot_trades.json"),  # Script directory
        "bot_trades.json",  # Current directory
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
    
    return "src/bot_trades.json"  # Default to src/


def main():
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
    else:
        filepath = find_trades_file()
    
    data = load_trades(filepath)
    if not data:
        return
    
    display_summary(data)
    
    trades = data.get("trades", [])
    
    if trades:
        display_open_trades(trades)
        display_closed_trades(trades, limit=15)
        display_by_side(trades)
        display_statistics(data)
    
    print("\n")


if __name__ == "__main__":
    main()
