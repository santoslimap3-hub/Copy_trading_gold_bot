#!/usr/bin/env python3
"""
Persistent Signal Queue - stores trade signals to disk for replay on reconnect.
Ensures no signal is lost even if the bot crashes or loses Telegram connection.
"""

import json
import os
import time
from typing import Optional, Dict, List, Tuple
from datetime import datetime


class SignalQueue:
    """Disk-backed signal queue with JSON persistence"""
    
    def __init__(self, queue_file: str = "signal_queue.json"):
        self.queue_file = queue_file
        self._load_or_init()
    
    def _load_or_init(self):
        """Load existing queue from disk or create empty one"""
        if os.path.exists(self.queue_file):
            try:
                with open(self.queue_file, 'r') as f:
                    data = json.load(f)
                    self.queue = data.get('queue', [])
                    print(f"[SIGNAL_QUEUE] Loaded {len(self.queue)} pending signals from disk")
            except json.JSONDecodeError:
                print(f"[SIGNAL_QUEUE] Corrupted queue file, starting fresh")
                self.queue = []
                self._save()
        else:
            self.queue = []
    
    def _save(self):
        """Persist queue to disk"""
        try:
            with open(self.queue_file, 'w') as f:
                json.dump({'queue': self.queue}, f, indent=2)
        except Exception as e:
            print(f"[SIGNAL_QUEUE] ⚠️ Failed to save queue: {e}")
    
    def add_signal(self, signal_type: str, side: str, entry_price: float, 
                   sl_price: float, lot_size: float, tp_levels: Dict[int, float],
                   msg_id: int, msg_text: str = ""):
        """
        Add a signal to queue (called after successful trade execution).
        This creates an immutable record of what was traded.
        """
        entry = {
            "id": msg_id,
            "signal_type": signal_type,  # "BUY" or "SELL"
            "side": side,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "lot_size": lot_size,
            "tp_levels": tp_levels,
            "msg_text": msg_text[:100],  # truncate for space
            "timestamp": datetime.now().isoformat(),
            "executed": True,  # mark this signal as already executed
        }
        self.queue.append(entry)
        self._save()
        print(f"[SIGNAL_QUEUE] Queued {signal_type} signal (msg_id={msg_id})")
    
    def add_pending_signal(self, msg_id: int, signal_type: str, msg_text: str = ""):
        """
        Add a signal that was RECEIVED but NOT YET EXECUTED (e.g., parsing/execution failed).
        This allows replay if bot reconnects before the signal is executed.
        """
        entry = {
            "id": msg_id,
            "signal_type": signal_type,
            "msg_text": msg_text[:200],
            "timestamp": datetime.now().isoformat(),
            "executed": False,  # will be retried on reconnect
        }
        self.queue.append(entry)
        self._save()
        print(f"[SIGNAL_QUEUE] Added PENDING signal (msg_id={msg_id}, type={signal_type})")
    
    def get_pending_signals(self) -> List[Dict]:
        """
        Return all signals that were received but NOT yet executed.
        Used for replay after reconnect.
        """
        pending = [s for s in self.queue if not s.get('executed', False)]
        print(f"[SIGNAL_QUEUE] Found {len(pending)} pending signals for replay")
        return pending
    
    def mark_executed(self, msg_id: int):
        """Mark a signal as successfully executed"""
        for entry in self.queue:
            if entry['id'] == msg_id:
                entry['executed'] = True
                self._save()
                print(f"[SIGNAL_QUEUE] Marked signal {msg_id} as executed")
                return
    
    def remove_old_signals(self, days: int = 7):
        """Remove signals older than N days to keep file size manageable"""
        now = datetime.now()
        original_len = len(self.queue)
        
        self.queue = [
            s for s in self.queue
            if (now - datetime.fromisoformat(s['timestamp'])).days <= days
        ]
        
        if len(self.queue) < original_len:
            self._save()
            removed = original_len - len(self.queue)
            print(f"[SIGNAL_QUEUE] Removed {removed} signals older than {days} days")
    
    def clear_all(self):
        """Clear all signals (use cautiously)"""
        self.queue = []
        self._save()
        print(f"[SIGNAL_QUEUE] Queue cleared")
    
    def get_stats(self) -> Dict:
        """Get queue statistics"""
        total = len(self.queue)
        executed = len([s for s in self.queue if s.get('executed', False)])
        pending = total - executed
        return {
            "total_signals": total,
            "executed": executed,
            "pending": pending,
        }
