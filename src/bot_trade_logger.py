"""
Bot Trade Logger - Tracks all trades placed by bot_zone.py with detailed PnL metrics.
Logs each trade's lifecycle: open -> close, with entry price, targets, actual P&L, and more.
"""

import json
import os
import time
from typing import Optional, Dict, List
from datetime import datetime, timezone


class BotTradeLogger:
    """Logs and tracks individual bot trades with full lifecycle and PnL"""
    
    DEFAULT_FILE = "bot_trades.json"
    
    def __init__(self, filepath: str = DEFAULT_FILE):
        self.filepath = filepath
        self._ensure_file_exists()
    
    def _ensure_file_exists(self):
        """Create empty structure if file doesn't exist"""
        if not os.path.exists(self.filepath):
            initial_data = {
                "metadata": {
                    "created_at": self._iso_timestamp(),
                    "bot_version": "v2",
                    "symbol": "XAUUSD"
                },
                "summary": {
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
                    "last_updated": self._iso_timestamp()
                },
                "trades": []
            }
            self._save(initial_data)
    
    def _load(self) -> dict:
        """Load trade data from file"""
        if not os.path.exists(self.filepath):
            self._ensure_file_exists()
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            self._ensure_file_exists()
            return self._load()
    
    def _save(self, data: dict):
        """Save trade data to file with atomic write"""
        tmp_file = self.filepath + ".tmp"
        try:
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_file, self.filepath)
        except Exception as e:
            print(f"⚠️ Error saving trade log: {e}")
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
    
    def _iso_timestamp(self) -> str:
        """Get ISO format timestamp with UTC timezone"""
        return datetime.now(timezone.utc).isoformat()
    
    def _recalculate_summary(self, trades: List[dict]):
        """Recalculate summary metrics from trades"""
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
                "last_updated": self._iso_timestamp()
            }
        
        closed_trades = [t for t in trades if t.get("status") == "CLOSED" and "pnl" in t]
        if not closed_trades:
            return {
                "total_trades": len(trades),
                "total_pnl": 0.0,
                "total_wins": 0,
                "total_losses": 0,
                "win_rate_percent": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "max_drawdown": 0.0,
                "largest_win": 0.0,
                "largest_loss": 0.0,
                "pending_trades": len([t for t in trades if t.get("status") == "OPEN"]),
                "last_updated": self._iso_timestamp()
            }
        
        total_pnl = sum(t["pnl"] for t in closed_trades)
        wins = [t["pnl"] for t in closed_trades if t["pnl"] > 0]
        losses = [t["pnl"] for t in closed_trades if t["pnl"] < 0]
        
        win_rate = len(wins) / len(closed_trades) * 100 if closed_trades else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        largest_win = max(wins) if wins else 0
        largest_loss = min(losses) if losses else 0
        
        # Simple max drawdown: lowest point from peak
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
            "pending_trades": len([t for t in trades if t.get("status") == "OPEN"]),
            "last_updated": self._iso_timestamp()
        }
    
    def log_trade_open(self, ticket: int, side: str, entry_price: float, 
                      stop_loss: float, targets: List[float], 
                      lot_size: float, message_id: Optional[int] = None) -> dict:
        """
        Log a trade opening event
        
        Args:
            ticket: MT5 ticket number
            side: "BUY" or "SELL"
            entry_price: Entry price
            stop_loss: Stop loss price
            targets: List of take profit levels [TP1, TP2, TP3, ...]
            lot_size: Lot size traded
            message_id: Optional Telegram message ID that triggered this trade
        
        Returns:
            Trade entry dict
        """
        data = self._load()
        
        trade_entry = {
            "ticket": ticket,
            "status": "OPEN",
            "side": side,
            "entry_price": round(entry_price, 5),
            "stop_loss": round(stop_loss, 5),
            "take_profits": {str(i+1): round(tp, 5) for i, tp in enumerate(targets)},
            "lot_size": round(lot_size, 4),
            "opened_at": self._iso_timestamp(),
            "message_id": message_id,
            "pnl": None,
            "close_price": None,
            "closed_at": None,
            "close_reason": None,
            "tp_hit": None,
            "risk_reward": None
        }
        
        data["trades"].append(trade_entry)
        data["summary"] = self._recalculate_summary(data["trades"])
        self._save(data)
        
        return trade_entry
    
    def log_trade_close(self, ticket: int, close_price: float, close_reason: str, 
                       tp_hit: Optional[str] = None) -> Optional[dict]:
        """
        Log a trade closing event and calculate PnL
        
        Args:
            ticket: MT5 ticket number
            close_price: Price at which trade closed
            close_reason: Reason for close (e.g., "TP1_HIT", "SL_HIT", "MANUAL", "SAFETY_EXIT")
            tp_hit: Which target was hit (if applicable), e.g., "1", "2", "3"
        
        Returns:
            Updated trade entry dict with PnL, or None if ticket not found
        """
        data = self._load()
        
        # Find the trade
        trade = None
        trade_idx = None
        for idx, t in enumerate(data["trades"]):
            if t["ticket"] == ticket:
                trade = t
                trade_idx = idx
                break
        
        if trade is None:
            print(f"⚠️ Ticket {ticket} not found in trade log")
            return None
        
        if trade["status"] == "CLOSED":
            print(f"⚠️ Ticket {ticket} already closed")
            return trade
        
        # Calculate PnL
        entry_price = float(trade["entry_price"])
        lot_size = float(trade["lot_size"])
        side = trade["side"]
        
        # For gold: 1 lot = 100 oz, 1 point = 1 unit, PnL = (price_diff * lot * 100)
        price_diff = close_price - entry_price
        if side == "SELL":
            price_diff = entry_price - close_price
        
        # MT5 Gold contract value: 1 lot = 100 oz
        pnl = price_diff * lot_size * 100
        
        # Risk/Reward ratio (guard against missing stop_loss for legacy entries)
        stop_loss = trade.get("stop_loss")
        risk = abs(entry_price - float(stop_loss)) if stop_loss is not None else None
        reward = abs(close_price - entry_price)
        risk_reward = (reward / risk) if (risk is not None and risk > 0) else None
        
        # Update trade
        trade["status"] = "CLOSED"
        trade["close_price"] = round(close_price, 5)
        trade["closed_at"] = self._iso_timestamp()
        trade["close_reason"] = close_reason
        trade["tp_hit"] = tp_hit
        trade["pnl"] = round(pnl, 2)
        trade["risk_reward"] = round(risk_reward, 2) if risk_reward is not None else None
        
        data["trades"][trade_idx] = trade
        data["summary"] = self._recalculate_summary(data["trades"])
        self._save(data)
        
        return trade
    
    def get_trade(self, ticket: int) -> Optional[dict]:
        """Retrieve a specific trade by ticket number"""
        data = self._load()
        for trade in data["trades"]:
            if trade["ticket"] == ticket:
                return trade
        return None
    
    def get_all_trades(self, status: Optional[str] = None) -> List[dict]:
        """
        Get all trades, optionally filtered by status
        
        Args:
            status: Filter by "OPEN" or "CLOSED", or None for all
        
        Returns:
            List of trade entries
        """
        data = self._load()
        if status:
            return [t for t in data["trades"] if t.get("status") == status]
        return data["trades"]
    
    def get_summary(self) -> dict:
        """Get trading summary metrics"""
        data = self._load()
        return data.get("summary", {})
    
    def get_statistics(self) -> dict:
        """Get detailed trading statistics"""
        data = self._load()
        trades = data["trades"]
        closed_trades = [t for t in trades if t.get("status") == "CLOSED" and "pnl" in t]
        
        if not closed_trades:
            return {
                "closed_trades": 0,
                "pending_trades": len([t for t in trades if t.get("status") == "OPEN"]),
                "stats": "No closed trades yet"
            }
        
        pnls = [t["pnl"] for t in closed_trades]
        tp_hits = {}
        for trade in closed_trades:
            if trade.get("tp_hit"):
                tp = trade["tp_hit"]
                tp_hits[f"TP{tp}"] = tp_hits.get(f"TP{tp}", 0) + 1
        
        close_reasons = {}
        for trade in closed_trades:
            reason = trade.get("close_reason", "UNKNOWN")
            close_reasons[reason] = close_reasons.get(reason, 0) + 1
        
        return {
            "closed_trades": len(closed_trades),
            "pending_trades": len([t for t in trades if t.get("status") == "OPEN"]),
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl_per_trade": round(sum(pnls) / len(pnls), 2) if pnls else 0,
            "best_trade": round(max(pnls), 2) if pnls else 0,
            "worst_trade": round(min(pnls), 2) if pnls else 0,
            "tp_hit_distribution": tp_hits,
            "close_reason_distribution": close_reasons,
            "summary": data.get("summary", {})
        }


# Singleton instance
logger = BotTradeLogger()
