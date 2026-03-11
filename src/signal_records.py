#!/usr/bin/env python3
"""
Signal Records Manager
======================
One JSON record per Telegram signal (keyed by msg_id), shared across all bots.

Each record stores:
  - signal_price      : market price at the instant BUY/SELL NOW was received
  - zone              : [zone_low, zone_high] once received
  - signal_to_worst_edge : [distance, "inside"|"outside"]
      distance = signal_price − worst_zone_edge
      (worst = zone_high for BUY, zone_low for SELL)
      negative + "inside" → great R:R
  - price_entered_zone : whether price ever touched the zone
  - best_prices_per_tp : [[best_price_in_zone_before_tp1, "tp1"], ...]
      only prices inside the zone, best = lowest for BUY / highest for SELL
      resets after each TP level is hit
  - outcome_sequence  : ordered list of integers
      -1 = SL, 0 = zone touched, 1 = TP1, 2 = TP2, ..., 5 = TP5
      duplicates allowed (price can revisit levels)
  - bot_entries       : per-magic entry result {"entered", "entry_type", "entry_price", "ticket"}
  - entry_events      : flat list of entry quality events (replaces entry_stats_*.json)

Multi-bot safety
----------------
Multiple bots (777, 779, …) may receive the same Telegram message.
  • register_signal() is idempotent — only the first call creates the record.
  • All write operations hold an OS-level lock file while they run.

Usage
-----
    records = SignalRecordsManager(data_dir, magic=777)

    # 1. Signal received
    records.register_signal(msg_id, "BUY", signal_price=5266.47)

    # 2. Zone + levels received (edit)
    records.set_zone(msg_id, zone_low=5250, zone_high=5260,
                     tp_levels={1: 5280, 2: 5300}, sl_price=5200)

    # 3. Every price tick (call from monitoring loop)
    records.update_price(msg_id, current_price)   # fast, in-memory

    # 4. Level hit (call alongside outcome tracker)
    records.record_level_hit(msg_id, level=1)     # TP1
    records.record_level_hit(msg_id, level=-1)    # SL — auto-finalizes

    # 5. Bot entry result
    records.record_bot_entry(msg_id, entered=True, entry_type="limit",
                             entry_price=5253.0, ticket=9270138)

    # 6. Entry events (replaces record_entry_stat)
    records.record_entry_event(msg_id, "limit_placed", limit_price=5250.0)
"""

import os
import json
import time
import logging
from datetime import datetime
from typing import Optional, Dict, List, Any

log = logging.getLogger(__name__)

# ── outcome level constants ───────────────────────────────────────────────────
LEVEL_SL   = -1
LEVEL_ZONE =  0
# 1..5 = TP1..TP5

_LEVEL_NAME_MAP: Dict[str, int] = {
    "SL": -1,
    "ZONE": 0,
    "TP1": 1, "TP2": 2, "TP3": 3, "TP4": 4, "TP5": 5,
}


def level_to_int(level_name: str) -> Optional[int]:
    """Convert "TP1", "TP2", "SL" etc. to the integer used in outcome_sequence."""
    return _LEVEL_NAME_MAP.get(level_name.upper().strip())


# ── manager ───────────────────────────────────────────────────────────────────

class SignalRecordsManager:
    """
    Manages data/signal_records.json with cross-bot deduplication.

    All write operations are atomic (write-to-tmp then os.replace) and
    protected by an OS-level lock file so multiple bot processes can safely
    share the file.

    Price tracking (update_price) is in-memory only for speed; the running
    best-price-in-zone is committed to disk each time a level is hit.
    """

    LOCK_TIMEOUT   = 5.0   # seconds to wait for lock before giving up
    LOCK_RETRY     = 0.03  # seconds between lock attempts
    SHADOW_TIMEOUT = 24 * 3600  # auto-finalize inactive records after 24 h

    def __init__(self, data_dir: str, magic: int):
        self._magic     = magic
        self._file      = os.path.join(data_dir, "signal_records.json")
        self._lock_path = self._file + ".lock"
        os.makedirs(data_dir, exist_ok=True)

        # In-memory state for active price tracking — never hits disk on update_price
        # {msg_id: {"best_in_zone": float|None, "price_extreme": float|None,
        #           "zone": [lo,hi]|None, "side": str, "active": bool, "entered_zone": bool}}
        self._mem: Dict[int, Dict] = {}
        self._restore_mem_state()

    # ── locking ──────────────────────────────────────────────────────────────

    def _acquire(self) -> bool:
        deadline = time.monotonic() + self.LOCK_TIMEOUT
        while time.monotonic() < deadline:
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
            except FileExistsError:
                time.sleep(self.LOCK_RETRY)
        log.warning("SignalRecords: could not acquire lock within %.1fs", self.LOCK_TIMEOUT)
        return False

    def _release(self):
        try:
            os.remove(self._lock_path)
        except OSError:
            pass

    # ── I/O ──────────────────────────────────────────────────────────────────

    def _load(self) -> Dict:
        if not os.path.exists(self._file):
            return {"version": 1, "last_updated": None, "records": {}}
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            log.error("SignalRecords: load error — %s", exc)
            return {"version": 1, "last_updated": None, "records": {}}

    def _save(self, data: Dict):
        data["last_updated"] = _now()
        tmp = self._file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp, self._file)
        except Exception as exc:
            log.error("SignalRecords: save error — %s", exc)
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _transact(self, fn) -> Any:
        """Acquire lock → load → fn(data) → save → release."""
        if not self._acquire():
            return None
        try:
            data   = self._load()
            result = fn(data)
            self._save(data)
            return result
        finally:
            self._release()

    def _restore_mem_state(self):
        """On startup, rebuild in-memory state for records that are still actively tracking."""
        try:
            data = self._load()
        except Exception:
            return
        for key, rec in data.get("records", {}).items():
            if rec.get("tracking_active") and not rec.get("tracking_complete"):
                mid  = int(key)
                zone = rec.get("zone")
                self._mem[mid] = {
                    "best_in_zone":  None,
                    "price_extreme": None,  # can't recover pre-zone extreme after restart
                    "zone":          zone,
                    "side":          rec.get("side", "BUY"),
                    "active":        True,
                    "entered_zone":  rec.get("price_entered_zone", False),
                }

    # ── static helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _in_zone(price: float, zone: List[float]) -> bool:
        return zone[0] <= price <= zone[1]

    @staticmethod
    def _is_better(side: str, candidate: float, best: Optional[float]) -> bool:
        """True if candidate is a better zone entry than current best."""
        if best is None:
            return True
        return candidate < best if side == "BUY" else candidate > best

    @staticmethod
    def _worst_edge(side: str, zone: List[float]) -> float:
        """Worst entry price in the zone: high for BUY, low for SELL."""
        return zone[1] if side == "BUY" else zone[0]

    # ── public API ────────────────────────────────────────────────────────────

    def register_signal(self, msg_id: int, side: str, signal_price: float) -> bool:
        """
        Create a record for this signal.
        Returns True if this bot created it (first to register), False if another
        bot already registered the same msg_id.
        """
        created = [False]

        def _op(data):
            key = str(msg_id)
            if key not in data["records"]:
                data["records"][key] = _new_record(msg_id, side, signal_price)
                created[0] = True
                log.info("SignalRecords: created record — msg_id=%s %s @ %.2f",
                         msg_id, side, signal_price)
            else:
                log.debug("SignalRecords: msg_id=%s already registered by another bot", msg_id)

        self._transact(_op)

        # Always start in-memory tracking immediately after registration
        # so price_extreme is captured from the first tick — even before the zone arrives.
        if msg_id not in self._mem:
            self._mem[msg_id] = {
                "best_in_zone":  None,
                "price_extreme": None,  # min price seen for BUY / max for SELL from signal receipt
                "zone":          None,  # not yet known; zone-based tracking starts in set_zone
                "side":          side,
                "active":        True,
                "entered_zone":  False,
            }

        return created[0]

    def set_zone(self, msg_id: int, zone_low: float, zone_high: float,
                 tp_levels: Optional[Dict] = None, sl_price: Optional[float] = None):
        """
        Store zone info and activate price tracking for this signal.
        Calculates signal_to_worst_edge from the stored signal_price.

        tp_levels  – {1: price, 2: price, …} or None if not yet known
        sl_price   – stop-loss price or None if not yet known
        """
        zone     = [zone_low, zone_high]
        side_ref = [None]

        # Snapshot the price extreme accumulated since register_signal (before zone was known)
        mem_state = self._mem.get(msg_id)
        price_extreme_snap = mem_state.get("price_extreme") if mem_state else None

        def _op(data):
            rec = data["records"].get(str(msg_id))
            if rec is None:
                log.warning("SignalRecords: set_zone for unknown msg_id=%s", msg_id)
                return
            side_ref[0] = rec["side"]

            rec["zone"] = zone
            if tp_levels is not None:
                rec["tp_levels"] = {str(k): v for k, v in tp_levels.items()}
            if sl_price is not None:
                rec["sl_price"] = sl_price

            # signal_to_worst_edge
            side  = rec["side"]
            worst = self._worst_edge(side, zone)
            dist  = round(rec["signal_price"] - worst, 2)
            loc   = "inside" if self._in_zone(rec["signal_price"], zone) else "outside"
            rec["signal_to_worst_edge"] = [dist, loc]

            # Commit the pre-zone price extreme (best price seen from signal to zone arrival)
            if price_extreme_snap is not None:
                rec["price_extreme_at_zone_arrival"] = price_extreme_snap

            rec["tracking_active"]      = True
            rec["tracking_started_at"]  = _now()
            rec["last_updated"]         = _now()

        self._transact(_op)

        if side_ref[0] is not None:
            if msg_id in self._mem:
                # Update zone in existing mem state (preserves price_extreme already tracked)
                self._mem[msg_id]["zone"] = zone
            else:
                # Fallback: create fresh mem state if somehow missing
                self._mem[msg_id] = {
                    "best_in_zone":  None,
                    "price_extreme": None,
                    "zone":          zone,
                    "side":          side_ref[0],
                    "active":        True,
                    "entered_zone":  False,
                }

    def update_price(self, msg_id: int, current_price: float):
        """
        Fast in-memory price update — NO disk I/O on normal ticks.
        Tracks:
          • whether price entered the zone (fires record_level_hit(LEVEL_ZONE) once)
          • running best price within zone (committed to disk on each TP level hit)

        Call this on every price tick from the monitoring loop.
        """
        state = self._mem.get(msg_id)
        if state is None or not state.get("active"):
            return

        # Track raw price extreme from the moment of signal receipt (pre-zone and post-zone)
        if self._is_better(state["side"], current_price, state.get("price_extreme")):
            state["price_extreme"] = current_price

        zone = state.get("zone")
        if zone is None:
            return  # zone-specific tracking not yet possible

        in_z = self._in_zone(current_price, zone)

        # Zone entry — triggers one disk write, then never again
        if in_z and not state.get("entered_zone"):
            state["entered_zone"] = True
            # Record LEVEL_ZONE in outcome_sequence (one I/O hit per signal)
            self.record_level_hit(msg_id, LEVEL_ZONE)

        # Update running best price (within zone only)
        if in_z:
            if self._is_better(state["side"], current_price, state.get("best_in_zone")):
                state["best_in_zone"] = current_price

    def record_level_hit(self, msg_id: int, level: int):
        """
        Record that a price level was hit and commit the current best-in-zone snapshot.

        level: LEVEL_SL=-1, LEVEL_ZONE=0, 1=TP1 … 5=TP5

        Deduplication: if the same level was recorded by any bot in the last 2 seconds,
        the call is a no-op (prevents double-recording when two bots hit the same level
        at nearly the same time).

        Auto-finalizes on SL (-1) or TP5 (5).
        """
        mem          = self._mem.get(msg_id, {})
        best_snap    = mem.get("best_in_zone")
        entered_zone = mem.get("entered_zone", False)

        def _op(data):
            rec = data["records"].get(str(msg_id))
            if rec is None or rec.get("tracking_complete"):
                return

            seq        = rec.setdefault("outcome_sequence", [])
            level_ts   = rec.setdefault("_level_times", [])

            # Cross-bot dedup: skip if same level was recorded < 2 s ago
            if seq and seq[-1] == level and level_ts:
                try:
                    age = (datetime.now() -
                           datetime.fromisoformat(level_ts[-1])).total_seconds()
                    if age < 2.0:
                        return
                except Exception:
                    pass

            # Commit zone-entry flag
            if entered_zone and not rec.get("price_entered_zone"):
                rec["price_entered_zone"] = True

            # For TP levels: snapshot best-in-zone before this TP, then reset
            if level >= 1 and best_snap is not None:
                rec.setdefault("best_prices_per_tp", []).append(
                    [best_snap, f"tp{level}"]
                )
                if msg_id in self._mem:
                    self._mem[msg_id]["best_in_zone"] = None   # reset for next interval

            # LEVEL_ZONE: only record once (skip if already first in sequence)
            if level == LEVEL_ZONE and LEVEL_ZONE in seq:
                return

            seq.append(level)
            level_ts.append(_now())
            rec["last_updated"] = _now()

            # Auto-finalize
            if level == LEVEL_SL or level == 5:
                _finalize_record(rec)
                self._mem.pop(msg_id, None)

        self._transact(_op)

    def finalize(self, msg_id: int):
        """Manually mark tracking as complete (e.g. on 24 h timeout)."""
        def _op(data):
            rec = data["records"].get(str(msg_id))
            if rec:
                _finalize_record(rec)
        self._transact(_op)
        self._mem.pop(msg_id, None)

    def record_bot_entry(self, msg_id: int, entered: bool,
                         entry_type: Optional[str] = None,
                         entry_price: Optional[float] = None,
                         ticket: Optional[int] = None):
        """
        Record whether this bot entered the trade for this signal.
        Each bot writes to its own key inside bot_entries.
        """
        def _op(data):
            rec = data["records"].get(str(msg_id))
            if rec is None:
                return
            rec.setdefault("bot_entries", {})[str(self._magic)] = {
                "entered":     entered,
                "entry_type":  entry_type,
                "entry_price": entry_price,
                "ticket":      ticket,
            }
            rec["last_updated"] = _now()
        self._transact(_op)

    def record_entry_event(self, msg_id: int, event_type: str, **kwargs):
        """
        Append an entry quality event to this signal's record.
        Mirrors record_entry_stat() in bot_v2.py — both should be called together
        until entry_stats_*.json is fully replaced.

        event_type examples:
            signal_buffered, zone_from_edit, limit_placed, limit_fill,
            limit_timeout, limit_invalidated, market_entry_in_zone,
            zone_wait_timeout, limit_placement_failed
        """
        def _op(data):
            rec = data["records"].get(str(msg_id))
            if rec is None:
                return
            event = {"time": _now(), "type": event_type, **kwargs}
            events = rec.setdefault("entry_events", [])
            events.append(event)
            rec["entry_events"] = events[-50:]   # cap at 50 per signal
            rec["last_updated"] = _now()
        self._transact(_op)

    def update_tp_sl(self, msg_id: int, tp_levels: Dict, sl_price: float):
        """Update TP/SL levels on an existing record (e.g. when edit arrives after entry)."""
        def _op(data):
            rec = data["records"].get(str(msg_id))
            if rec is None:
                return
            rec["tp_levels"] = {str(k): v for k, v in tp_levels.items()}
            rec["sl_price"]  = sl_price
            rec["last_updated"] = _now()
        self._transact(_op)

    # ── read helpers ─────────────────────────────────────────────────────────

    def get_record(self, msg_id: int) -> Optional[Dict]:
        """Return a copy of a single record (excludes internal _* keys)."""
        data = self._load()
        rec  = data["records"].get(str(msg_id))
        if rec is None:
            return None
        return {k: v for k, v in rec.items() if not k.startswith("_")}

    def get_all_records(self) -> List[Dict]:
        """All records sorted newest first, internal keys stripped."""
        data = self._load()
        out  = [
            {k: v for k, v in rec.items() if not k.startswith("_")}
            for rec in data["records"].values()
        ]
        out.sort(key=lambda r: r.get("signal_time", ""), reverse=True)
        return out

    def get_active_msg_ids(self) -> List[int]:
        """Return msg_ids currently being tracked in memory."""
        return [mid for mid, s in self._mem.items() if s.get("active")]

    def get_active_states(self) -> Dict[int, Dict]:
        """Return in-memory state dict for all active signals, keyed by msg_id.
        Useful for iterating over signals that don't yet have a ticket (pre-entry)."""
        return {mid: state for mid, state in self._mem.items() if state.get("active")}


# ── module helpers ────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_record(msg_id: int, side: str, signal_price: float) -> Dict:
    return {
        # ── signal info ──
        "msg_id":               msg_id,
        "side":                 side,             # "BUY" or "SELL"
        "signal_time":          _now(),
        "signal_price":         signal_price,     # market price at BUY/SELL NOW

        # ── zone info (filled when zone is received) ──
        "zone":                 None,             # [zone_low, zone_high]
        "signal_to_worst_edge": None,             # [dist_to_worst_edge, "inside"|"outside"]
        "tp_levels":            None,             # {"1": price, "2": price, ...}
        "sl_price":             None,

        # ── zone price tracking ──
        "price_entered_zone":          False,
        "price_extreme_at_zone_arrival": None,    # best price seen from signal receipt → zone arrival
        "best_prices_per_tp":          [],        # [[best_price, "tp1"], ...]
        "outcome_sequence":            [],        # [-1, 0, 1, 2, 3, 4, 5]

        # ── bot entries ──
        "bot_entries":          {},               # {magic_str: {entered, entry_type, ...}}

        # ── entry quality events ──
        "entry_events":         [],               # flat list, same shape as entry_stats entries

        # ── tracking state ──
        "tracking_active":      False,
        "tracking_complete":    False,
        "tracking_started_at":  None,
        "tracking_ended_at":    None,
        "last_updated":         _now(),

        # internal (stripped from get_record/get_all_records output)
        "_level_times":         [],               # timestamps for cross-bot level dedup
    }


def _finalize_record(rec: Dict):
    rec["tracking_active"]   = False
    rec["tracking_complete"] = True
    rec["tracking_ended_at"] = _now()
    rec["last_updated"]      = _now()
