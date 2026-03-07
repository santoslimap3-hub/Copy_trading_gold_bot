#!/usr/bin/env python3
"""
Trade Outcome Tracker
Tracks the full sequence of price level events (TP1-TP4, breakeven, SL)
after trade entry to build statistical data on trade outcome patterns.

Usage:
    tracker = TradeOutcomeTracker(magic=779)
    tracker.register_trade(ticket, side, entry_price, sl, {1: tp1, 2: tp2, 3: tp3, 4: tp4})
    # ... background monitor calls tracker.check_levels(current_price) periodically ...
    tracker.close_trade(ticket, final_profit)
"""

import json
import os
import time
from typing import Dict, List, Optional, Tuple


class TradeOutcomeTracker:
    """
    Tracks price level events for open trades and persists completed trade
    outcome sequences to a JSON file for later statistical analysis.

    For each registered trade, monitors whether price crosses:
      - TP1, TP2, TP3, TP4 (take profit levels)
      - BE (breakeven = entry price)
      - SL (stop loss)

    Records the order in which these levels are hit with timestamps.
    """

    def __init__(self, magic: int, data_dir: str = None):
        self.magic = magic
        if data_dir is None:
            data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
        self.data_dir = data_dir
        self.outcomes_file = os.path.join(data_dir, f"trade_outcomes_{magic}.json")

        # Active trades being tracked: ticket -> trade_info dict
        self._active_trades: Dict[int, Dict] = {}

        # Load existing outcomes from disk
        self._outcomes: List[Dict] = self._load_outcomes()

    def _load_outcomes(self) -> List[Dict]:
        """Load previously saved outcomes from disk."""
        if os.path.exists(self.outcomes_file):
            try:
                with open(self.outcomes_file, "r") as f:
                    data = json.load(f)
                return data.get("outcomes", [])
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _save_outcomes(self):
        """Persist outcomes to disk."""
        os.makedirs(self.data_dir, exist_ok=True)
        data = {
            "magic": self.magic,
            "total_tracked": len(self._outcomes),
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "outcomes": self._outcomes,
        }
        try:
            with open(self.outcomes_file, "w") as f:
                json.dump(data, f, indent=2)
        except IOError:
            pass

    def register_trade(self, ticket: int, side: str, entry_price: float,
                       sl_price: float, tp_levels: Dict[int, float]):
        """
        Start tracking a new trade.

        Args:
            ticket: MT5 position ticket
            side: "BUY" or "SELL"
            entry_price: The actual entry/fill price
            sl_price: Stop loss price
            tp_levels: Dict of TP level number -> price, e.g. {1: 2650, 2: 2655, 3: 2660, 4: 2665}
        """
        # Build the price levels to monitor
        levels: Dict[str, float] = {}
        for tp_num, tp_price in tp_levels.items():
            levels[f"TP{tp_num}"] = tp_price
        levels["BE"] = entry_price
        if sl_price > 0:
            levels["SL"] = sl_price

        self._active_trades[ticket] = {
            "ticket": ticket,
            "side": side,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp_levels": tp_levels.copy(),
            "monitored_levels": levels,
            "sequence": [],           # List of {"level": str, "time": str, "price": float}
            "levels_hit": set(),      # Set of level names already recorded
            "registered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "registered_ts": time.time(),
        }

    def update_levels(self, ticket: int, sl_price: float = None,
                      tp_levels: Dict[int, float] = None):
        """
        Update SL or TP levels on an already-tracked trade.
        Called when the bot receives the real SL/TP from message edits.
        """
        if ticket not in self._active_trades:
            return

        trade = self._active_trades[ticket]

        if sl_price is not None and sl_price > 0:
            trade["sl_price"] = sl_price
            trade["monitored_levels"]["SL"] = sl_price

        if tp_levels:
            trade["tp_levels"].update(tp_levels)
            for tp_num, tp_price in tp_levels.items():
                trade["monitored_levels"][f"TP{tp_num}"] = tp_price

    def check_levels(self, ticket: int, current_price: float) -> List[str]:
        """
        Check if the current price has crossed any monitored levels for a trade.
        Returns a list of newly hit level names (e.g. ["TP1", "BE"]).

        Each level is recorded only once — subsequent crossings are ignored.
        """
        if ticket not in self._active_trades:
            return []

        trade = self._active_trades[ticket]
        side = trade["side"]
        newly_hit = []

        for level_name, level_price in trade["monitored_levels"].items():
            if level_name in trade["levels_hit"]:
                continue

            hit = False
            if level_name.startswith("TP"):
                # TP is hit when price reaches or exceeds the TP level
                if side == "BUY" and current_price >= level_price:
                    hit = True
                elif side == "SELL" and current_price <= level_price:
                    hit = True
            elif level_name == "BE":
                # Breakeven is hit when price returns to entry after initially moving away
                # For BUY: price must have gone up first, then come back down to entry
                # For SELL: price must have gone down first, then come back up to entry
                # We simplify: BE is "hit" when price crosses back through entry
                # Only meaningful if at least one TP was already hit (trade was in profit)
                if trade["levels_hit"]:  # Only track BE after some movement
                    if side == "BUY" and current_price <= level_price:
                        hit = True
                    elif side == "SELL" and current_price >= level_price:
                        hit = True
            elif level_name == "SL":
                # SL is hit when price reaches or exceeds the SL level
                if side == "BUY" and current_price <= level_price:
                    hit = True
                elif side == "SELL" and current_price >= level_price:
                    hit = True

            if hit:
                trade["levels_hit"].add(level_name)
                event = {
                    "level": level_name,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "timestamp": time.time(),
                    "price": current_price,
                }
                trade["sequence"].append(event)
                newly_hit.append(level_name)

        return newly_hit

    def close_trade(self, ticket: int, final_profit: float = 0.0,
                    close_reason: str = ""):
        """
        Finalize tracking for a closed trade and persist the outcome.

        Args:
            ticket: Position ticket
            final_profit: The final P&L of the trade
            close_reason: How the trade was closed (e.g. "SL", "TP1", "manual")
        """
        if ticket not in self._active_trades:
            return

        trade = self._active_trades.pop(ticket)

        # Build the sequence string (e.g. "TP1 → BE → TP2")
        sequence_labels = [evt["level"] for evt in trade["sequence"]]
        sequence_str = " → ".join(sequence_labels) if sequence_labels else "NONE"

        outcome = {
            "ticket": trade["ticket"],
            "side": trade["side"],
            "entry_price": trade["entry_price"],
            "sl_price": trade["sl_price"],
            "tp_levels": {str(k): v for k, v in trade["tp_levels"].items()},
            "sequence": trade["sequence"],
            "sequence_str": sequence_str,
            "levels_hit": sorted(list(trade["levels_hit"])),
            "final_profit": final_profit,
            "close_reason": close_reason,
            "registered_at": trade["registered_at"],
            "closed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_seconds": time.time() - trade["registered_ts"],
        }

        self._outcomes.append(outcome)
        self._save_outcomes()

    def is_tracking(self, ticket: int) -> bool:
        """Check if a ticket is currently being tracked."""
        return ticket in self._active_trades

    def get_active_tickets(self) -> List[int]:
        """Return list of currently tracked ticket numbers."""
        return list(self._active_trades.keys())

    def get_trade_info(self, ticket: int) -> Optional[Dict]:
        """Get tracking info for an active trade (read-only copy)."""
        if ticket not in self._active_trades:
            return None
        trade = self._active_trades[ticket]
        return {
            "ticket": trade["ticket"],
            "side": trade["side"],
            "entry_price": trade["entry_price"],
            "sl_price": trade["sl_price"],
            "tp_levels": trade["tp_levels"].copy(),
            "sequence_so_far": [evt["level"] for evt in trade["sequence"]],
            "levels_hit": sorted(list(trade["levels_hit"])),
        }

    def get_stats_summary(self) -> Dict:
        """
        Calculate statistics from all completed trade outcomes.
        Returns a summary dict with pattern frequencies.
        """
        total = len(self._outcomes)
        if total == 0:
            return {"total_trades": 0}

        # Count sequence patterns
        pattern_counts: Dict[str, int] = {}
        for outcome in self._outcomes:
            pattern = outcome.get("sequence_str", "NONE")
            pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1

        # Sort by frequency
        sorted_patterns = sorted(pattern_counts.items(), key=lambda x: x[1], reverse=True)

        # Level hit rates
        level_hits: Dict[str, int] = {}
        for outcome in self._outcomes:
            for level in outcome.get("levels_hit", []):
                level_hits[level] = level_hits.get(level, 0) + 1

        wins = sum(1 for o in self._outcomes if o.get("final_profit", 0) > 0)
        losses = sum(1 for o in self._outcomes if o.get("final_profit", 0) < 0)

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / total * 100) if total > 0 else 0,
            "pattern_counts": sorted_patterns,
            "level_hit_rates": {k: {"count": v, "pct": v / total * 100} for k, v in level_hits.items()},
        }
