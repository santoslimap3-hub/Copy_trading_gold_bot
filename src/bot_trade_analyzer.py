#!/usr/bin/env python3
"""
Bot Trade PnL Analyzer & Dashboard
Displays trading statistics and PnL analysis from bot_trades.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List

import MetaTrader5 as mt5

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


def format_price(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"${value:.5f}"


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
        print(f"   Entry:     {format_price(trade.get('entry_price'))}")
        lot_size = trade.get("lot_size")
        lot_str = f"{lot_size:.4f}" if isinstance(lot_size, (int, float)) else "N/A"
        print(f"   Lot Size:  {lot_str}")
        print(f"   Stop Loss: {format_price(trade.get('stop_loss'))}")
        tps = trade.get('take_profits', {})
        if tps:
            tp_str = " | ".join([f"TP{k}: ${v:.5f}" for k, v in sorted(tps.items())])
            print(f"   Targets:   {tp_str}")
        print(f"   Opened:    {trade.get('opened_at', 'N/A')}")


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
        entry_price = trade.get("entry_price")
        close_price = trade.get("close_price")
        print(f"   Entry:     {format_price(entry_price)} | Close: {format_price(close_price)}")
        lot_size = trade.get("lot_size")
        lot_str = f"{lot_size:.4f}" if isinstance(lot_size, (int, float)) else "N/A"
        print(f"   Lot Size:  {lot_str}")
        print(f"   Opened:    {trade.get('opened_at', 'N/A')}")
        print(f"   Closed:    {trade.get('closed_at', 'N/A')} ({trade.get('close_reason', 'N/A')})")
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


def parse_magics(value: str) -> List[int]:
    if not value:
        return []
    magics = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            magics.append(int(item))
        except ValueError:
            print(f"[WARN] Skipping invalid magic number: {item}")
    return magics


def connect_mt5() -> bool:
    if mt5.initialize():
        return True
    print(f"[ERROR] MT5 initialize failed: {mt5.last_error()}")
    return False


def iso_from_timestamp(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def weighted_avg_price(deals: List[mt5.TradeDeal]) -> Optional[float]:
    if not deals:
        return None
    total_vol = sum(d.volume for d in deals)
    if total_vol <= 0:
        return None
    return sum(d.price * d.volume for d in deals) / total_vol


def build_summary(trades: List[dict]) -> dict:
    if not trades:
        return {
            "total_trades": 0,
            "total_pnl": 0.0,
            "total_wins": 0,
            "total_losses": 0,
            "win_rate_percent": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "max_drawdown": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
            "pending_trades": 0,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    closed_trades = [t for t in trades if t.get("status") == "CLOSED" and t.get("pnl") is not None]
    open_count = len([t for t in trades if t.get("status") == "OPEN"])

    if not closed_trades:
        return {
            "total_trades": 0,
            "total_pnl": 0.0,
            "total_wins": 0,
            "total_losses": 0,
            "win_rate_percent": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "max_drawdown": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
            "pending_trades": open_count,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    pnls = [t["pnl"] for t in closed_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_pnl = sum(pnls)
    win_rate = (len(wins) / len(closed_trades)) * 100 if closed_trades else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    largest_win = max(wins) if wins else 0
    largest_loss = min(losses) if losses else 0

    cumsum = 0
    peak = 0
    max_dd = 0
    for trade in closed_trades:
        cumsum += trade["pnl"]
        if cumsum > peak:
            peak = cumsum
        drawdown = peak - cumsum
        if drawdown > max_dd:
            max_dd = drawdown

    return {
        "total_trades": len(closed_trades),
        "total_pnl": round(total_pnl, 2),
        "total_wins": len(wins),
        "total_losses": len(losses),
        "win_rate_percent": round(win_rate, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_drawdown": round(max_dd, 2),
        "largest_win": round(largest_win, 2),
        "largest_loss": round(largest_loss, 2),
        "pending_trades": open_count,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def build_trades_from_mt5(days_back: int, symbol: Optional[str], magics: List[int]) -> Dict[str, object]:
    from_date = datetime.now() - timedelta(days=days_back)
    to_date = datetime.now()

    deals = mt5.history_deals_get(from_date, to_date)
    if deals is None:
        print(f"[ERROR] MT5 history_deals_get failed: {mt5.last_error()}")
        return {}
    if len(deals) == 0:
        print("[WARN] No deals found for the specified period")
        return {}

    filtered_deals = []
    for deal in deals:
        if symbol and deal.symbol != symbol:
            continue
        if magics and int(deal.magic) not in magics:
            continue
        filtered_deals.append(deal)

    if not filtered_deals:
        print("[WARN] No bot deals found with the specified filters")
        return {}

    positions = mt5.positions_get()
    positions_by_id = {int(p.ticket): p for p in positions} if positions else {}

    deals_by_position: Dict[int, List[mt5.TradeDeal]] = {}
    for deal in filtered_deals:
        position_id = int(deal.position_id) if getattr(deal, "position_id", 0) else int(deal.order)
        deals_by_position.setdefault(position_id, []).append(deal)

    trades: List[dict] = []
    for position_id, group in deals_by_position.items():
        group.sort(key=lambda d: d.time)

        entry_deals = [d for d in group if d.entry in (mt5.DEAL_ENTRY_IN, mt5.DEAL_ENTRY_INOUT)]
        exit_deals = [d for d in group if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT)]

        entry_price = weighted_avg_price(entry_deals)
        close_price = weighted_avg_price(exit_deals)
        entry_time = iso_from_timestamp(group[0].time)
        close_time = iso_from_timestamp(exit_deals[-1].time) if exit_deals else None

        side = "BUY"
        if entry_deals:
            side = "BUY" if entry_deals[0].type == mt5.DEAL_TYPE_BUY else "SELL"
        elif group:
            side = "BUY" if group[0].type == mt5.DEAL_TYPE_BUY else "SELL"

        lot_size = sum(d.volume for d in entry_deals) if entry_deals else None
        pnl = None
        if exit_deals:
            pnl = sum(d.profit + d.commission + d.swap for d in group)

        close_reason = None
        if exit_deals:
            close_reason = str(exit_deals[-1].reason)

        trade = {
            "ticket": position_id,
            "status": "CLOSED" if exit_deals else "OPEN",
            "side": side,
            "entry_price": round(entry_price, 5) if entry_price is not None else None,
            "stop_loss": None,
            "take_profits": {},
            "lot_size": round(lot_size, 4) if lot_size is not None else None,
            "opened_at": entry_time,
            "message_id": None,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "close_price": round(close_price, 5) if close_price is not None else None,
            "closed_at": close_time,
            "close_reason": close_reason,
            "tp_hit": None,
            "risk_reward": 0,
        }

        if trade["status"] == "OPEN" and position_id in positions_by_id:
            pos = positions_by_id[position_id]
            trade["entry_price"] = round(float(pos.price_open), 5)
            trade["lot_size"] = round(float(pos.volume), 4)
            trade["stop_loss"] = round(float(pos.sl), 5) if pos.sl > 0 else None
            trade["take_profits"] = {"1": round(float(pos.tp), 5)} if pos.tp > 0 else {}

        trades.append(trade)

    trades.sort(key=lambda t: t.get("opened_at", ""))
    return {
        "summary": build_summary(trades),
        "trades": trades,
    }


def build_metadata(symbol: Optional[str], magics: List[int]) -> dict:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "mt5_history",
        "symbol": symbol or "ALL",
        "magics": magics,
    }


def save_trade_data(filepath: str, data: dict) -> None:
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    tmp_file = filepath + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_file, filepath)


def main():
    parser = argparse.ArgumentParser(description="Bot trade analyzer")
    parser.add_argument("--file", help="Path to bot_trades.json")
    parser.add_argument("--mt5", action="store_true", help="Analyze trades from MT5 history")
    parser.add_argument("--days", type=int, default=120, help="Days of MT5 history to fetch")
    parser.add_argument("--symbol", default=None, help="Symbol filter (e.g., XAUUSD)")
    parser.add_argument("--magics", default="777,778", help="Comma-separated magic numbers")
    parser.add_argument("--write-json", action="store_true", help="Write MT5 analysis to bot_trades.json")

    args = parser.parse_args()

    if args.mt5:
        magics = parse_magics(args.magics)
        if not connect_mt5():
            return
        data = build_trades_from_mt5(args.days, args.symbol, magics)
        mt5.shutdown()
        if data and args.write_json:
            filepath = args.file if args.file else find_trades_file()
            data_with_meta = {
                "metadata": build_metadata(args.symbol, magics),
                **data,
            }
            save_trade_data(filepath, data_with_meta)
    else:
        filepath = args.file if args.file else find_trades_file()
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
