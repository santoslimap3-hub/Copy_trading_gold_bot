#!/usr/bin/env python3
"""
Channel Observer — Lightweight Signal Tracker (Observe Only)
============================================================
Monitors a second Telegram signals channel and records detailed tick-by-tick
price movement for each signal. No trades are placed.

For each signal, records:
  - Side (BUY/SELL), price at arrival, entry zone, TP/SL levels (if provided)
  - A price_trail of actual prices (only when price moves ≥$0.50)
  - Outcome: WIN ($15 above worst entry) or LOSS ($8 below worst entry)

Connects to the same MT5 terminal as bot_v2.py for price data.
Uses its own Telethon session to avoid conflicts.

Resource-conscious: ~45MB RAM, 1 tick/second, minimal disk I/O.
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime
from typing import Optional, Dict, Tuple, List

import MetaTrader5 as mt5
from telethon import TelegramClient, events

# ===================== CONFIGURATION =====================
# Telegram
API_ID = 34597981
API_HASH = "2cd59609b6cacb56da261e43fdb897ea"
CHANNEL_ID_2 = -1003142865169
SESSION_FILE = "channel_observer_session"

# Symbol
SYMBOL = "XAUUSD"

# MT5 — same terminal shared with bot_v2
MT5_PATH = r"C:\MT5_1\terminal64.exe"

# Tick polling
TICK_INTERVAL = 1.0        # seconds between price checks
PRICE_DELTA_MIN = 0.50     # minimum price change to record in trail

# Outcome thresholds (from worst entry)
WIN_DISTANCE = 15.0        # dollars above worst entry → WIN
LOSS_DISTANCE = 8.0        # dollars below worst entry → LOSS

# Resource limits
MAX_ACTIVE_SIGNALS = 20    # force-finalize oldest if exceeded
SIGNAL_TIMEOUT = 86400     # 24 hours hard timeout
FLUSH_INTERVAL = 300       # seconds between periodic disk flushes (5 min)

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data")
DATA_FILE = os.path.join(DATA_DIR, "channel2_signal_records.json")
LOCK_FILE = DATA_FILE + ".lock"

# ===================== LOGGING =====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("observer")

# ===================== REGEX PATTERNS =====================
# Channel format: "Buy zone" / "Sell zone" (also accepts BUY NOW, SELL NOW, etc.)
_RE_SIDE = re.compile(
    r"\b(BUY\s*ZONE|SELL\s*ZONE|BUY\s*NOW|SELL\s*NOW|BUY|SELL|LONG|SHORT)\b",
    re.IGNORECASE,
)
_RE_ENTRY_ZONE = re.compile(
    r"(\d{4,5}(?:\.\d{1,2})?)\s*[-\u2013\u2014]\s*(\d{4,5}(?:\.\d{1,2})?)"
)

# ===================== MT5 EXECUTOR =====================
_mt5_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5obs")


async def run_mt5(func, *args):
    """Run a blocking MT5 function in a thread so the event loop stays responsive."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_mt5_executor, func, *args)


# ===================== MT5 INIT =====================

def ensure_mt5():
    """Initialize MT5 connection (shared terminal with other bots)."""
    log.info("Initializing MT5 at: %s", MT5_PATH)
    if not mt5.initialize(path=MT5_PATH):
        log.error("MT5 initialization FAILED: %s", mt5.last_error())
        sys.exit(1)

    log.info("MT5 initialized successfully")
    account = mt5.account_info()
    if account:
        log.info("  Account: %s | Balance: $%.2f", account.login, account.balance)

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log.error("Cannot get %s tick data — is symbol available?", SYMBOL)
        sys.exit(1)
    log.info("  %s bid=%.2f ask=%.2f", SYMBOL, tick.bid, tick.ask)


async def get_tick():
    """Fetch current tick asynchronously."""
    return await run_mt5(mt5.symbol_info_tick, SYMBOL)


# ===================== SIGNAL PARSING =====================

def parse_side(text: str) -> Optional[str]:
    """Detect BUY or SELL from message text. Returns 'BUY', 'SELL', or None."""
    match = _RE_SIDE.search(text)
    if not match:
        return None
    raw = match.group(1).upper().strip()
    if "BUY" in raw or raw == "LONG":
        return "BUY"
    if "SELL" in raw or raw == "SHORT":
        return "SELL"
    return None


def parse_entry_zone(text: str) -> Optional[Tuple[float, float]]:
    """Parse entry zone. Returns (zone_low, zone_high) or None."""
    match = _RE_ENTRY_ZONE.search(text)
    if not match:
        return None
    val1, val2 = float(match.group(1)), float(match.group(2))
    if val1 < 1000 or val2 < 1000:
        return None
    zone_high, zone_low = max(val1, val2), min(val1, val2)
    width = zone_high - zone_low
    if width > 30 or width < 0.5:
        return None
    return (zone_low, zone_high)




# ===================== THRESHOLD CALCULATION =====================

def compute_worst_entry(side: str, entry_zone: Optional[Tuple[float, float]],
                        signal_bid: float, signal_ask: float) -> float:
    """Compute worst entry price (zone edge or signal price)."""
    if entry_zone:
        # Worst entry for BUY = zone_high (buying at the top of zone)
        # Worst entry for SELL = zone_low (selling at the bottom of zone)
        return entry_zone[1] if side == "BUY" else entry_zone[0]
    return signal_ask if side == "BUY" else signal_bid


def compute_thresholds(side: str, worst_entry: float) -> Tuple[float, float]:
    """Returns (win_threshold, loss_threshold) from worst entry."""
    if side == "BUY":
        return (worst_entry + WIN_DISTANCE, worst_entry - LOSS_DISTANCE)
    else:
        return (worst_entry - WIN_DISTANCE, worst_entry + LOSS_DISTANCE)


# ===================== PERSISTENCE =====================

def _acquire_lock() -> bool:
    """Acquire file lock (OS-level exclusive create)."""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            time.sleep(0.03)
    log.warning("Could not acquire lock within 5s")
    return False


def _release_lock():
    """Release file lock."""
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


def load_data() -> Dict:
    """Load JSON data file."""
    if not os.path.exists(DATA_FILE):
        return {"version": 1, "last_updated": None, "records": {}}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error("Load error: %s", e)
        return {"version": 1, "last_updated": None, "records": {}}


def save_data(data: Dict):
    """Atomic save: write to tmp then replace."""
    data["last_updated"] = datetime.now().isoformat()
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, DATA_FILE)


def flush_signal_to_disk(sig: Dict):
    """Write a single signal record to the data file."""
    if not _acquire_lock():
        log.warning("Skipping flush — lock unavailable")
        return
    try:
        data = load_data()
        data["records"][str(sig["msg_id"])] = _build_disk_record(sig)
        save_data(data)
    finally:
        _release_lock()


def flush_all_active_to_disk(active: Dict):
    """Periodic flush: write all active signals to disk for crash recovery."""
    if not active:
        return
    if not _acquire_lock():
        log.warning("Skipping periodic flush — lock unavailable")
        return
    try:
        data = load_data()
        for msg_id, sig in active.items():
            data["records"][str(msg_id)] = _build_disk_record(sig)
        save_data(data)
        log.info("Periodic flush: %d active signals written to disk", len(active))
    finally:
        _release_lock()


def _build_disk_record(sig: Dict) -> Dict:
    """Convert in-memory signal state to JSON-serializable disk record."""
    return {
        "msg_id": sig["msg_id"],
        "side": sig["side"],
        "signal_time": sig["signal_time"],
        "signal_text": sig["signal_text"],
        "signal_price_bid": sig["signal_price_bid"],
        "signal_price_ask": sig["signal_price_ask"],
        "entry_zone": sig["entry_zone"],
        "worst_entry": sig["worst_entry"],
        "win_threshold": sig["win_threshold"],
        "loss_threshold": sig["loss_threshold"],
        "price_trail": sig["price_trail"],
        "last_recorded_price": sig["last_recorded_price"],
        "outcome": sig["outcome"],
        "outcome_time": sig["outcome_time"],
        "outcome_price": sig["outcome_price"],
        "tracking_active": sig["tracking_active"],
        "tracking_started_at": sig["tracking_started_at"],
        "tracking_ended_at": sig["tracking_ended_at"],
        "zone_updated_via_edit": sig["zone_updated_via_edit"],
    }


# ===================== IN-MEMORY STATE =====================

# Active signals being tracked: {msg_id: dict}
_active: Dict[int, Dict] = {}


def _create_signal(msg_id: int, side: str, text: str, bid: float, ask: float,
                   entry_zone: Optional[Tuple[float, float]]) -> Dict:
    """Create a new in-memory signal record."""
    worst = compute_worst_entry(side, entry_zone, bid, ask)
    win_thresh, loss_thresh = compute_thresholds(side, worst)
    now = datetime.now().isoformat()
    price = bid if side == "BUY" else ask

    return {
        "msg_id": msg_id,
        "side": side,
        "signal_time": now,
        "signal_time_ts": time.time(),
        "signal_text": text,
        "signal_price_bid": bid,
        "signal_price_ask": ask,
        "entry_zone": list(entry_zone) if entry_zone else None,
        "worst_entry": worst,
        "win_threshold": win_thresh,
        "loss_threshold": loss_thresh,
        "price_trail": [[time.time(), price]],
        "last_recorded_price": price,
        "outcome": None,
        "outcome_time": None,
        "outcome_price": None,
        "tracking_active": True,
        "tracking_started_at": now,
        "tracking_ended_at": None,
        "zone_updated_via_edit": False,
    }


def restore_active_from_disk():
    """On startup, restore signals that were still active when we last shut down."""
    data = load_data()
    restored = 0
    for key, rec in data.get("records", {}).items():
        if not rec.get("tracking_active", False):
            continue
        msg_id = rec["msg_id"]
        # Rebuild in-memory state
        sig = {
            "msg_id": msg_id,
            "side": rec["side"],
            "signal_time": rec["signal_time"],
            "signal_time_ts": _parse_iso_to_ts(rec["signal_time"]),
            "signal_text": rec.get("signal_text", ""),
            "signal_price_bid": rec["signal_price_bid"],
            "signal_price_ask": rec["signal_price_ask"],
            "entry_zone": rec.get("entry_zone"),
            "worst_entry": rec["worst_entry"],
            "win_threshold": rec["win_threshold"],
            "loss_threshold": rec["loss_threshold"],
            "price_trail": rec.get("price_trail", []),
            "last_recorded_price": rec.get("last_recorded_price", rec["signal_price_bid"]),
            "outcome": None,
            "outcome_time": None,
            "outcome_price": None,
            "tracking_active": True,
            "tracking_started_at": rec["tracking_started_at"],
            "tracking_ended_at": None,
            "zone_updated_via_edit": rec.get("zone_updated_via_edit", False),
        }
        # Use last price from trail if available
        if sig["price_trail"]:
            sig["last_recorded_price"] = sig["price_trail"][-1][1]
        _active[msg_id] = sig
        restored += 1
        if len(_active) >= MAX_ACTIVE_SIGNALS:
            break

    if restored:
        log.info("Restored %d active signals from disk", restored)


def _parse_iso_to_ts(iso_str: str) -> float:
    """Convert ISO datetime string to unix timestamp."""
    try:
        return datetime.fromisoformat(iso_str).timestamp()
    except (ValueError, TypeError):
        return time.time()


# ===================== SIGNAL FINALIZATION =====================

def finalize_signal(msg_id: int, outcome: str, price: float):
    """Mark a signal as complete and flush to disk."""
    sig = _active.get(msg_id)
    if not sig:
        return
    sig["outcome"] = outcome
    sig["outcome_time"] = datetime.now().isoformat()
    sig["outcome_price"] = price
    sig["tracking_active"] = False
    sig["tracking_ended_at"] = datetime.now().isoformat()
    # Append final price to trail
    sig["price_trail"].append([time.time(), price])

    log.info(
        "SIGNAL FINALIZED: msg_id=%d side=%s outcome=%s price=%.2f worst_entry=%.2f trail_len=%d",
        msg_id, sig["side"], outcome, price, sig["worst_entry"], len(sig["price_trail"]),
    )

    flush_signal_to_disk(sig)
    del _active[msg_id]


def force_finalize_oldest():
    """Force-finalize the oldest active signal to make room."""
    if not _active:
        return
    oldest_id = min(_active, key=lambda k: _active[k]["signal_time_ts"])
    log.warning("Force-finalizing oldest signal %d (max active reached)", oldest_id)
    finalize_signal(oldest_id, "EVICTED", _active[oldest_id]["last_recorded_price"])


# ===================== TICK MONITOR =====================

async def tick_monitor():
    """Main tick loop: poll price every TICK_INTERVAL, update all active signals."""
    last_flush = time.time()

    while True:
        await asyncio.sleep(TICK_INTERVAL)

        if not _active:
            continue

        tick = await get_tick()
        if tick is None:
            log.warning("Tick fetch returned None — MT5 might be disconnected")
            continue

        bid, ask = float(tick.bid), float(tick.ask)
        now = time.time()

        # Sanity checks (same as bot_v2)
        if bid < 100 or ask < 100:
            continue
        if (ask - bid) > 5.0:
            continue

        to_finalize: List[Tuple[int, str, float]] = []

        for msg_id, sig in _active.items():
            price = bid if sig["side"] == "BUY" else ask

            # Record price if it moved enough
            delta = abs(price - sig["last_recorded_price"])
            if delta >= PRICE_DELTA_MIN:
                sig["price_trail"].append([now, price])
                sig["last_recorded_price"] = price

            # Check WIN/LOSS thresholds
            if sig["side"] == "BUY":
                if price >= sig["win_threshold"]:
                    to_finalize.append((msg_id, "WIN", price))
                elif price <= sig["loss_threshold"]:
                    to_finalize.append((msg_id, "LOSS", price))
            else:  # SELL
                if price <= sig["win_threshold"]:
                    to_finalize.append((msg_id, "WIN", price))
                elif price >= sig["loss_threshold"]:
                    to_finalize.append((msg_id, "LOSS", price))

            # Check timeout
            if (now - sig["signal_time_ts"]) >= SIGNAL_TIMEOUT:
                to_finalize.append((msg_id, "TIMEOUT", price))

        # Finalize outside the iteration
        for msg_id, outcome, price in to_finalize:
            if msg_id in _active:  # guard against double-finalize
                finalize_signal(msg_id, outcome, price)

        # Periodic flush for crash recovery
        if (now - last_flush) >= FLUSH_INTERVAL:
            flush_all_active_to_disk(_active)
            last_flush = now


# ===================== TELEGRAM HANDLERS =====================

def register_handlers(client: TelegramClient):
    """Register Telegram event handlers for the second channel."""

    @client.on(events.NewMessage(chats=CHANNEL_ID_2))
    async def on_new_message(event):
        text = event.message.text or ""
        msg_id = event.message.id

        side = parse_side(text)
        if not side:
            return  # Not a trading signal

        if msg_id in _active:
            log.info("Signal %d already tracked, skipping", msg_id)
            return

        # Get current price
        tick = await get_tick()
        if tick is None:
            log.warning("Cannot get tick for signal %d — skipping", msg_id)
            return

        bid, ask = float(tick.bid), float(tick.ask)
        entry_zone = parse_entry_zone(text)

        # Enforce max active limit
        if len(_active) >= MAX_ACTIVE_SIGNALS:
            force_finalize_oldest()

        sig = _create_signal(msg_id, side, text, bid, ask, entry_zone)
        _active[msg_id] = sig

        log.info(
            "NEW SIGNAL: msg_id=%d side=%s bid=%.2f ask=%.2f zone=%s worst=%.2f win=%.2f loss=%.2f",
            msg_id, side, bid, ask, entry_zone,
            sig["worst_entry"], sig["win_threshold"], sig["loss_threshold"],
        )

    @client.on(events.MessageEdited(chats=CHANNEL_ID_2))
    async def on_message_edited(event):
        msg_id = event.message.id
        if msg_id not in _active:
            return  # Not a tracked signal

        text = event.message.text or ""
        sig = _active[msg_id]
        updated = False

        # Re-parse zone (only field we track from this channel)
        new_zone = parse_entry_zone(text)
        if new_zone and new_zone != sig.get("entry_zone"):
            sig["entry_zone"] = list(new_zone)
            updated = True

        if updated:
            # Recalculate thresholds with new zone
            sig["worst_entry"] = compute_worst_entry(
                sig["side"],
                tuple(sig["entry_zone"]) if sig["entry_zone"] else None,
                sig["signal_price_bid"],
                sig["signal_price_ask"],
            )
            sig["win_threshold"], sig["loss_threshold"] = compute_thresholds(
                sig["side"], sig["worst_entry"]
            )
            sig["zone_updated_via_edit"] = True
            sig["signal_text"] = text  # Update to latest version

            log.info(
                "SIGNAL UPDATED (edit): msg_id=%d zone=%s worst=%.2f win=%.2f loss=%.2f",
                msg_id, sig["entry_zone"],
                sig["worst_entry"], sig["win_threshold"], sig["loss_threshold"],
            )


# ===================== GRACEFUL SHUTDOWN =====================

_shutdown_event = asyncio.Event()


def _handle_shutdown(signum, frame):
    """Signal handler for graceful shutdown."""
    log.info("Shutdown signal received (%s) — flushing active signals...", signum)
    flush_all_active_to_disk(_active)
    log.info("Flush complete. Exiting.")
    sys.exit(0)


# ===================== MAIN =====================

async def main():
    # Ensure data directory exists
    os.makedirs(DATA_DIR, exist_ok=True)

    # Initialize MT5
    ensure_mt5()

    # Restore active signals from previous run
    restore_active_from_disk()

    # Set up Telegram client
    client = TelegramClient(
        os.path.join(SCRIPT_DIR, SESSION_FILE),
        API_ID,
        API_HASH,
    )

    register_handlers(client)

    log.info("Starting Telegram client...")
    await client.start()

    me = await client.get_me()
    log.info("Logged in as: %s (ID: %d)", me.first_name, me.id)
    log.info("Monitoring channel: %d", CHANNEL_ID_2)
    log.info("Active signals restored: %d", len(_active))
    log.info("Config: tick=%.1fs, delta=$%.2f, win=$%.1f, loss=$%.1f",
             TICK_INTERVAL, PRICE_DELTA_MIN, WIN_DISTANCE, LOSS_DISTANCE)

    # Launch tick monitor
    asyncio.create_task(tick_monitor())

    log.info("Channel Observer running. Press Ctrl+C to stop.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    # Register shutdown handlers
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        flush_all_active_to_disk(_active)
        log.info("Shutdown complete.")
    finally:
        mt5.shutdown()
