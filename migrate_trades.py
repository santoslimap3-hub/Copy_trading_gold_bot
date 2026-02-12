#!/usr/bin/env python3
"""
Migrate historical trades from trade_history.json to bot_trades.json
Calculates P&L for all historical channel signals
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional, Dict, List


def calculate_entry_price(entry_high: float, entry_low: float) -> float:
    """Calculate entry price as midpoint of entry range"""
    return (entry_high + entry_low) / 2


def get_close_price_and_reason(trade: dict, entry_price: float) -> tuple:
    """Extract close price and reason from results"""
    direction = trade.get('direction', 'BUY')
    results = trade.get('results', [])
    
    if not results:
        return None, None, None
    
    # Find the final result with actual price
    for i, result in enumerate(results):
        if result.get('tp_hits') or result.get('sl_hit') is not None:
            tp_hits = result.get('tp_hits', [])
            sl_hit = result.get('sl_hit')
            
            # Determine close price and reason
            if tp_hits:
                # TP was hit - use TP price
                tp_num = tp_hits[-1]  # Last TP hit
                take_profits = trade.get('take_profits', {})
                if str(tp_num) in take_profits:
                    close_price = float(take_profits[str(tp_num)])
                    return close_price, f"TP{tp_num}_HIT", str(tp_num)
            
            if sl_hit is not None:
                # SL was hit
                close_price = float(sl_hit)
                return close_price, "SL_HIT", None
    
    return None, None, None


def calculate_pnl(side: str, entry_price: float, close_price: float, lot_size: float = 0.1) -> float:
    """
    Calculate P&L for the trade
    Uses lot_size=0.1 as default for historical trades
    """
    if close_price is None:
        return 0
    
    price_diff = close_price - entry_price
    if side == "SELL":
        price_diff = entry_price - close_price
    
    # Gold: 1 lot = 100 oz
    pnl = price_diff * lot_size * 100
    return round(pnl, 2)


def migrate_trades():
    """Migrate historical trades to bot_trades.json"""
    
    # Load source data
    source_file = "data/trade_history.json"
    if not os.path.exists(source_file):
        print(f"❌ Source file not found: {source_file}")
        return False
    
    with open(source_file, 'r', encoding='utf-8') as f:
        source_data = json.load(f)
    
    source_trades = source_data.get('trades', [])
    print(f"📖 Found {len(source_trades)} trades in trade_history.json")
    
    # Load or create target data
    target_file = "src/bot_trades.json"
    if os.path.exists(target_file):
        with open(target_file, 'r', encoding='utf-8') as f:
            target_data = json.load(f)
        print(f"📈 Merging with existing {len(target_data.get('trades', []))} trades in bot_trades.json")
    else:
        target_data = {
            "metadata": {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "bot_version": "v2_migrated",
                "symbol": "XAUUSD"
            },
            "summary": {},
            "trades": []
        }
    
    # Migrate trades
    migrated = 0
    skipped = 0
    
    for source_trade in source_trades:
        msg_id = source_trade.get('message_id')
        
        # Check if already exists
        if any(t['ticket'] == msg_id for t in target_data['trades']):
            skipped += 1
            continue
        
        side = source_trade.get('direction', 'BUY').upper()
        entry_high = source_trade.get('entry_high')
        entry_low = source_trade.get('entry_low')
        
        if entry_high is None or entry_low is None:
            skipped += 1
            continue
        
        entry_price = calculate_entry_price(entry_high, entry_low)
        timestamp = source_trade.get('timestamp', datetime.now(timezone.utc).isoformat())
        
        # Get close info from results
        close_price, close_reason, tp_hit = get_close_price_and_reason(source_trade, entry_price)
        
        if close_price is None or close_reason is None:
            # Trade has no clear result
            # Create as open trade for historical records
            trade_entry = {
                "ticket": msg_id,
                "status": "UNKNOWN",
                "side": side,
                "entry_price": round(entry_price, 5),
                "stop_loss": round(source_trade.get('stop_loss', 0), 5) if source_trade.get('stop_loss') else None,
                "take_profits": {str(k): round(v, 5) for k, v in source_trade.get('take_profits', {}).items()},
                "lot_size": 0.1,
                "opened_at": timestamp,
                "closed_at": None,
                "close_price": None,
                "close_reason": "INCOMPLETE_DATA",
                "tp_hit": None,
                "pnl": None,
                "risk_reward": None,
                "message_id": msg_id,
                "is_historical": True
            }
        else:
            # Calculate P&L
            pnl = calculate_pnl(side, entry_price, close_price, lot_size=0.1)
            
            # Calculate risk/reward
            stop_loss = source_trade.get('stop_loss')
            risk = abs(entry_price - stop_loss) if stop_loss else 0
            reward = abs(close_price - entry_price)
            risk_reward = reward / risk if risk > 0 else 0
            
            trade_entry = {
                "ticket": msg_id,
                "status": "CLOSED",
                "side": side,
                "entry_price": round(entry_price, 5),
                "stop_loss": round(stop_loss, 5) if stop_loss else None,
                "take_profits": {str(k): round(v, 5) for k, v in source_trade.get('take_profits', {}).items()},
                "lot_size": 0.1,
                "opened_at": timestamp,
                "closed_at": datetime.now(timezone.utc).isoformat(),
                "close_price": round(close_price, 5),
                "close_reason": close_reason,
                "tp_hit": tp_hit,
                "pnl": pnl,
                "risk_reward": round(risk_reward, 2),
                "message_id": msg_id,
                "is_historical": True
            }
        
        target_data['trades'].append(trade_entry)
        migrated += 1
    
    # Recalculate summary
    closed_trades = [t for t in target_data['trades'] if t.get('status') == 'CLOSED' and 'pnl' in t]
    
    if closed_trades:
        pnls = [t['pnl'] for t in closed_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        
        # Calculate max drawdown
        cumsum = 0
        peak = 0
        max_dd = 0
        for trade in closed_trades:
            cumsum += trade['pnl']
            if cumsum > peak:
                peak = cumsum
            drawdown = peak - cumsum
            if drawdown > max_dd:
                max_dd = drawdown
        
        target_data['summary'] = {
            "total_trades": len(closed_trades),
            "total_pnl": round(sum(pnls), 2),
            "total_wins": len(wins),
            "total_losses": len(losses),
            "win_rate_percent": round(len(wins) / len(closed_trades) * 100 if closed_trades else 0, 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "max_drawdown": round(max_dd, 2),
            "largest_win": round(max(pnls), 2) if pnls else 0,
            "largest_loss": round(min(pnls), 2) if pnls else 0,
            "pending_trades": len([t for t in target_data['trades'] if t.get('status') == 'OPEN']),
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
    
    # Save target data
    tmp_file = target_file + ".tmp"
    with open(tmp_file, 'w', encoding='utf-8') as f:
        json.dump(target_data, f, indent=2)
    os.replace(tmp_file, target_file)
    
    print(f"\n✅ Migration complete!")
    print(f"   Migrated: {migrated} trades")
    print(f"   Skipped: {skipped} trades")
    print(f"   Total trades in bot_trades.json: {len(target_data['trades'])}")
    
    if target_data.get('summary'):
        summary = target_data['summary']
        print(f"\n📊 Summary Statistics:")
        print(f"   Total P&L: ${summary['total_pnl']:.2f}")
        print(f"   Win Rate: {summary['win_rate_percent']:.2f}%")
        print(f"   Total Wins: {summary['total_wins']}")
        print(f"   Total Losses: {summary['total_losses']}")
        print(f"   Avg Win: ${summary['avg_win']:.2f}")
        print(f"   Avg Loss: ${summary['avg_loss']:.2f}")
        print(f"   Max Drawdown: ${summary['max_drawdown']:.2f}")
    
    return True


if __name__ == "__main__":
    import sys
    
    print("=" * 70)
    print("  🔄 Migrating Historical Trades")
    print("=" * 70)
    print()
    
    success = migrate_trades()
    
    if success:
        print("\n🎉 Migration successful!")
        print("   Run 'python src/bot_trade_analyzer.py' to view results")
    else:
        print("\n❌ Migration failed!")
        sys.exit(1)
