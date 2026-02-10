#!/usr/bin/env python3
"""
Reconnect Monitor - tracks reconnection events and alerting when thresholds are exceeded.
Helps identify persistent connectivity issues early.
"""

import time
from datetime import datetime, timedelta
from collections import deque
from typing import List, Dict, Optional


class ReconnectMonitor:
    """Monitor and track reconnection events with alerting"""
    
    def __init__(self, alert_threshold: int = 5, time_window_minutes: int = 30):
        """
        alert_threshold: alert if N reconnects occur in time_window
        time_window_minutes: rolling window size for counting reconnects
        """
        self.alert_threshold = alert_threshold
        self.time_window = timedelta(minutes=time_window_minutes)
        self.reconnect_log: deque = deque()  # stores (timestamp, error_type)
        self.alerts_sent: List[Dict] = []
        self.error_counters: Dict[str, int] = {}  # count by error type
    
    def record_reconnect(self, error_type: str = "unknown", error_msg: str = ""):
        """Record a reconnection event"""
        now = datetime.now()
        self.reconnect_log.append({
            "timestamp": now,
            "error_type": error_type,
            "error_msg": error_msg[:100]  # truncate
        })
        
        # Track error types
        self.error_counters[error_type] = self.error_counters.get(error_type, 0) + 1
        
        print(f"[RECONNECT] ✋ Reconnected: {error_type}")
        
        # Check if alert should be triggered
        alert = self._check_alert_threshold()
        if alert:
            self._trigger_alert(alert)
    
    def _check_alert_threshold(self) -> Optional[Dict]:
        """
        Check if reconnect frequency exceeds threshold.
        Returns alert dict if threshold exceeded, None otherwise.
        """
        now = datetime.now()
        cutoff = now - self.time_window
        
        # Count reconnects in the rolling window
        recent = [
            r for r in self.reconnect_log
            if r['timestamp'] >= cutoff
        ]
        
        if len(recent) >= self.alert_threshold:
            return {
                "timestamp": now,
                "reconnect_count": len(recent),
                "threshold": self.alert_threshold,
                "time_window_minutes": self.time_window.total_seconds() / 60,
                "recent_errors": [r['error_type'] for r in recent[-5:]],  # last 5
            }
        
        return None
    
    def _trigger_alert(self, alert: Dict):
        """Handle alert (log, could send email/SMS/Telegram later)"""
        msg = (
            f"🚨 ALERT: Bot reconnected {alert['reconnect_count']} times "
            f"in {alert['time_window_minutes']:.0f} min (threshold: {alert['threshold']})\n"
            f"   Recent error types: {', '.join(alert['recent_errors'])}"
        )
        print(f"[RECONNECT] {msg}")
        
        self.alerts_sent.append(alert)
    
    def get_stats(self) -> Dict:
        """Get reconnection statistics"""
        now = datetime.now()
        cutoff = now - self.time_window
        recent = [r for r in self.reconnect_log if r['timestamp'] >= cutoff]
        
        return {
            "total_reconnects": len(self.reconnect_log),
            "reconnects_in_window": len(recent),
            "alert_threshold": self.alert_threshold,
            "time_window_minutes": self.time_window.total_seconds() / 60,
            "error_breakdown": self.error_counters,
            "alerts_triggered": len(self.alerts_sent),
        }
    
    def reset_window(self):
        """Clear old reconnect records to keep memory usage bounded"""
        now = datetime.now()
        cutoff = now - timedelta(hours=24)  # keep 24 hours of history
        
        original_len = len(self.reconnect_log)
        self.reconnect_log = deque(
            r for r in self.reconnect_log
            if r['timestamp'] >= cutoff
        )
        
        if len(self.reconnect_log) < original_len:
            print(f"[RECONNECT] 🧹 Cleaned up {original_len - len(self.reconnect_log)} old records")
    
    def print_summary(self):
        """Print a summary of reconnection stats"""
        stats = self.get_stats()
        print("\n" + "=" * 70)
        print("📊 RECONNECT MONITOR SUMMARY")
        print("=" * 70)
        print(f"Total reconnects: {stats['total_reconnects']}")
        print(f"In last {stats['time_window_minutes']:.0f} min: {stats['reconnects_in_window']} (threshold: {stats['alert_threshold']})")
        print(f"Error breakdown: {stats['error_breakdown']}")
        print(f"Alerts triggered: {stats['alerts_triggered']}")
        print("=" * 70 + "\n")
