#!/usr/bin/env python3
"""
Trade Outcome Tracker
Tracks the full sequence of price level events (TP1-TP5, breakeven, SL)
after trade entry to build statistical data on trade outcome patterns.

Shadow tracking: when the real position closes (e.g. at TP1), the tracker
continues monitoring price against all remaining levels (TP2-TP5, SL) so
you can evaluate alternative hold strategies later.  Shadow tracking ends
when either the highest TP or SL is hit, or after a timeout (24h default).

Usage:
    tracker = TradeOutcomeTracker(magic=779)
    tracker.register_trade(ticket, side, entry_price, sl, {1: tp1, 2: tp2, 3: tp3, 4: tp4, 5: tp5})
    # ... position closes at TP1 ...
    tracker.mark_position_closed(ticket, profit, "TP1")
    # ... monitor keeps calling tracker.check_levels(ticket, price) ...
    # ... tracker auto-finalizes when highest TP or SL is hit ...
"""

import json
import os
import time
from typing import Dict, List, Optional


class TradeOutcomeTracker:
    """
    Tracks price level events for open trades and persists completed trade
    outcome sequences to a JSON file for later statistical analysis.

    For each registered trade, monitors whether price crosses:
      - TP1, TP2, TP3, TP4, TP5 (take profit levels)
      - BE (breakeven = entry price)
      - SL (stop loss)

    Records the order in which these levels are hit with timestamps.

    Shadow tracking: after the real position closes (e.g. at TP1), the
    tracker continues monitoring price to see which remaining levels
    would have been hit.  Tracking finalizes when the highest registered
    TP or SL is reached, or after SHADOW_TIMEOUT seconds (default 24h).
    """

    SHADOW_TIMEOUT = 86400  # 24 hours — max time to shadow-track after position close

    def __init__(self, magic: int, data_dir: str = None):
        self.magic = magic
        if data_dir is None:
            data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
        self.data_dir = data_dir
        self.outcomes_file = os.path.join(data_dir, f"trade_outcomes_{magic}.json")
        self._active_file = os.path.join(data_dir, f"active_trades_{magic}.json")

        # Active trades being tracked: ticket -> trade_info dict
        # Trades skipped from monitoring (unknown-close) but preserved in the file
        self._preserved_trades: Dict[int, Dict] = {}
        self._active_trades: Dict[int, Dict] = self._load_active_trades()

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

    def _load_active_trades(self) -> Dict[int, Dict]:
        """Load in-progress active trades from disk (survives restarts)."""
        if not os.path.exists(self._active_file):
            return {}
        try:
            with open(self._active_file, "r") as f:
                data = json.load(f)
            trades = {}
            skipped = 0
            for ticket_str, trade in data.items():
                # Skip shadow-tracked trades whose close reason is unknown — they
                # can never be finalized meaningfully and cause noisy re-visit logs.
                # Preserve them in _preserved_trades so they stay in the file.
                if trade.get("position_closed") and trade.get("position_close_reason") == "unknown":
                    self._preserved_trades[int(ticket_str)] = trade
                    skipped += 1
                    continue
                # Convert levels_hit back from list to set
                trade["levels_hit"] = set(trade.get("levels_hit", []))
                # levels_breached tracks which levels price is CURRENTLY past
                # (vs levels_hit which tracks levels EVER crossed)
                if "levels_breached" in trade:
                    trade["levels_breached"] = set(trade.get("levels_breached", []))
                else:
                    # Backward compat: assume all ever-hit levels are currently breached
                    trade["levels_breached"] = trade["levels_hit"].copy()
                trades[int(ticket_str)] = trade
            if trades or skipped:
                print(f"[TradeOutcomeTracker] Restored {len(trades)} active trade(s) from disk"
                      + (f" (skipped {skipped} unknown-close shadow trades)" if skipped else ""))
            return trades
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_active_trades(self):
        """Persist active trades to disk so shadow tracking survives restarts."""
        os.makedirs(self.data_dir, exist_ok=True)
        # Convert sets to lists for JSON serialization
        serializable = {}
        for ticket, trade in self._active_trades.items():
            t = trade.copy()
            t["levels_hit"] = sorted(list(trade["levels_hit"]))
            t["levels_breached"] = sorted(list(trade["levels_breached"]))
            serializable[str(ticket)] = t
        # Preserved (unknown-close) trades stay in the file untouched
        for ticket, trade in self._preserved_trades.items():
            serializable[str(ticket)] = trade
        tmp_file = self._active_file + ".tmp"
        try:
            with open(tmp_file, "w") as f:
                json.dump(serializable, f, indent=2)
            if os.path.exists(self._active_file):
                os.replace(tmp_file, self._active_file)
            else:
                os.rename(tmp_file, self._active_file)
        except IOError:
            try:
                os.remove(tmp_file)
            except OSError:
                pass

    def _save_outcomes(self):
        """Persist outcomes to disk atomically (write to temp file, then rename)."""
        os.makedirs(self.data_dir, exist_ok=True)
        data = {
            "magic": self.magic,
            "total_tracked": len(self._outcomes),
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "outcomes": self._outcomes,
        }
        tmp_file = self.outcomes_file + ".tmp"
        try:
            with open(tmp_file, "w") as f:
                json.dump(data, f, indent=2)
            # Atomic rename (on Windows this replaces the target)
            if os.path.exists(self.outcomes_file):
                os.replace(tmp_file, self.outcomes_file)
            else:
                os.rename(tmp_file, self.outcomes_file)
        except IOError:
            # Clean up temp file on failure
            try:
                os.remove(tmp_file)
            except OSError:
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
            "levels_hit": set(),      # Set of level names EVER crossed (for finalization logic)
            "levels_breached": set(), # Set of levels price is CURRENTLY past (for re-visit detection)
            "registered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "registered_ts": time.time(),
            "position_closed": False,      # True once the real MT5 position is closed
            "position_closed_at": None,    # Timestamp when position closed
            "position_profit": None,       # Actual P&L from the closed position
            "position_close_reason": None, # Why the real position closed (e.g. "TP1")
            "position_close_price": None,  # Actual MT5 execution price when position closed
        }
        self._save_active_trades()

    def register_virtual_trade(self, trade_id: int, side: str, entry_price: float,
                               sl_price: float, tp_levels: Dict[int, float],
                               cancel_reason: str = ""):
        """
        Register a trade that was never entered (limit not filled, zone not hit)
        for tracking what would have happened.  Starts immediately in shadow mode.

        Args:
            trade_id: Unique ID for this virtual trade (use negative msg_id to avoid collision)
            side: "BUY" or "SELL"
            entry_price: The limit price where entry would have occurred
            sl_price: Stop loss price
            tp_levels: Dict of TP level number -> price
            cancel_reason: Why the trade was never entered
        """
        self.register_trade(trade_id, side, entry_price, sl_price, tp_levels)
        trade = self._active_trades[trade_id]
        trade["virtual"] = True
        trade["position_closed"] = True
        trade["position_closed_at"] = time.time()
        trade["position_profit"] = 0.0
        trade["position_close_reason"] = f"not_entered:{cancel_reason}"
        self._save_active_trades()

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

        self._save_active_trades()

    def mark_position_closed(self, ticket: int, final_profit: float = 0.0,
                             close_reason: str = "", close_price: float = 0.0):
        """
        Record that the real MT5 position has been closed, but keep tracking
        price levels (shadow tracking) for strategy evaluation.

        The tracker will continue monitoring until the highest TP or SL is hit,
        or until SHADOW_TIMEOUT elapses.
        """
        if ticket not in self._active_trades:
            return

        trade = self._active_trades[ticket]
        trade["position_closed"] = True
        trade["position_closed_at"] = time.time()
        trade["position_profit"] = final_profit
        trade["position_close_reason"] = close_reason
        trade["position_close_price"] = close_price

        # Record position closure as a sequence event
        trade["sequence"].append({
            "level": f"POS_CLOSED({close_reason})",
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": time.time(),
            "price": close_price,
            "profit": final_profit,
        })
        self._save_active_trades()

    def is_shadow_tracking(self, ticket: int) -> bool:
        """Check if a ticket is being shadow-tracked (position closed, still monitoring)."""
        if ticket not in self._active_trades:
            return False
        return self._active_trades[ticket]["position_closed"]

    def should_finalize(self, ticket: int) -> bool:
        """
        Check whether shadow tracking should end for this ticket.
        Returns True if:
          - The highest registered TP level has been hit, OR
          - SL has been hit (after position close), OR
          - Shadow timeout has elapsed
        """
        if ticket not in self._active_trades:
            return False

        trade = self._active_trades[ticket]
        if not trade["position_closed"]:
            return False

        # Check shadow timeout
        elapsed = time.time() - (trade["position_closed_at"] or trade["registered_ts"])
        if elapsed >= self.SHADOW_TIMEOUT:
            return True

        # Find the highest registered TP level
        tp_nums = [k for k in trade["tp_levels"].keys() if isinstance(k, int)]
        if tp_nums:
            highest_tp = f"TP{max(tp_nums)}"
            if highest_tp in trade["levels_hit"]:
                return True

        # SL hit after position closed → finalize
        if "SL" in trade["levels_hit"] and trade["position_closed"]:
            return True

        return False

    def check_levels(self, ticket: int, current_price: float) -> List[str]:
        """
        Check if the current price has crossed any monitored levels for a trade.
        Returns a list of newly hit level names (e.g. ["TP1", "BE"]).

        Tracks the full price movement including re-visits. If price crosses
        TP3, retraces past TP2, then crosses TP3 again, the sequence will
        record: TP3 → TP2 → TP3 (each transition is captured).
        """
        if ticket not in self._active_trades:
            return []

        trade = self._active_trades[ticket]
        side = trade["side"]
        newly_hit = []
        state_changed = False

        for level_name, level_price in trade["monitored_levels"].items():
            breached = False
            if level_name.startswith("TP"):
                # TP is breached when price reaches or exceeds the TP level
                if side == "BUY" and current_price >= level_price:
                    breached = True
                elif side == "SELL" and current_price <= level_price:
                    breached = True
            elif level_name == "BE":
                # Breakeven is breached when price returns to entry after moving in profit
                # Only meaningful if at least one TP was already hit
                if trade["levels_hit"]:
                    if side == "BUY" and current_price <= level_price:
                        breached = True
                    elif side == "SELL" and current_price >= level_price:
                        breached = True
            elif level_name == "SL":
                # SL is breached when price reaches or exceeds the SL level
                if side == "BUY" and current_price <= level_price:
                    breached = True
                elif side == "SELL" and current_price >= level_price:
                    breached = True

            was_breached = level_name in trade["levels_breached"]

            if breached and not was_breached:
                # Level newly crossed (first time or re-visit after retrace)
                revisit = level_name in trade["levels_hit"]
                trade["levels_breached"].add(level_name)
                trade["levels_hit"].add(level_name)

                # Only record a sequence event if a DIFFERENT level was last recorded.
                # This prevents repeated entries like TP4 → TP4 → TP4 when price
                # oscillates around a level boundary.
                last_level = trade["sequence"][-1]["level"] if trade["sequence"] else None
                if level_name != last_level:
                    event = {
                        "level": level_name,
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "timestamp": time.time(),
                        "price": current_price,
                        "revisit": revisit,
                    }
                    trade["sequence"].append(event)

                newly_hit.append(level_name)
            elif not breached and was_breached:
                # Price retraced back past this level — no longer breached
                trade["levels_breached"].discard(level_name)
                state_changed = True

        if newly_hit or state_changed:
            self._save_active_trades()

        return newly_hit

    def close_trade(self, ticket: int, final_profit: float = 0.0,
                    close_reason: str = ""):
        """
        Finalize tracking for a trade and persist the outcome.

        For shadow-tracked trades, final_profit is the actual position P&L
        (recorded earlier via mark_position_closed), and close_reason reflects
        why shadow tracking ended (e.g. "shadow:TP5_hit", "shadow:SL_hit",
        "shadow:timeout").

        Args:
            ticket: Position ticket
            final_profit: The final P&L of the actual trade (0 if not known)
            close_reason: How/why tracking ended
        """
        if ticket not in self._active_trades:
            return

        trade = self._active_trades.pop(ticket)

        # Build the sequence string (e.g. "TP1 → POS_CLOSED(TP1) → TP2 → TP3 → SL")
        sequence_labels = [evt["level"] for evt in trade["sequence"]]
        sequence_str = " → ".join(sequence_labels) if sequence_labels else "NONE"

        # Use actual position profit if available (from shadow tracking)
        actual_profit = trade.get("position_profit") if trade.get("position_profit") is not None else final_profit

        outcome = {
            "ticket": trade["ticket"],
            "side": trade["side"],
            "entry_price": trade["entry_price"],
            "sl_price": trade["sl_price"],
            "tp_levels": {str(k): v for k, v in trade["tp_levels"].items()},
            "sequence": trade["sequence"],
            "sequence_str": sequence_str,
            "levels_hit": sorted(list(trade["levels_hit"])),
            "final_profit": actual_profit,
            "close_reason": close_reason,
            "registered_at": trade["registered_at"],
            "closed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_seconds": time.time() - trade["registered_ts"],
            # Shadow tracking metadata
            "position_closed_at": trade.get("position_closed_at"),
            "position_close_reason": trade.get("position_close_reason"),
            "shadow_tracked": trade.get("position_closed", False),
            "shadow_duration_seconds": (
                time.time() - trade["position_closed_at"]
                if trade.get("position_closed_at") else 0
            ),
            "virtual": trade.get("virtual", False),
            "position_close_price": trade.get("position_close_price"),
        }

        self._outcomes.append(outcome)
        self._save_outcomes()
        self._save_active_trades()

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
            "position_closed": trade.get("position_closed", False),
            "position_profit": trade.get("position_profit"),
            "position_close_price": trade.get("position_close_price"),
            "virtual": trade.get("virtual", False),
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
