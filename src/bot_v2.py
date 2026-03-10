#!/usr/bin/env python3
"""
Gold Trading Bot - Set & Forget TP1 Strategy (Robust Edition)
Connects to Telegram channel for gold trading signals and executes trades automatically.

STRATEGY:
  1. Signal received → Enter immediately with failsafe SL ($8) and failsafe TP ($3)
  2. Wait for message edit with real SL and TP1 values
  3. Parse TP1 and SL from the edited message, update the trade
  4. FORGET — let the broker handle exit via SL/TP. No further management.

Priority: Never miss a trade. Designed to run unattended for weeks/months.

Includes full health monitoring, connection recovery, message polling fallback,
and MT5 thread pool executor for robustness.
"""

import asyncio
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
import MetaTrader5 as mt5
from telethon import TelegramClient, events
from telethon.errors.common import TypeNotFoundError
from typing import Optional, Dict, Tuple

# Import custom modules for signal persistence, session management, and monitoring
from signal_queue import SignalQueue
from session_manager import SessionManager
from reconnect_monitor import ReconnectMonitor
from trade_outcome_tracker import TradeOutcomeTracker

# ===================== CONFIGURATION =====================
# Telegram
API_ID = 34597981
API_HASH = "2cd59609b6cacb56da261e43fdb897ea"
CHANNEL_ID = -1003349563414  # Main trading channel (EGN GOLD)
SESSION_FILE = "trading_bot_session"

# Trading
SYMBOL = "XAUUSD"
MAGIC = 777
RISK_PCT = 0.1  # 10% risk per trade
FAILSAFE_SL_DISTANCE = 8.0  # failsafe SL: $8 away from entry
FAILSAFE_TP_DISTANCE = 3.0  # failsafe TP: $3 away from entry (until real TP1 arrives)

# Limit Order Entry Strategy
LIMIT_ORDER_TIMEOUT = 14400  # seconds: cancel unfilled limit orders after this (4 hours - generous to let zone fill)
ZONE_WAIT_TIMEOUT = 120      # seconds: wait for zone via edit before falling back to market (2 min)
ENTRY_STRATEGY = "LIMIT_ZONE" # "LIMIT_ZONE" = limit at zone edge | "MARKET" = immediate market (old behavior)

# Telegram filters
ALLOWED_CHAT_IDS = {CHANNEL_ID}
DEBUG_LOG_ALL_MESSAGES = True

# Logging
LOG_LEVEL = "INFO"  # Options: "DEBUG" (verbose), "INFO" (normal), "WARN" (quiet - errors only)

# ===================== STATE =====================
# Track open positions: message_id -> ticket
position_map: Dict[int, int] = {}

# Track entry prices: ticket -> entry_price
entry_prices: Dict[int, float] = {}

# Track whether real SL/TP has been applied (from message edit): ticket -> bool
tp_sl_updated: Dict[int, bool] = {}

# Limit order tracking: msg_id -> {side, order_ticket, limit_price, zone_high, zone_low, lot, created_at, ...}
_pending_limit_orders: Dict[int, Dict] = {}

# Buffered signals waiting for zone: msg_id -> {side, text, created_at, channel_name, source}
_buffered_signals: Dict[int, Dict] = {}

# Signal attempt log: list of (timestamp, signal, result, details)
signal_log: list = []

# Initialize external modules
signal_queue: Optional[SignalQueue] = None
session_manager: Optional[SessionManager] = None
reconnect_monitor: Optional[ReconnectMonitor] = None
outcome_tracker: Optional[TradeOutcomeTracker] = None

# ===================== HEALTH MONITORING =====================
# Global Telegram client reference (set in main(), used by health monitor for active checks)
_telegram_client = None

# Bot health metrics
bot_start_time: float = 0
messages_received: int = 0
messages_processed: int = 0
messages_ignored: int = 0
trades_executed: int = 0
trades_failed: int = 0
mt5_connection_losses: int = 0
telegram_connection_losses: int = 0
last_message_time: float = 0
last_telegram_activity: float = 0
last_mt5_check: float = 0
mt5_connected: bool = False
telegram_connected: bool = False
heartbeat_counter: int = 0

# Track processed message IDs to avoid duplicate processing (push events + polling)
_processed_msg_ids: set = set()
_MAX_PROCESSED_IDS = 500  # Cap the set size to prevent memory leak
POLLING_INTERVAL = 0.2  # seconds between polling checks (was 0.5 — reduced for faster fallback)

# Thread pool for running blocking MT5 calls without freezing the async event loop
_mt5_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5")

# Pre-compiled regex patterns for signal parsing (avoids re-compilation on every call)
_RE_TP_LEVELS = re.compile(r"TP\s*(\d+)\s+([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_RE_STOP_LOSS = re.compile(r"SL\s+([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_RE_TP1 = re.compile(r"TP\s*1\s+([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)

# Fallback: bare "TP 5164" without level number (channel sometimes sends abbreviated TP)
_RE_TP_BARE = re.compile(r"\bTP\s+(\d{4,5}(?:\.\d{1,2})?)\b", re.IGNORECASE)

# Entry zone pattern: matches "5396 - 5392" or "5396-5392" or "5396\u20135392"
_RE_ENTRY_ZONE = re.compile(r"(\d{4,5}(?:\.\d{1,2})?)\s*[-\u2013\u2014]\s*(\d{4,5}(?:\.\d{1,2})?)")

# Trade closure patterns: "TP1 HIT", "TP1 ,2,3,4 HIT", "SL HIT"
# NOTE: "Profit" and "Close profit" are NOT closures - channel uses those to say trade is positive
_RE_TRADE_CLOSURE = re.compile(
    r"(?:TP\s*\d[\s,\d]*\s*HIT|SL\s+HIT)",
    re.IGNORECASE | re.MULTILINE,
)

# Pre-built separator string (avoid "=" * 70 allocation on every log call)
_LOG_SEP = "=" * 70

# Cached filling mode (symbol filling mode never changes during a session)
_cached_filling_mode: Optional[int] = None

# Maximum consecutive reconnection failures before self-restart
MAX_CONSECUTIVE_FAILURES = 10
# Flag to signal the bot should do a full self-restart
_restart_requested = False

# ===================== MT5 CACHE (refreshed every 5s in background) =====================
# Caches symbol_info and account_info to avoid redundant MT5 calls in the hot trade path
_cached_symbol_info = None
_cached_account_info = None
_cache_last_refresh: float = 0
_CACHE_REFRESH_INTERVAL = 5.0  # seconds


def refresh_mt5_cache():
    """Refresh cached MT5 data. Called from background task and at startup."""
    global _cached_symbol_info, _cached_account_info, _cache_last_refresh
    _cached_symbol_info = mt5.symbol_info(SYMBOL)
    _cached_account_info = mt5.account_info()
    _cache_last_refresh = time.time()


def get_cached_symbol_info():
    """Get symbol info from cache, refreshing if stale."""
    if _cached_symbol_info is None or (time.time() - _cache_last_refresh) > _CACHE_REFRESH_INTERVAL:
        refresh_mt5_cache()
    return _cached_symbol_info


def get_cached_account_info():
    """Get account info from cache, refreshing if stale."""
    if _cached_account_info is None or (time.time() - _cache_last_refresh) > _CACHE_REFRESH_INTERVAL:
        refresh_mt5_cache()
    return _cached_account_info


async def run_mt5(func, *args):
    """Run a blocking MT5 function in a thread so the event loop stays responsive."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_mt5_executor, func, *args)


def is_market_open() -> bool:
    """Check whether XAUUSD market is currently open for new trades.
    Returns False when the broker only allows closing (retcode 10015 scenario)."""
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        return False
    # trade_mode values: SYMBOL_TRADE_MODE_DISABLED=0, SYMBOL_TRADE_MODE_LONGONLY=1,
    #   SYMBOL_TRADE_MODE_SHORTONLY=2, SYMBOL_TRADE_MODE_CLOSEONLY=3, SYMBOL_TRADE_MODE_FULL=4
    if info.trade_mode == 3:  # CLOSEONLY
        return False
    if info.trade_mode == 0:  # DISABLED
        return False
    return True


# ===================== HELPERS =====================

def log(msg: str, level: str = "INFO"):
    """Print with timestamp and level, respecting LOG_LEVEL setting"""
    levels = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
    current_priority = levels.get(LOG_LEVEL, 1)
    msg_priority = levels.get(level, 1)

    if msg_priority >= current_priority:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            print(f"[{ts}] [{level}] {msg}", flush=True)
        except UnicodeEncodeError:
            msg_ascii = msg.encode('ascii', 'ignore').decode('ascii')
            print(f"[{ts}] [{level}] {msg_ascii}", flush=True)


def get_error_message(retcode: int) -> str:
    """Map MT5 return codes to human-readable messages"""
    error_map = {
        1: "Generic error",
        2: "Invalid request",
        3: "Invalid order volume",
        4: "Invalid order price",
        5: "Invalid stops",
        6: "Trade disabled",
        7: "Market closed",
        8: "No money",
        9: "Price changed",
        10: "Requote",
        11: "Order expired",
        12: "Order cancelled",
        13: "Invalid response",
        14: "Invalid request",
        15: "Request timeout",
        16: "Invalid request repeat",
        17: "Reference error",
        18: "Unknown order",
        19: "Order duplicate",
        20: "Trade busy",
        21: "No connection",
        22: "No money",
        23: "Too frequent requests",
        24: "Malfunctional price",
        25: "Broker busy",
        26: "Account suspended",
        27: "Account forbidden",
        28: "Unknown symbol",
        29: "Wrong order type",
        30: "Wrong order size",
        31: "Wrong order price",
        32: "Wrong stop level",
        33: "Wrong filling",
        34: "Request rejected",
        35: "Order partial filled",
        10004: "Trade disabled by administrator",
        10005: "Trade forbidden by another manager",
        10006: "Trade disabled by experts",
        10009: "Request in progress",
        10011: "Order locked",
        10012: "Only buy allowed",
        10013: "Only sell allowed",
        10014: "Position by symbol locked",
        10015: "Close only allowed",
        10016: "Fifo rule restrictions",
        10017: "Account disabled",
        10018: "Pending orders limit exceeded",
        10019: "Order locked for modification",
        10020: "Order locked for deletion",
        10021: "Order too close to market",
        10022: "Pending orders count exceeded",
        10023: "Hedge operations prohibited",
        10024: "Prohibited operation",
        10025: "Invalid order state",
        10026: "Order state changed",
        10027: "AutoTrading DISABLED by client - enable in Terminal settings!",
        10028: "Trade context busy",
        10029: "Orders limit exceeded",
        10030: "Volume limit exceeded",
        10031: "Orders on symbol limit",
        10032: "Invalid expiration",
        10033: "Trade type invalid",
        10034: "Wrong color",
        10035: "Disabled by client",
        10036: "Permission denied",
        10038: "Order closed by system",
        10039: "Order closed by broker",
        10040: "Invalid take profit",
        10041: "Invalid stop loss",
        10042: "Invalid magic",
        10043: "Invalid expiration type",
        10044: "Too many requests",
        10047: "Order not found",
        10048: "Order status changed",
        10049: "Order info not available",
        10050: "Depth of market not available",
    }
    return error_map.get(retcode, f"Unknown error code {retcode}")


def check_autotrading_status():
    """Check if AutoTrading is enabled and warn if disabled"""
    log("Checking AutoTrading status...", "DEBUG")

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log("Cannot check AutoTrading - no tick data", "WARN")
        return

    account = mt5.account_info()
    if account:
        if account.trade_allowed:
            log("AutoTrading is ENABLED and allowed", "INFO")
        else:
            log("AutoTrading is DISABLED - trading is forbidden!", "ERROR")
            log("   FIX: Enable AutoTrading in Terminal > Tools > Options > Expert Advisors", "ERROR")
            log("   Also enable 'Allow automated trading' checkbox", "ERROR")
    else:
        log("Cannot check AutoTrading status", "WARN")


def ensure_mt5_connection():
    """Initialize MT5 if not connected.
    
    Supports multi-account setups via the MT5_PATH environment variable.
    Set MT5_PATH to the full path of terminal64.exe for the desired account.
    Example: set MT5_PATH=C:\\MT5_Account2\\terminal64.exe
    If not set, uses the default MT5 installation.
    """
    global mt5_connected, mt5_connection_losses

    log("Attempting MT5 initialization...", "DEBUG")

    mt5_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    log(f"Initializing MT5 at: {mt5_path}", "INFO")
    init_ok = mt5.initialize(path=mt5_path)

    if not init_ok:
        log("MT5 initialization FAILED", "ERROR")
        log(f"   Last error: {mt5.last_error()}", "ERROR")
        mt5_connected = False
        mt5_connection_losses += 1
        sys.exit(1)

    log("MT5 initialized successfully", "INFO")
    mt5_connected = True

    version = mt5.version()
    log(f"   MT5 Version: {version}", "DEBUG")

    account = mt5.account_info()
    if account:
        log(f"   Account: {account.login} | Server: {account.server} | Currency: {account.currency}", "DEBUG")
        log(f"   Balance: ${account.balance:.2f} | Equity: ${account.equity:.2f}", "DEBUG")
    else:
        log("Could not retrieve account info", "WARN")

    check_autotrading_status()

    # Warm up the MT5 cache at startup
    refresh_mt5_cache()
    log("MT5 cache initialized", "DEBUG")


def check_mt5_health() -> bool:
    """Check if MT5 connection is still alive"""
    global mt5_connected, mt5_connection_losses, last_mt5_check

    last_mt5_check = time.time()

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log("MT5 Health Check FAILED - No tick data", "WARN")
        mt5_connected = False
        mt5_connection_losses += 1
        return False

    account = mt5.account_info()
    if account is None:
        log("MT5 Health Check FAILED - No account info", "WARN")
        mt5_connected = False
        mt5_connection_losses += 1
        return False

    mt5_connected = True
    return True


def recover_mt5_connection() -> bool:
    """Attempt to recover MT5 connection"""
    log("Attempting MT5 reconnection...", "WARN")

    try:
        mt5.shutdown()
        time.sleep(2)

        mt5_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
        init_ok = mt5.initialize(path=mt5_path)
        if init_ok:
            log("MT5 reconnection successful", "INFO")
            return True
        else:
            log("MT5 reconnection failed", "ERROR")
            return False
    except Exception as e:
        log(f"MT5 reconnection exception: {e}", "ERROR")
        return False


def get_account_balance() -> float:
    """Get current account balance (uses cache for speed)"""
    acc = get_cached_account_info()
    if acc is None:
        log("account_info() returned None - account not logged in?", "ERROR")
        return 0.0

    balance = float(acc.balance)
    equity = float(acc.equity)
    log(f"Account Balance: ${balance:.2f} | Equity: ${equity:.2f}", "DEBUG")
    return balance


def get_market_price(side: str) -> Optional[float]:
    """Get entry price (ask for BUY, bid for SELL)"""
    log(f"Getting market price for {side}...", "DEBUG")

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log(f"symbol_info_tick({SYMBOL}) returned None - market data unavailable", "ERROR")
        log(f"   Last error: {mt5.last_error()}", "ERROR")
        return None

    bid = float(tick.bid)
    ask = float(tick.ask)
    price = float(tick.ask if side == "BUY" else tick.bid)

    log(f"   Bid: ${bid:.5f} | Ask: ${ask:.5f} | Entry ({side}): ${price:.5f}", "DEBUG")
    return price


def calculate_lot_size(entry_price: float, sl_price: float, balance: float) -> float:
    """
    Calculate max lot size that risks less than RISK_PCT of balance.
    lot_size = (balance * risk_pct) / (price_distance * contract_size)
    """
    log("Calculating lot size...", "DEBUG")
    log(f"   Entry: ${entry_price:.5f} | SL: ${sl_price:.5f} | Balance: ${balance:.2f}", "DEBUG")

    price_distance = abs(entry_price - sl_price)
    log(f"   Price distance: ${price_distance:.5f}", "DEBUG")

    if price_distance <= 0:
        log(f"Invalid price distance: {price_distance}", "ERROR")
        return 0.01

    symbol_info = get_cached_symbol_info()
    if symbol_info is None:
        log(f"symbol_info({SYMBOL}) returned None", "ERROR")
        log(f"   Last error: {mt5.last_error()}", "ERROR")
        return 0.01

    contract_size = float(symbol_info.trade_contract_size)
    vol_min = float(symbol_info.volume_min)
    vol_max = float(symbol_info.volume_max)
    vol_step = float(symbol_info.volume_step)

    log(f"   Contract size: {contract_size} | Min: {vol_min} | Max: {vol_max} | Step: {vol_step}", "DEBUG")

    max_risk_money = balance * RISK_PCT
    lot = max_risk_money / (price_distance * contract_size)

    log(f"   Max risk ({RISK_PCT*100:.0f}%): ${max_risk_money:.2f} | Calculated lot: {lot:.4f}", "DEBUG")

    lot = max(lot, vol_min)
    lot = min(lot, vol_max)
    lot = round(lot / vol_step) * vol_step

    log(f"   Final lot size: {lot:.4f}", "DEBUG")
    return lot


def get_filling_mode() -> int:
    """Get the appropriate filling mode for the symbol (cached per session for speed)"""
    global _cached_filling_mode
    if _cached_filling_mode is not None:
        return _cached_filling_mode

    symbol_info = get_cached_symbol_info()
    if symbol_info is None:
        log("Could not get symbol info, defaulting to IOC", "WARN")
        return mt5.ORDER_FILLING_IOC

    filling = symbol_info.filling_mode

    if filling & 2 == 2:
        log("   Using ORDER_FILLING_IOC (cached)", "DEBUG")
        _cached_filling_mode = mt5.ORDER_FILLING_IOC
    elif filling & 1 == 1:
        log("   Using ORDER_FILLING_FOK (cached)", "DEBUG")
        _cached_filling_mode = mt5.ORDER_FILLING_FOK
    else:
        log("   Using ORDER_FILLING_RETURN (cached)", "DEBUG")
        _cached_filling_mode = mt5.ORDER_FILLING_RETURN

    return _cached_filling_mode


def open_position(side: str, lot: float, sl: float = 0.0, tp: float = 0.0) -> Optional[int]:
    """
    Open market position with optional SL/TP in a single order.
    Including SL/TP in the initial order avoids a second round-trip.
    Returns ticket number or None.
    """
    log(f"Opening {side} position with lot size {lot:.4f}...", "INFO")

    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    filling_mode = get_filling_mode()

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": lot,
        "type": order_type,
        "type_filling": filling_mode,
        "magic": MAGIC,
        "comment": f"Signal {side}",
    }

    # Include SL/TP in initial order to avoid a second round-trip
    if sl > 0:
        request["sl"] = sl
    if tp > 0:
        request["tp"] = tp

    log(f"   Request: {request}", "DEBUG")

    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        ticket = result.order
        log(f"Position opened successfully - Ticket: {ticket}", "INFO")
        return ticket

    # If SL/TP caused the rejection, retry without them and set separately
    if result.retcode in {5, 10040, 10041} and (sl > 0 or tp > 0):
        log(f"Order with SL/TP rejected (retcode {result.retcode}) - retrying without SL/TP...", "WARN")
        request.pop("sl", None)
        request.pop("tp", None)
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            ticket = result.order
            log(f"Position opened without SL/TP - Ticket: {ticket}. Setting SL/TP separately...", "INFO")
            set_sl_and_tp(ticket, sl, tp)
            return ticket

    if result.retcode == 10027:
        log("AutoTrading DISABLED - cannot trade!", "ERROR")
        return None

    # Market closed / close-only (retcode 10015)
    if result.retcode == 10015:
        log("Market is CLOSE-ONLY (retcode 10015) - cannot open new positions right now", "ERROR")
        log("   This typically means: market is closed (weekend/holiday) or account is in close-only mode", "ERROR")
        # Retry a few times with increasing delay in case market is about to reopen
        for attempt in range(1, 4):
            delay = 5 * attempt  # 5s, 10s, 15s
            log(f"   Retry {attempt}/3 for 10015 in {delay}s...", "INFO")
            time.sleep(delay)
            if not is_market_open():
                log(f"   Market still close-only after {delay}s", "WARN")
                continue
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                ticket = result.order
                log(f"Position opened on 10015-retry {attempt} - Ticket: {ticket}", "INFO")
                return ticket
            if result.retcode != 10015:
                break  # Different error, fall through
            log(f"   Retry {attempt} still 10015", "WARN")
        log("All 10015 retries exhausted - market remains closed", "ERROR")
        return None

    log(f"Order failed - Retcode: {result.retcode} | Error: {result.comment}", "ERROR")
    log(f"   Human-readable: {get_error_message(result.retcode)}", "ERROR")
    log(f"   Last MT5 error: {mt5.last_error()}", "ERROR")

    # Retry for certain errors (reduced delay from 2s to 0.5s)
    retryable_codes = {10009, 10028, 10044, 9}

    if result.retcode in retryable_codes:
        for attempt in range(1, 3):
            log(f"   Retry attempt {attempt}/2 (retcode was {result.retcode})...", "INFO")
            time.sleep(0.15)
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                ticket = result.order
                log(f"Position opened on retry - Ticket: {ticket}", "INFO")
                return ticket
            log(f"   Retry {attempt} failed: {result.comment}", "WARN")

    return None


def set_stop_loss(ticket: int, sl_price: float) -> bool:
    """Update position stop loss - preserves current TP"""
    log(f"Updating SL for ticket {ticket} to ${sl_price:.5f}...", "INFO")

    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"Position {ticket} not found", "ERROR")
        return False

    current_tp = float(pos[0].tp) if pos[0].tp > 0 else 0.0
    log(f"   Current TP: ${current_tp:.5f} (will preserve)", "DEBUG")

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": sl_price,
        "tp": current_tp,
    }

    result = mt5.order_send(request)
    success = result.retcode == mt5.TRADE_RETCODE_DONE

    if success:
        log(f"SL updated - Ticket {ticket}: ${sl_price:.5f}", "INFO")
    else:
        log(f"SL update FAILED - Retcode: {result.retcode} | Error: {result.comment}", "WARN")
        log(f"   Last MT5 error: {mt5.last_error()}", "WARN")

    return success


def set_sl_and_tp(ticket: int, sl_price: float, tp_price: float) -> bool:
    """Update both SL and TP on a position in a single MT5 call."""
    log(f"Updating SL+TP for ticket {ticket}: SL=${sl_price:.5f} | TP=${tp_price:.5f}", "INFO")

    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"Position {ticket} not found", "ERROR")
        return False

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": sl_price,
        "tp": tp_price,
    }

    result = mt5.order_send(request)
    success = result.retcode == mt5.TRADE_RETCODE_DONE

    if success:
        log(f"SL+TP updated - Ticket {ticket}: SL=${sl_price:.5f} | TP=${tp_price:.5f}", "INFO")
    else:
        log(f"SL+TP update FAILED - Retcode: {result.retcode} | Error: {result.comment}", "WARN")
        log(f"   Last MT5 error: {mt5.last_error()}", "WARN")

    return success


def set_take_profit(ticket: int, tp_price: float) -> bool:
    """Update position take profit - preserves current SL"""
    log(f"Updating TP for ticket {ticket} to ${tp_price:.5f}...", "INFO")

    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"Position {ticket} not found", "ERROR")
        return False

    current_sl = float(pos[0].sl) if pos[0].sl > 0 else 0.0
    log(f"   Current SL: ${current_sl:.5f} (will preserve)", "DEBUG")

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": current_sl,
        "tp": tp_price,
    }

    log(f"   Request: {request}", "DEBUG")

    result = mt5.order_send(request)
    log(f"   Response retcode: {result.retcode}", "DEBUG")

    success = result.retcode == mt5.TRADE_RETCODE_DONE

    if success:
        log(f"TP updated - Ticket {ticket}: ${tp_price:.5f}", "INFO")
    else:
        log(f"TP update FAILED - Retcode: {result.retcode} | Error: {result.comment}", "WARN")
        log(f"   Last MT5 error: {mt5.last_error()}", "WARN")

    return success


# ===================== PARSING =====================

def parse_signal(text: str) -> Optional[Tuple[str, str]]:
    """
    Parse "XAUUSD BUY NOW" or "XAUUSD SELL NOW"
    Returns (symbol, side) or None
    """
    text = text.upper().strip()
    log(f"Parsing signal from: {text[:80]}", "DEBUG")

    if "BUY NOW" in text:
        log("   Found BUY signal", "DEBUG")
        return (SYMBOL, "BUY")
    if "SELL NOW" in text:
        log("   Found SELL signal", "DEBUG")
        return (SYMBOL, "SELL")

    log("   No BUY/SELL signal found", "DEBUG")
    return None


def parse_tp_levels(text: str) -> Dict[int, float]:
    """
    Parse all TP levels from text.
    Looks for patterns like "TP1 4877" or "TP 1 4877"
    Returns {1: 4877, 2: 4875, 3: 4873, ...}
    """
    log("Parsing TP levels...", "DEBUG")
    levels = {}

    matches = _RE_TP_LEVELS.finditer(text)

    for match in matches:
        tp_num = int(match.group(1))
        tp_price = float(match.group(2))

        if tp_price >= 100:
            levels[tp_num] = tp_price
            log(f"   Found TP{tp_num}: ${tp_price:.5f}", "DEBUG")

    # Fallback: bare "TP 5164" without level number → treat as TP1
    if not levels:
        bare_match = _RE_TP_BARE.search(text)
        if bare_match:
            tp_price = float(bare_match.group(1))
            if tp_price >= 1000:
                levels[1] = tp_price
                log(f"   Found bare TP (treated as TP1): ${tp_price:.5f}", "DEBUG")

    if not levels:
        log("   No TP levels found", "DEBUG")

    return levels


def parse_stop_loss(text: str) -> Optional[float]:
    """
    Parse stop loss value from text.
    Looks for patterns like "SL 4888" or "SL 4873"
    """
    log("Parsing stop loss...", "DEBUG")

    match = _RE_STOP_LOSS.search(text)

    if match:
        sl_price = float(match.group(1))
        if sl_price >= 100:
            log(f"   Found SL: ${sl_price:.5f}", "DEBUG")
            return sl_price

    log("   No valid SL found", "DEBUG")
    return None


def parse_tp1(text: str) -> Optional[float]:
    """Parse TP1 value from text"""
    match = _RE_TP1.search(text)

    if match:
        tp1_price = float(match.group(1))
        if tp1_price >= 100:
            return tp1_price

    # Fallback: bare "TP 5164" without level number → treat as TP1
    bare_match = _RE_TP_BARE.search(text)
    if bare_match:
        tp1_price = float(bare_match.group(1))
        if tp1_price >= 1000:
            return tp1_price

    return None


def parse_entry_zone(text: str) -> Optional[Tuple[float, float]]:
    """
    Parse entry zone from signal message text.
    Returns (zone_low, zone_high) or None.
    """
    match = _RE_ENTRY_ZONE.search(text)
    if not match:
        return None

    val1 = float(match.group(1))
    val2 = float(match.group(2))

    if val1 < 1000 or val2 < 1000:
        return None

    zone_high = max(val1, val2)
    zone_low = min(val1, val2)
    zone_width = zone_high - zone_low

    if zone_width > 30 or zone_width < 0.5:
        log(f"Entry zone rejected: ${zone_low:.2f}-${zone_high:.2f} (width ${zone_width:.2f})", "WARN")
        return None

    log(f"Parsed entry zone: ${zone_low:.2f} - ${zone_high:.2f} (width ${zone_width:.2f})", "INFO")
    return (zone_low, zone_high)


def parse_trade_closure(text: str) -> Optional[str]:
    """
    Detect trade closure messages from the channel.
    Returns closure type string or None.
    Matches: "TP1 HIT", "TP1 ,2,3,4 HIT", "SL HIT"
    Does NOT match "Profit" or "Close profit" - those are just status updates.
    """
    match = _RE_TRADE_CLOSURE.search(text)
    if match:
        matched = match.group(0).strip()
        log(f"Trade closure detected: '{matched}'", "INFO")
        return matched
    return None


# ===================== LIMIT ORDER FUNCTIONS =====================

def place_limit_order(side: str, price: float, lot: float, sl: float = 0.0, tp: float = 0.0) -> Optional[int]:
    """Place a pending limit or stop order at the specified price.
    Automatically chooses LIMIT vs STOP based on current price position:
      - BUY LIMIT if price is above order price (waiting for pullback)
      - BUY STOP if price is below order price (waiting for price to rise into zone)
      - SELL LIMIT if price is below order price (waiting for rally)
      - SELL STOP if price is above order price (waiting for price to drop into zone)
    Returns order ticket or None.
    """
    # Determine correct order type based on current price vs order price
    current_price = get_market_price(side)
    if side == "BUY":
        if current_price and current_price < price:
            order_type = mt5.ORDER_TYPE_BUY_STOP
            order_label = "BUY STOP"
        else:
            order_type = mt5.ORDER_TYPE_BUY_LIMIT
            order_label = "BUY LIMIT"
    else:
        if current_price and current_price > price:
            order_type = mt5.ORDER_TYPE_SELL_STOP
            order_label = "SELL STOP"
        else:
            order_type = mt5.ORDER_TYPE_SELL_LIMIT
            order_label = "SELL LIMIT"

    cp_str = f"${current_price:.2f}" if current_price else "N/A"
    log(f"Placing {order_label} at ${price:.2f} (current={cp_str}) | Lot={lot:.4f} | SL=${sl:.2f} | TP=${tp:.2f}", "INFO")

    filling_mode = get_filling_mode()

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": SYMBOL,
        "volume": lot,
        "type": order_type,
        "price": price,
        "type_filling": filling_mode,
        "type_time": mt5.ORDER_TIME_GTC,
        "magic": MAGIC,
        "comment": f"Zone {side}",
    }
    if sl > 0:
        request["sl"] = sl
    if tp > 0:
        request["tp"] = tp

    result = mt5.order_send(request)
    if result is None:
        log(f"Limit order failed - order_send returned None | Error: {mt5.last_error()}", "ERROR")
        return None

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"Limit order placed - ticket: {result.order}", "INFO")
        return result.order

    # Retry without SL/TP if they caused rejection
    if result.retcode in {5, 10040, 10041} and (sl > 0 or tp > 0):
        log(f"Limit order with SL/TP rejected ({result.retcode}) - retrying without", "WARN")
        request.pop("sl", None)
        request.pop("tp", None)
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log(f"Limit order placed (no SL/TP) - ticket: {result.order}", "INFO")
            return result.order

    log(f"Limit order failed - Retcode: {result.retcode} | {result.comment}", "ERROR")
    log(f"   Human-readable: {get_error_message(result.retcode)}", "ERROR")

    # Market closed / close-only (retcode 10015) - retry with backoff
    if result.retcode == 10015:
        log("Market is CLOSE-ONLY (retcode 10015) - limit order cannot be placed now", "ERROR")
        for attempt in range(1, 4):
            delay = 5 * attempt
            log(f"   Retry {attempt}/3 for 10015 in {delay}s...", "INFO")
            time.sleep(delay)
            if not is_market_open():
                log(f"   Market still close-only", "WARN")
                continue
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"Limit order placed on 10015-retry {attempt} - ticket: {result.order}", "INFO")
                return result.order
            if result and result.retcode != 10015:
                break
            log(f"   Retry {attempt} still 10015", "WARN")
        log("All 10015 retries exhausted for limit order", "ERROR")

    return None


def cancel_limit_order(order_ticket: int) -> bool:
    """Cancel a pending limit order."""
    log(f"Canceling limit order {order_ticket}...", "INFO")
    request = {"action": mt5.TRADE_ACTION_REMOVE, "order": order_ticket}
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"Limit order {order_ticket} canceled", "INFO")
        return True
    log(f"Failed to cancel order {order_ticket}: {result.comment if result else 'None'}", "WARN")
    return False


def modify_limit_order_sl_tp(order_ticket: int, sl: float = 0.0, tp: float = 0.0) -> bool:
    """Modify SL/TP on a pending limit order."""
    orders = mt5.orders_get(ticket=order_ticket)
    if not orders:
        log(f"Pending order {order_ticket} not found", "WARN")
        return False
    order = orders[0]
    request = {
        "action": mt5.TRADE_ACTION_MODIFY,
        "order": order_ticket,
        "price": float(order.price_open),
        "sl": sl if sl > 0 else float(order.sl),
        "tp": tp if tp > 0 else float(order.tp),
        "type_time": mt5.ORDER_TIME_GTC,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"Pending order {order_ticket} modified: SL=${sl:.2f} TP=${tp:.2f}", "INFO")
        return True
    log(f"Failed to modify order {order_ticket}: {result.comment if result else 'None'}", "WARN")
    return False


def find_position_from_order(order_ticket: int) -> Optional[int]:
    """Find the position ticket from a filled pending order via deal history."""
    now = datetime.now()
    deals = mt5.history_deals_get(now - timedelta(hours=24), now + timedelta(minutes=1))
    if deals:
        for deal in deals:
            if deal.order == order_ticket and deal.entry == 0:
                return deal.position_id
    return None


def get_fill_price_from_order(order_ticket: int) -> Optional[float]:
    """Get the fill price of a filled pending order."""
    now = datetime.now()
    deals = mt5.history_deals_get(now - timedelta(hours=24), now + timedelta(minutes=1))
    if deals:
        for deal in deals:
            if deal.order == order_ticket and deal.entry == 0:
                return float(deal.price)
    return None


# ===================== ENTRY STATS TRACKER =====================

STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", f"entry_stats_{MAGIC}.json")


def _load_entry_stats() -> Dict:
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "total_signals": 0,
        "limit_orders_placed": 0,
        "limit_fills": 0,
        "limit_timeouts": 0,
        "limit_invalidated": 0,
        "signals_buffered": 0,
        "zones_received_from_edit": 0,
        "zone_wait_timeouts": 0,
        "market_orders_no_zone": 0,
        "market_orders_in_zone": 0,
        "market_orders_fallback": 0,
        "total_limit_slippage": 0.0,
        "zone_tracking_total": 0,
        "zone_best_entry_reached": 0,
        "zone_worst_entry_reached": 0,
        "entries": [],
    }


def _save_entry_stats():
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(_entry_stats, f, indent=2)
    except Exception as e:
        log(f"Failed to save entry stats: {e}", "WARN")


_entry_stats: Dict = _load_entry_stats()


def record_entry_stat(event_type: str, **kwargs):
    """Record an entry quality event and persist."""
    global _entry_stats
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    entry_record = {"time": ts, "type": event_type}
    entry_record.update(kwargs)

    if event_type == "signal":
        _entry_stats["total_signals"] += 1
    elif event_type == "limit_placed":
        _entry_stats["limit_orders_placed"] += 1
    elif event_type == "limit_fill":
        _entry_stats["limit_fills"] += 1
        _entry_stats["total_limit_slippage"] += kwargs.get("slippage", 0.0)
    elif event_type == "limit_timeout":
        _entry_stats["limit_timeouts"] += 1
    elif event_type == "limit_invalidated":
        _entry_stats.setdefault("limit_invalidated", 0)
        _entry_stats["limit_invalidated"] += 1
    elif event_type == "signal_buffered":
        _entry_stats.setdefault("signals_buffered", 0)
        _entry_stats["signals_buffered"] += 1
    elif event_type == "zone_from_edit":
        _entry_stats.setdefault("zones_received_from_edit", 0)
        _entry_stats["zones_received_from_edit"] += 1
    elif event_type == "zone_wait_timeout":
        _entry_stats.setdefault("zone_wait_timeouts", 0)
        _entry_stats["zone_wait_timeouts"] += 1
    elif event_type == "market_no_zone":
        _entry_stats["market_orders_no_zone"] += 1
    elif event_type == "market_entry_in_zone":
        _entry_stats["market_orders_in_zone"] += 1
    elif event_type == "market_fallback":
        _entry_stats["market_orders_fallback"] += 1
    elif event_type == "limit_placement_failed":
        _entry_stats.setdefault("limit_placement_failed", 0)
        _entry_stats["limit_placement_failed"] += 1

    # Zone entry tracking — record whether price reached best/worst edge
    if "best_reached" in kwargs:
        _entry_stats.setdefault("zone_tracking_total", 0)
        _entry_stats["zone_tracking_total"] += 1
        if kwargs["best_reached"]:
            _entry_stats.setdefault("zone_best_entry_reached", 0)
            _entry_stats["zone_best_entry_reached"] += 1
        if kwargs.get("worst_reached"):
            _entry_stats.setdefault("zone_worst_entry_reached", 0)
            _entry_stats["zone_worst_entry_reached"] += 1

    _entry_stats["entries"].append(entry_record)
    _entry_stats["entries"] = _entry_stats["entries"][-500:]
    _save_entry_stats()


def get_entry_stats_summary() -> str:
    s = _entry_stats
    fill_rate = (s["limit_fills"] / s["limit_orders_placed"] * 100) if s["limit_orders_placed"] > 0 else 0
    avg_slip = (s["total_limit_slippage"] / s["limit_fills"]) if s["limit_fills"] > 0 else 0
    inv = s.get("limit_invalidated", 0)
    buf = s.get("signals_buffered", 0)
    zfe = s.get("zones_received_from_edit", 0)
    zwt = s.get("zone_wait_timeouts", 0)
    return (
        f"Limits: placed={s['limit_orders_placed']} filled={s['limit_fills']} "
        f"timeout={s['limit_timeouts']} invalidated={inv} ({fill_rate:.0f}% fill) | "
        f"Buffered: {buf} (zone_edits={zfe} zone_timeout={zwt}) | "
        f"Market: no_zone={s.get('market_orders_no_zone',0)} in_zone={s.get('market_orders_in_zone',0)} "
        f"fallback={s.get('market_orders_fallback',0)} | Avg slip: ${avg_slip:.2f} | "
        f"Zone: best_hit={s.get('zone_best_entry_reached',0)}/{s.get('zone_tracking_total',0)} "
        f"worst_hit={s.get('zone_worst_entry_reached',0)}/{s.get('zone_tracking_total',0)}"
    )


def extract_message_text(message) -> str:
    """Extract text from Telegram message (tries multiple attributes for robustness)"""
    if hasattr(message, "raw_text") and message.raw_text:
        return message.raw_text
    if hasattr(message, "message") and message.message:
        return message.message
    if hasattr(message, "text") and message.text:
        return message.text
    if hasattr(message, "caption") and message.caption:
        return message.caption
    return ""


def log_signal_attempt(side: str, result: str, detail: str = ""):
    """Log a signal attempt for debugging and update health counters"""
    global trades_executed, trades_failed, messages_processed

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "timestamp": ts,
        "signal": side,
        "result": result,
        "detail": detail,
    }
    signal_log.append(entry)
    log(f"Signal log: {side} | {result} | {detail}", "DEBUG")

    if result == "SUCCESS":
        trades_executed += 1
        messages_processed += 1
    elif result == "FAILED":
        trades_failed += 1
        messages_processed += 1


def get_bot_metrics() -> Dict:
    """Get current bot health metrics"""
    uptime_seconds = time.time() - bot_start_time if bot_start_time > 0 else 0
    uptime_hours = uptime_seconds / 3600

    time_since_last_msg = time.time() - last_message_time if last_message_time > 0 else 0

    return {
        "uptime_hours": uptime_hours,
        "uptime_seconds": uptime_seconds,
        "messages_received": messages_received,
        "messages_processed": messages_processed,
        "messages_ignored": messages_ignored,
        "trades_executed": trades_executed,
        "trades_failed": trades_failed,
        "mt5_connection_losses": mt5_connection_losses,
        "telegram_connection_losses": telegram_connection_losses,
        "seconds_since_last_message": time_since_last_msg,
        "mt5_connected": mt5_connected,
        "telegram_connected": telegram_connected,
        "active_positions": len(position_map),
        "positions_with_real_tp": sum(1 for v in tp_sl_updated.values() if v),
        "entry_strategy": ENTRY_STRATEGY,
        "buffered_signals": len(_buffered_signals),
        "pending_limit_orders": len(_pending_limit_orders),
        "entry_stats": get_entry_stats_summary(),
    }


def print_bot_status():
    """Print comprehensive bot status"""
    metrics = get_bot_metrics()

    log("=" * 70, "INFO")
    log("BOT HEALTH STATUS", "INFO")
    log("=" * 70, "INFO")
    log(f"  Uptime: {metrics['uptime_hours']:.2f} hours ({metrics['uptime_seconds']:.0f}s)", "INFO")
    log(f"  Messages: Received={metrics['messages_received']} | Processed={metrics['messages_processed']} | Ignored={metrics['messages_ignored']}", "INFO")
    log(f"  Trades: Executed={metrics['trades_executed']} | Failed={metrics['trades_failed']}", "INFO")
    log(f"  Connections: MT5={('OK' if metrics['mt5_connected'] else 'DOWN')} | Telegram={('OK' if metrics['telegram_connected'] else 'DOWN')}", "INFO")
    log(f"  Connection Losses: MT5={metrics['mt5_connection_losses']} | Telegram={metrics['telegram_connection_losses']}", "INFO")
    log(f"  Strategy: {metrics['entry_strategy']} (SET & FORGET TP1)", "INFO")
    log(f"  Zone Wait Timeout: {ZONE_WAIT_TIMEOUT}s | Limit Order Timeout: {LIMIT_ORDER_TIMEOUT}s", "INFO")
    log(f"  Active Positions: {metrics['active_positions']} ({metrics['positions_with_real_tp']} with real SL/TP)", "INFO")
    log(f"  Buffered Signals: {metrics['buffered_signals']} | Pending Limits: {metrics['pending_limit_orders']}", "INFO")
    log(f"  {get_entry_stats_summary()}", "INFO")

    if last_message_time > 0:
        log(f"  Last Message: {metrics['seconds_since_last_message']:.0f}s ago", "INFO")
    else:
        log("  Last Message: Never", "INFO")

    log("=" * 70, "INFO")


# ===================== TELEGRAM HANDLERS =====================

def init_telegram():
    """Create Telegram client"""
    return TelegramClient(SESSION_FILE, API_ID, API_HASH)


async def reconnect_telegram(client, reason: str = "unknown") -> bool:
    """
    Robust Telegram reconnection with multiple retries and exponential backoff.
    Returns True if reconnection succeeded, False if all attempts exhausted.
    """
    global telegram_connected, last_telegram_activity, telegram_connection_losses

    max_attempts = 5
    base_delay = 5
    max_delay = 120

    log(f"Starting Telegram reconnection sequence (reason: {reason})...", "WARN")

    for attempt in range(1, max_attempts + 1):
        delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
        log(f"  Reconnection attempt {attempt}/{max_attempts} (delay: {delay}s)...", "INFO")

        # Ensure we're fully disconnected first
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass

        await asyncio.sleep(delay)

        try:
            await client.connect()
            if not await client.is_user_authorized():
                log("  Connected but not authorized - calling client.start()...", "WARN")
                await client.start()

            # Verify the connection actually works
            me = await asyncio.wait_for(client.get_me(), timeout=15)
            if me:
                telegram_connected = True
                last_telegram_activity = time.time()
                log(f"  Telegram reconnected successfully on attempt {attempt}", "INFO")

                # Refresh dialogs to re-initialize update state
                try:
                    await client.get_dialogs()
                    log("  Dialogs refreshed after reconnect", "DEBUG")
                except Exception:
                    pass

                # Catch up on any missed messages
                await catch_up_messages(client, lookback_minutes=5)
                return True
            else:
                log(f"  get_me() returned None on attempt {attempt}", "WARN")

        except Exception as e:
            log(f"  Reconnection attempt {attempt} failed: {e}", "ERROR")
            telegram_connected = False

    log(f"ALL {max_attempts} reconnection attempts failed", "ERROR")
    return False


def restart_bot():
    """
    Self-restart the bot process. Equivalent to Ctrl+C → re-run.
    This replaces the current process entirely.
    """
    log("=" * 70, "WARN")
    log("SELF-RESTART: Bot is restarting itself...", "WARN")
    log("=" * 70, "WARN")

    # Shutdown MT5 cleanly before restart
    try:
        mt5.shutdown()
    except Exception:
        pass

    # Small delay to let logs flush
    time.sleep(2)

    # Re-execute the same script with the same Python interpreter
    python = sys.executable
    script = os.path.abspath(__file__)
    os.execv(python, [python, script])


async def catch_up_messages(client, lookback_minutes: int = 5):
    """Manually fetch recent messages from allowed channels to catch up on missed messages."""
    try:
        log(f"Catching up on messages from last {lookback_minutes} minutes...", "INFO")
        cutoff_time = time.time() - (lookback_minutes * 60)

        for chat_id in ALLOWED_CHAT_IDS:
            try:
                messages = await client.get_messages(chat_id, limit=20)
                caught_up = 0

                for msg in reversed(messages):
                    if msg.date.timestamp() >= cutoff_time:
                        caught_up += 1
                        log(f"   Caught up message {msg.id} from {msg.date.strftime('%H:%M:%S')}", "DEBUG")

                if caught_up > 0:
                    log(f"Caught up {caught_up} message(s) from chat {chat_id}", "INFO")
            except Exception as e:
                log(f"Failed to catch up messages from chat {chat_id}: {e}", "WARN")

    except Exception as e:
        log(f"Error during message catch-up: {e}", "WARN")


async def replay_pending_signals(client):
    """Replay signals from queue that were received but not yet executed."""
    if not signal_queue:
        return

    pending = signal_queue.get_pending_signals()
    if not pending:
        log("No pending signals to replay", "INFO")
        return

    log("=" * 70, "INFO")
    log(f"REPLAYING {len(pending)} PENDING SIGNALS FROM QUEUE", "INFO")
    log("=" * 70, "INFO")

    for sig in pending:
        msg_id = sig.get("id")
        signal_type = sig.get("signal_type")
        msg_text = sig.get("msg_text", "")
        log(f"Replaying {signal_type} signal (msg_id={msg_id}): {msg_text[:60]}", "INFO")

    log("=" * 70, "INFO")


async def heartbeat_monitor():
    """Regular heartbeat to prove bot is alive - runs every 15 minutes (compact single-line)."""
    global heartbeat_counter

    HEARTBEAT_INTERVAL = 900  # 15 minutes
    log(f"Heartbeat monitor started ({HEARTBEAT_INTERVAL}s interval)", "INFO")

    while True:
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL)

            heartbeat_counter += 1
            metrics = get_bot_metrics()

            mt5_status = 'OK' if metrics['mt5_connected'] else 'DOWN'
            tg_status = 'OK' if metrics['telegram_connected'] else 'DOWN'
            log(
                f"HEARTBEAT #{heartbeat_counter} | "
                f"Up {metrics['uptime_hours']:.1f}h | "
                f"Msgs {metrics['messages_received']} | "
                f"Trades {metrics['trades_executed']} | "
                f"MT5={mt5_status} TG={tg_status} | "
                f"Pos {metrics['active_positions']} | "
                f"Buf {metrics['buffered_signals']} Lim {metrics['pending_limit_orders']}",
                "INFO",
            )

        except Exception as e:
            log(f"CRITICAL: Heartbeat monitor exception: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
            await asyncio.sleep(10)


# Track previous connection states so we only log on change
_prev_mt5_healthy: bool = True
_prev_tg_healthy: bool = True


async def connection_health_monitor():
    """Monitor MT5 and Telegram connection health - runs every 5 minutes.
    Only logs when connection state *changes* (OK→DOWN or DOWN→OK) to reduce noise."""
    global mt5_connected, telegram_connected, _prev_mt5_healthy, _prev_tg_healthy

    HEALTH_CHECK_INTERVAL = 300  # 5 minutes
    log(f"Connection health monitor started ({HEALTH_CHECK_INTERVAL}s interval)", "INFO")

    while True:
        try:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

            # Check MT5 connection (in executor to avoid blocking)
            mt5_healthy = await run_mt5(check_mt5_health)
            if not mt5_healthy:
                if _prev_mt5_healthy:
                    log("MT5 connection went DOWN - attempting recovery", "WARN")
                recovered = await run_mt5(recover_mt5_connection)
                if recovered:
                    if not _prev_mt5_healthy:
                        log("MT5 connection recovered", "INFO")
                    mt5_connected = True
                else:
                    log("MT5 connection recovery failed", "ERROR")
                    mt5_connected = False
                _prev_mt5_healthy = recovered
            else:
                if not _prev_mt5_healthy:
                    log("MT5 connection restored", "INFO")
                _prev_mt5_healthy = True
                mt5_connected = True

            # Telegram connectivity check
            if last_telegram_activity > 0:
                time_since_telegram = time.time() - last_telegram_activity

                if time_since_telegram < 120:
                    if not _prev_tg_healthy:
                        log("Telegram connection restored", "INFO")
                    _prev_tg_healthy = True
                    telegram_connected = True
                elif time_since_telegram > 300:
                    log(f"No Telegram activity for {time_since_telegram:.0f}s - running active connectivity check...", "WARN")

                    if _telegram_client is not None:
                        try:
                            if not _telegram_client.is_connected():
                                log("Telegram client.is_connected() = False - attempting reconnection from health monitor", "WARN")
                                telegram_connected = False
                                reconnected = await reconnect_telegram(_telegram_client, reason="health_monitor_not_connected")
                                if not reconnected:
                                    log("Health monitor reconnection failed - main loop will handle restart", "ERROR")
                            else:
                                try:
                                    await asyncio.wait_for(_telegram_client.get_me(), timeout=10)
                                    log("Telegram ping successful - connection alive, channel is just quiet", "DEBUG")
                                    telegram_connected = True
                                except (asyncio.TimeoutError, Exception) as ping_err:
                                    log(f"Telegram ping FAILED ({ping_err}) - attempting reconnection", "WARN")
                                    telegram_connected = False
                                    reconnected = await reconnect_telegram(_telegram_client, reason="health_monitor_ping_failed")
                                    if not reconnected:
                                        log("Health monitor reconnection failed - main loop will handle restart", "ERROR")
                                    _prev_tg_healthy = reconnected
                        except Exception as check_err:
                            log(f"Error during active Telegram check: {check_err}", "WARN")
                            telegram_connected = False
                            _prev_tg_healthy = False
                    else:
                        telegram_connected = False
                        _prev_tg_healthy = False
                else:
                    telegram_connected = True

        except Exception as e:
            log(f"CRITICAL: Health monitor exception: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
            await asyncio.sleep(10)


async def monitor_stale_failsafe():
    """
    Robustness monitor: periodically check if any positions still have failsafe SL/TP.

    This means the message edit with real SL/TP1 was never received.
    Logs a warning so the operator is aware, but does NOT close the trade
    (set-and-forget means the failsafe levels will handle the exit).
    """
    log("Stale failsafe monitor started (checks every 120s)", "INFO")

    while True:
        try:
            await asyncio.sleep(120)

            # Clean up ALL closed positions from tracking dicts (not just failsafe ones)
            all_tracked_tickets = list(tp_sl_updated.keys())
            for ticket in all_tracked_tickets:
                pos = await run_mt5(lambda t=ticket: mt5.positions_get(ticket=t))
                if not pos:
                    was_stale = not tp_sl_updated.get(ticket, True)
                    label = "failsafe SL/TP" if was_stale else "real SL/TP"
                    log(f"Position {ticket} closed by broker ({label}). Cleaning up.", "INFO")
                    tp_sl_updated.pop(ticket, None)
                    entry_prices.pop(ticket, None)
                    for mid, tid in list(position_map.items()):
                        if tid == ticket:
                            position_map.pop(mid, None)
                            break
                elif not tp_sl_updated.get(ticket, True):
                    log(f"WARNING: Ticket {ticket} still has FAILSAFE SL/TP (no edit received yet)", "WARN")

        except Exception as e:
            log(f"Stale failsafe monitor error: {str(e)}", "ERROR")
            await asyncio.sleep(30)


async def mt5_cache_refresher():
    """Background task to keep MT5 cache warm for fast trade execution."""
    while True:
        try:
            await asyncio.sleep(5)
            await run_mt5(refresh_mt5_cache)
        except Exception:
            await asyncio.sleep(5)


# Last trade/msg counts when we last printed status (suppress unchanged reports)
_last_status_trades: int = 0
_last_status_msgs: int = 0


async def cleanup_and_report():
    """Periodic cleanup and reporting of system health.
    Full status report is only printed when there has been meaningful activity
    (new messages or trades) since the last report."""
    global _last_status_trades, _last_status_msgs

    while True:
        try:
            await asyncio.sleep(300)  # Every 5 minutes

            if signal_queue:
                signal_queue.remove_old_signals(days=7)

            if session_manager:
                session_manager.cleanup_old_backups(keep_count=5)

            if reconnect_monitor:
                reconnect_monitor.reset_window()
                if reconnect_monitor.get_stats()['total_reconnects'] > 0:
                    reconnect_monitor.print_summary()

            # Only print full status report when there has been activity
            if trades_executed != _last_status_trades or messages_received != _last_status_msgs:
                print_bot_status()
                _last_status_trades = trades_executed
                _last_status_msgs = messages_received

        except Exception as e:
            log(f"CRITICAL: Cleanup exception: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
            await asyncio.sleep(10)


# ===================== PROCESS SIGNAL MESSAGE =====================

def execute_signal_trade(side: str, msg_id: int, msg_text: str):
    """
    Execute a trade from a parsed signal - Set & Forget TP1 Strategy.

    1. Get market price + open position with failsafe SL/TP in a single order
    2. Wait for message edit to provide real SL and TP1 values

    Runs inside the MT5 executor thread. All MT5 calls happen here to minimize
    async executor round-trips (previously get_market_price was a separate call).
    Returns (ticket, entry_price, failsafe_sl, lot) on success, or None on failure.
    Signal queue persistence is handled by the caller to keep file I/O off this thread.
    """
    # Get market price (previously a separate executor round-trip)
    entry_price = get_market_price(side)
    if entry_price is None:
        log("Cannot get market price - aborting trade", "ERROR")
        log_signal_attempt(side, "FAILED", "Cannot get market price")
        return None

    # Check if market is open for new trades
    if not is_market_open():
        log("Market is CLOSED / CLOSE-ONLY - cannot open new positions", "ERROR")
        log("   Signal will not be retried. Check market hours or broker restrictions.", "ERROR")
        log_signal_attempt(side, "FAILED", "Market closed (close-only mode)")
        return None

    balance = get_account_balance()
    if balance <= 0:
        log(f"Invalid balance: ${balance:.2f} - aborting trade", "ERROR")
        log_signal_attempt(side, "FAILED", f"Invalid balance: ${balance:.2f}")
        return None

    # Calculate failsafe levels
    if side == "BUY":
        failsafe_sl = entry_price - FAILSAFE_SL_DISTANCE
        failsafe_tp = entry_price + FAILSAFE_TP_DISTANCE
    else:
        failsafe_sl = entry_price + FAILSAFE_SL_DISTANCE
        failsafe_tp = entry_price - FAILSAFE_TP_DISTANCE

    log(f"Failsafe SL: ${failsafe_sl:.5f} (${FAILSAFE_SL_DISTANCE} from entry)", "INFO")
    log(f"Failsafe TP: ${failsafe_tp:.5f} (${FAILSAFE_TP_DISTANCE} from entry)", "INFO")

    lot = calculate_lot_size(entry_price, failsafe_sl, balance)

    if lot <= 0:
        log(f"Invalid lot size: {lot} - aborting trade", "ERROR")
        log_signal_attempt(side, "FAILED", f"Invalid lot size: {lot}")
        return None

    log(f"Trade parameters: Entry=${entry_price:.5f} | SL=${failsafe_sl:.5f} | TP=${failsafe_tp:.5f} | Lot={lot:.4f}", "INFO")

    # Open position with SL/TP included — single order_send, no second round-trip
    ticket = open_position(side, lot, sl=failsafe_sl, tp=failsafe_tp)

    if ticket:
        log(_LOG_SEP, "INFO")
        log(f"POSITION OPENED (SET & FORGET TP1)", "INFO")
        log(f"   Ticket: {ticket} | Side: {side} | Entry: ${entry_price:.5f} | Lot: {lot:.4f}", "INFO")
        log(f"   Failsafe SL: ${failsafe_sl:.5f} | Failsafe TP: ${failsafe_tp:.5f}", "INFO")
        log(f"   Waiting for message edit with real SL/TP1...", "INFO")
        log(_LOG_SEP, "INFO")

        log_signal_attempt(side, "SUCCESS", f"Ticket={ticket} | Entry=${entry_price:.5f}")
        position_map[msg_id] = ticket
        entry_prices[ticket] = entry_price
        tp_sl_updated[ticket] = False  # Will be set True when real SL/TP arrives
        log(f"   Stored in position_map: msg_id={msg_id} -> ticket={ticket}", "DEBUG")

        # Return trade details — signal_queue persistence handled by caller (off MT5 thread)
        return (ticket, entry_price, failsafe_sl, lot)
    else:
        log("POSITION OPEN FAILED - returning", "ERROR")
        log_signal_attempt(side, "FAILED", "Position open failed (see above for details)")
        return None


def execute_limit_trade(side: str, msg_id: int, msg_text: str, limit_price: float, zone_low: float, zone_high: float):
    """
    Zone-aware entry: enter at market if price is inside the zone,
    or place a pending order at the zone boundary if price is outside.
    NEVER enters outside the zone.

    Returns:
      ("MARKET", ticket, entry_price, failsafe_sl, lot) on market fill
      ("PENDING", order_ticket, limit_price, failsafe_sl, lot) on pending order
      None on failure.
    Runs inside the MT5 executor thread.
    """
    current_price = get_market_price(side)
    if current_price is None:
        log("Cannot get market price - aborting zone entry", "ERROR")
        log_signal_attempt(side, "FAILED", "Cannot get market price")
        return None

    cp_str = f"${current_price:.2f}"
    log(f"Zone entry: {side} | Price: {cp_str} | Zone: ${zone_low:.2f}-${zone_high:.2f}", "INFO")

    balance = get_account_balance()
    if balance <= 0:
        log(f"Invalid balance: ${balance:.2f} - aborting", "ERROR")
        log_signal_attempt(side, "FAILED", f"Invalid balance: ${balance:.2f}")
        record_entry_stat("limit_placement_failed", side=side, limit_price=limit_price, reason="invalid_balance", balance=balance, current_price=current_price)
        return None

    if not is_market_open():
        log("Market is CLOSED / CLOSE-ONLY - cannot enter", "ERROR")
        log_signal_attempt(side, "FAILED", "Market closed (close-only mode)")
        record_entry_stat("limit_placement_failed", side=side, limit_price=limit_price, reason="market_closed", current_price=current_price)
        return None

    # ── Price INSIDE zone → enter at MARKET immediately ──
    price_in_zone = zone_low <= current_price <= zone_high
    if price_in_zone:
        log(f"Price {cp_str} is INSIDE zone ${zone_low:.2f}-${zone_high:.2f} → MARKET {side}", "INFO")
        entry_price = current_price
        if side == "BUY":
            failsafe_sl = entry_price - FAILSAFE_SL_DISTANCE
            failsafe_tp = entry_price + FAILSAFE_TP_DISTANCE
        else:
            failsafe_sl = entry_price + FAILSAFE_SL_DISTANCE
            failsafe_tp = entry_price - FAILSAFE_TP_DISTANCE

        lot = calculate_lot_size(entry_price, failsafe_sl, balance)
        if lot <= 0:
            log(f"Invalid lot size: {lot} - aborting market entry", "ERROR")
            log_signal_attempt(side, "FAILED", f"Invalid lot size: {lot}")
            return None

        log(f"Market entry: {side} at ~{cp_str} | SL=${failsafe_sl:.2f} | TP=${failsafe_tp:.2f} | Lot={lot:.4f}", "INFO")
        ticket = open_position(side, lot, sl=failsafe_sl, tp=failsafe_tp)
        if ticket:
            log(_LOG_SEP, "INFO")
            log(f"MARKET ENTRY (INSIDE ZONE)", "INFO")
            log(f"   Ticket: {ticket} | {side} at ~{cp_str}", "INFO")
            log(f"   Zone: ${zone_low:.2f} - ${zone_high:.2f}", "INFO")
            log(f"   Failsafe SL: ${failsafe_sl:.2f} | TP: ${failsafe_tp:.2f} | Lot: {lot:.4f}", "INFO")
            log(_LOG_SEP, "INFO")
            log_signal_attempt(side, "SUCCESS", f"Ticket={ticket} | Entry=~{cp_str} (inside zone)")
            position_map[msg_id] = ticket
            entry_prices[ticket] = entry_price
            tp_sl_updated[ticket] = False
            return ("MARKET", ticket, entry_price, failsafe_sl, lot)
        else:
            log("MARKET ENTRY FAILED", "ERROR")
            log_signal_attempt(side, "FAILED", "Market order failed (inside zone)")
            return None

    # ── Price OUTSIDE zone → place pending order at zone boundary ──
    if side == "BUY":
        if current_price < zone_low:
            limit_price = zone_low
        else:
            limit_price = zone_high
    else:  # SELL
        if current_price > zone_high:
            limit_price = zone_high
        else:
            limit_price = zone_low

    log(f"Price {cp_str} is OUTSIDE zone ${zone_low:.2f}-${zone_high:.2f} → pending {side} at ${limit_price:.2f}", "INFO")

    if side == "BUY":
        failsafe_sl = limit_price - FAILSAFE_SL_DISTANCE
        failsafe_tp = limit_price + FAILSAFE_TP_DISTANCE
    else:
        failsafe_sl = limit_price + FAILSAFE_SL_DISTANCE
        failsafe_tp = limit_price - FAILSAFE_TP_DISTANCE

    lot = calculate_lot_size(limit_price, failsafe_sl, balance)
    if lot <= 0:
        log(f"Invalid lot size: {lot} - aborting pending order", "ERROR")
        log_signal_attempt(side, "FAILED", f"Invalid lot size: {lot}")
        record_entry_stat("limit_placement_failed", side=side, limit_price=limit_price, reason="invalid_lot", lot=lot, balance=balance, current_price=current_price)
        return None

    log(f"Pending order: {side} at ${limit_price:.2f} | SL=${failsafe_sl:.2f} | TP=${failsafe_tp:.2f} | Lot={lot:.4f}", "INFO")

    order_ticket = place_limit_order(side, limit_price, lot, failsafe_sl, failsafe_tp)
    if order_ticket:
        log(_LOG_SEP, "INFO")
        log(f"PENDING ORDER PLACED (OUTSIDE ZONE)", "INFO")
        log(f"   Order: {order_ticket} | {side} at ${limit_price:.2f}", "INFO")
        log(f"   Zone: ${zone_low:.2f} - ${zone_high:.2f}", "INFO")
        log(f"   Failsafe SL: ${failsafe_sl:.2f} | TP: ${failsafe_tp:.2f} | Lot: {lot:.4f}", "INFO")
        log(_LOG_SEP, "INFO")
        log_signal_attempt(side, "LIMIT_PLACED", f"Order={order_ticket} | Limit=${limit_price:.2f}")
        return ("PENDING", order_ticket, limit_price, failsafe_sl, lot)
    else:
        log("PENDING ORDER FAILED", "ERROR")
        log_signal_attempt(side, "FAILED", "Pending order placement failed")
        record_entry_stat("limit_placement_failed", side=side, limit_price=limit_price, reason="broker_rejected", current_price=current_price, balance=balance, lot=lot)
        return None

async def main():
    global signal_queue, session_manager, reconnect_monitor
    global bot_start_time, telegram_connected, last_telegram_activity, _telegram_client
    global messages_received, messages_ignored, last_message_time
    global telegram_connection_losses

    # Record bot start time
    bot_start_time = time.time()
    start_timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    signal_queue = SignalQueue("signal_queue.json")
    session_manager = SessionManager("trading_bot_session", "sessions")
    reconnect_monitor = ReconnectMonitor(alert_threshold=5, time_window_minutes=30)
    outcome_tracker = TradeOutcomeTracker(magic=MAGIC)

    client = init_telegram()
    _telegram_client = client
    ensure_mt5_connection()

    log("=" * 70, "INFO")
    log("BOT INITIALIZATION COMPLETE", "INFO")
    log("=" * 70, "INFO")
    log(f"  Bot start time: {start_timestamp}", "INFO")
    log(f"  Main channel ID: {CHANNEL_ID}", "INFO")
    log(f"  Trading symbol: {SYMBOL}", "INFO")
    log(f"  Risk per trade: {RISK_PCT*100:.1f}%", "INFO")
    log(f"  Failsafe SL distance: ${FAILSAFE_SL_DISTANCE:.2f}", "INFO")
    log(f"  Failsafe TP distance: ${FAILSAFE_TP_DISTANCE:.2f}", "INFO")
    log(f"  Strategy: SET & FORGET TP1", "INFO")
    log(f"  Entry Strategy: {ENTRY_STRATEGY}", "INFO")
    log(f"  Zone Wait Timeout: {ZONE_WAIT_TIMEOUT}s", "INFO")
    log(f"  Limit Order Timeout: {LIMIT_ORDER_TIMEOUT}s", "INFO")
    log(f"  Magic number: {MAGIC}", "INFO")
    log(f"  Log level: {LOG_LEVEL}", "INFO")
    log("=" * 70, "INFO")

    # ── Helper: update SL and TP1 on a position from parsed values ──
    async def update_position_sl_tp(ticket: int, sl: float = None, tp1: float = None, source: str = "", all_tp_levels: Dict[int, float] = None):
        """Update SL and/or TP1 on a position. Set-and-forget: once updated, done."""
        pos = await run_mt5(lambda: mt5.positions_get(ticket=ticket))
        if not pos:
            log(f"Position {ticket} not found - may have been closed already", "WARN")
            return

        current_sl = float(pos[0].sl) if pos[0].sl > 0 else 0.0
        current_tp = float(pos[0].tp) if pos[0].tp > 0 else 0.0

        new_sl = sl if sl else current_sl
        new_tp = tp1 if tp1 else current_tp

        # Validate: SL and TP must be on correct sides of entry
        side = "BUY" if pos[0].type == 0 else "SELL"
        entry = entry_prices.get(ticket, float(pos[0].price_open))

        if sl:
            if side == "BUY" and sl >= entry:
                log(f"SL ${sl:.5f} is above entry ${entry:.5f} for BUY - ignoring SL update", "WARN")
                new_sl = current_sl
            elif side == "SELL" and sl <= entry:
                log(f"SL ${sl:.5f} is below entry ${entry:.5f} for SELL - ignoring SL update", "WARN")
                new_sl = current_sl

        if tp1:
            if side == "BUY" and tp1 <= entry:
                log(f"TP1 ${tp1:.5f} is below entry ${entry:.5f} for BUY - ignoring TP update", "WARN")
                new_tp = current_tp
            elif side == "SELL" and tp1 >= entry:
                log(f"TP1 ${tp1:.5f} is above entry ${entry:.5f} for SELL - ignoring TP update", "WARN")
                new_tp = current_tp

        # Only update if something actually changed
        if abs(new_sl - current_sl) < 0.001 and abs(new_tp - current_tp) < 0.001:
            log(f"No SL/TP changes needed for ticket {ticket}", "DEBUG")
            return

        log("=" * 70, "INFO")
        log(f"UPDATING REAL SL/TP1 ON TICKET {ticket}{source}", "INFO")
        log(f"   Old: SL=${current_sl:.5f} | TP=${current_tp:.5f}", "INFO")
        log(f"   New: SL=${new_sl:.5f} | TP=${new_tp:.5f}", "INFO")
        log("=" * 70, "INFO")

        await run_mt5(lambda: set_sl_and_tp(ticket, new_sl, new_tp))
        tp_sl_updated[ticket] = True
        log(f"SET & FORGET: Ticket {ticket} now has real SL/TP1. Broker handles exit.", "INFO")

        # Update outcome tracker with real SL/TP levels
        if outcome_tracker and outcome_tracker.is_tracking(ticket):
            tp_update = all_tp_levels.copy() if all_tp_levels else {}
            if tp1 and 1 not in tp_update:
                tp_update[1] = tp1
            outcome_tracker.update_levels(ticket, sl_price=sl, tp_levels=tp_update if tp_update else None)
            log(f"Outcome tracker updated for ticket {ticket}", "DEBUG")

    # ── Process any message text through the signal pipeline ──
    async def process_message(text: str, msg_id: int, channel_name: str, source: str = ""):
        """
        Core message processing - Set & Forget TP1 Strategy with Zone-Aware Limit Orders.

        Handles:
        1. BUY NOW / SELL NOW signals:
           - LIMIT_ZONE strategy: buffer signal, wait for zone via edit, then limit order
           - MARKET strategy: immediate market order (legacy)
        2. SL / TP updates (new messages) -> update position with real values
        """
        global messages_ignored

        # ── Trade signal (BUY NOW / SELL NOW) ──
        signal = parse_signal(text)
        if signal:
            symbol, side = signal
            log(f"SIGNAL DETECTED{source}: {side} {symbol}", "INFO")
            log_signal_attempt(side, "RECEIVED", f"msg_id={msg_id}{source}")

            # Capture market price at signal reception for virtual trade tracking
            signal_market_price = await run_mt5(lambda: get_market_price(side))
            if signal_market_price:
                log(f"Signal market price at reception: ${signal_market_price:.2f}", "DEBUG")
            else:
                log(f"WARNING: Could not capture market price at signal reception", "WARN")

            # ── LIMIT_ZONE strategy: check for zone, buffer if missing ──
            if ENTRY_STRATEGY == "LIMIT_ZONE":
                zone = parse_entry_zone(text)
                if zone:
                    # Zone already in initial message (rare) → enter zone
                    zone_low, zone_high = zone
                    log(f"Zone found in initial message: ${zone_low:.2f}-${zone_high:.2f}", "INFO")

                    result = await run_mt5(lambda: execute_limit_trade(side, msg_id, text, 0, zone_low, zone_high))
                    if result:
                        result_type = result[0]
                        if result_type == "MARKET":
                            _, ticket, entry_price, fsl, lot = result
                            sl = parse_stop_loss(text)
                            tp1 = parse_tp1(text)
                            all_tps = parse_tp_levels(text)
                            signal_queue.add_signal(signal_type="TRADE", side=side, entry_price=entry_price,
                                                    sl_price=fsl, lot_size=lot, tp_levels={}, msg_id=msg_id, msg_text=text[:100])
                            signal_queue.mark_executed(msg_id)
                            # Register with outcome tracker
                            if outcome_tracker:
                                outcome_tracker.register_trade(ticket, side, entry_price, sl or fsl, all_tps)
                            if sl or tp1:
                                await update_position_sl_tp(ticket, sl, tp1, source, all_tp_levels=all_tps)
                            record_entry_stat("market_entry_in_zone", side=side, entry_price=entry_price, zone_low=zone_low, zone_high=zone_high)
                        else:  # PENDING
                            _, order_ticket, lp, fsl, lot = result
                            sl = parse_stop_loss(text)
                            tp1 = parse_tp1(text)
                            all_tps = parse_tp_levels(text)
                            _pending_limit_orders[msg_id] = {
                                "order_ticket": order_ticket,
                                "side": side,
                                "limit_price": lp,
                                "zone_low": zone_low,
                                "zone_high": zone_high,
                                "failsafe_sl": fsl,
                                "lot": lot,
                                "msg_id": msg_id,
                                "placed_at": time.time(),
                                "sl": sl,
                                "tp1": tp1,
                                "all_tps": all_tps,
                                "signal_market_price": signal_market_price,
                                "price_min_seen": float('inf'),
                                "price_max_seen": float('-inf'),
                            }
                            record_entry_stat("limit_placed", side=side, limit_price=lp, zone_low=zone_low, zone_high=zone_high)
                    return True
                else:
                    # No zone yet → buffer signal and wait for zone via edit
                    _buffered_signals[msg_id] = {
                        "side": side,
                        "symbol": symbol,
                        "text": text,
                        "msg_id": msg_id,
                        "channel_name": channel_name,
                        "buffered_at": time.time(),
                        "signal_market_price": signal_market_price,
                    }
                    record_entry_stat("signal_buffered", side=side)
                    log(f"SIGNAL BUFFERED - waiting for zone via edit (msg_id={msg_id}, timeout={ZONE_WAIT_TIMEOUT}s)", "INFO")
                    log(f"   Buffered signals: {len(_buffered_signals)}", "INFO")
                    return True

            # ── MARKET strategy (legacy): immediate market order ──
            result = await run_mt5(lambda: execute_signal_trade(side, msg_id, text))

            if result:
                ticket, entry_price, failsafe_sl, lot = result

                # Persist to signal queue AFTER MT5 thread is free (file I/O off executor)
                signal_queue.add_signal(
                    signal_type="TRADE",
                    side=side,
                    entry_price=entry_price,
                    sl_price=failsafe_sl,
                    lot_size=lot,
                    tp_levels={},
                    msg_id=msg_id,
                    msg_text=text[:100],
                )
                signal_queue.mark_executed(msg_id)

                # If initial message already contains real SL/TP, apply immediately
                if msg_id in position_map:
                    sl = parse_stop_loss(text)
                    tp1 = parse_tp1(text)
                    all_tps = parse_tp_levels(text)
                    # Register with outcome tracker
                    if outcome_tracker:
                        outcome_tracker.register_trade(ticket, side, entry_price, sl or failsafe_sl, all_tps)
                    if sl or tp1:
                        await update_position_sl_tp(ticket, sl, tp1, source, all_tp_levels=all_tps)

            return True

        # ── Trade closure (TP HIT / SL HIT / Close profit) → cancel pending orders & buffered signals ──
        closure = parse_trade_closure(text)
        if closure:
            cancelled_count = 0
            cleared_count = 0

            is_sl_hit = "SL" in closure.upper()
            # Check ACTUAL live positions instead of stale position_map
            live_positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
            has_open_positions = bool(live_positions and [p for p in live_positions if p.magic == MAGIC])

            # Pending limit orders:
            #   SL HIT → always cancel (trade thesis invalidated, direction wrong)
            #   TP HIT + open positions → cancel (provider closure applies to us)
            #   TP HIT + NO open positions → KEEP limits active (our entry hasn't
            #     filled yet; limit is still valid with its own SL/TP and timeout;
            #     provider's TP doesn't apply to us since we never entered)
            if is_sl_hit or has_open_positions:
                for mid, info in list(_pending_limit_orders.items()):
                    ot = info["order_ticket"]
                    log(f"TRADE CLOSURE '{closure}' → cancelling pending limit order {ot} (msg_id={mid})", "INFO")
                    await run_mt5(lambda _ot=ot: cancel_limit_order(_ot))
                    record_entry_stat("limit_invalidated", side=info["side"], limit_price=info["limit_price"],
                                      reason=f"channel_closure: {closure}")
                    # Register virtual trade for tracking what would have happened
                    if outcome_tracker and info.get("all_tps"):
                        vt_id = -abs(mid)
                        sl_val = info.get("sl") or info.get("failsafe_sl") or 0
                        vt_entry = info.get("signal_market_price") or info["limit_price"]
                        outcome_tracker.register_virtual_trade(
                            vt_id, info["side"], vt_entry,
                            sl_val, info["all_tps"],
                            cancel_reason=f"channel_closure:{closure}")
                        log(f"VIRTUAL TRADE registered: id={vt_id} side={info['side']} entry=${vt_entry:.2f}", "INFO")
                    cancelled_count += 1
                _pending_limit_orders.clear()
            elif _pending_limit_orders:
                log(f"Trade closure '{closure}' but no open positions — keeping {len(_pending_limit_orders)} pending limit(s) active (entry still valid)", "INFO")

            # Always clear buffered signals (provider already closed, no point waiting for zone)
            for mid, buf in list(_buffered_signals.items()):
                log(f"TRADE CLOSURE '{closure}' → clearing buffered signal (msg_id={mid})", "INFO")
                record_entry_stat("zone_wait_timeout", side=buf["side"])
                # Register virtual trade if signal text had TP levels
                if outcome_tracker:
                    buf_tps = parse_tp_levels(buf.get("text", ""))
                    buf_sl = parse_stop_loss(buf.get("text", ""))
                    if buf_tps:
                        vt_id = -abs(mid)
                        vt_entry = buf.get("signal_market_price") or 0
                        if vt_entry:
                            outcome_tracker.register_virtual_trade(
                                vt_id, buf["side"], vt_entry, buf_sl or 0, buf_tps,
                                cancel_reason=f"channel_closure:{closure}")
                            log(f"VIRTUAL TRADE registered: id={vt_id} side={buf['side']} entry=${vt_entry:.2f}", "INFO")
                cleared_count += 1
            _buffered_signals.clear()

            if cancelled_count or cleared_count:
                log(f"Trade closure cleanup: cancelled {cancelled_count} pending orders, cleared {cleared_count} buffered signals", "INFO")
            elif not _pending_limit_orders:
                log(f"Trade closure '{closure}' received - no pending orders or buffered signals to clean up", "DEBUG")

            return True

        # ── SL / TP update in a follow-up message (not an edit) ──
        sl = parse_stop_loss(text)
        tp1 = parse_tp1(text)
        tp_levels_parsed = parse_tp_levels(text)

        # If TP1 not found directly, try to get it from tp_levels
        if not tp1 and tp_levels_parsed and 1 in tp_levels_parsed:
            tp1 = tp_levels_parsed[1]

        if sl or tp1:
            # Find the most recent bot position to update
            positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
            if not positions:
                log(f"SL/TP update received but no open positions{source}", "WARN")
                return True

            # Find the most recent bot position
            bot_positions = [p for p in positions if p.magic == MAGIC]
            if not bot_positions:
                log(f"SL/TP update received but no bot positions found{source}", "WARN")
                return True

            ticket = int(bot_positions[-1].ticket)
            log(f"Applying SL/TP from new message to most recent bot position: ticket {ticket}{source}", "INFO")
            await update_position_sl_tp(ticket, sl, tp1, source, all_tp_levels=tp_levels_parsed)
            return True

        # Not recognized
        return False

    # ══════════════════════════════════════════════════════════════════════
    # Push-based message handler
    # ══════════════════════════════════════════════════════════════════════
    @client.on(events.NewMessage())
    async def on_new_message(event):
        """Handle new Telegram messages from main or test channel"""
        global messages_received, messages_ignored, last_message_time, last_telegram_activity

        try:
            log(f"INCOMING (PUSH): chat_id={event.chat_id} | msg_id={event.message.id}", "INFO")

            # Deduplicate
            if event.message.id in _processed_msg_ids:
                log(f"msg_id={event.message.id} already processed (via poll) - skipping", "DEBUG")
                return
            _processed_msg_ids.add(event.message.id)
            if len(_processed_msg_ids) > _MAX_PROCESSED_IDS:
                # Keep only the most recent half to avoid reprocessing
                sorted_ids = sorted(_processed_msg_ids)
                _processed_msg_ids.difference_update(sorted_ids[:len(sorted_ids) // 2])

            messages_received += 1

            # Filter by allowed chat IDs
            if event.chat_id not in ALLOWED_CHAT_IDS:
                messages_ignored += 1
                log(f"Ignored: chat_id={event.chat_id}", "DEBUG")
                return

            last_telegram_activity = time.time()
            last_message_time = time.time()

            text = extract_message_text(event.message).strip()
            msg_id = event.message.id
            channel_name = "[MAIN]"

            if DEBUG_LOG_ALL_MESSAGES:
                log(f"[RAW] chat_id={event.chat_id} | {channel_name} | {text[:80]}", "DEBUG")

            # Calculate delivery delay
            msg_timestamp = event.message.date.timestamp()
            delivery_delay = time.time() - msg_timestamp
            delay_warning = ""
            if delivery_delay > 10:
                delay_warning = f" DELAYED: {delivery_delay:.0f}s"

            log("=" * 70, "INFO")
            log(f"{channel_name} NEW MESSAGE RECEIVED{delay_warning}", "INFO")
            log(f"   Message ID: {msg_id}", "INFO")
            log(f"   Chat ID: {event.chat_id}", "INFO")
            log(f"   Length: {len(text)} chars", "INFO")
            if delivery_delay > 10:
                log(f"   Sent: {event.message.date.strftime('%H:%M:%S')} | Received: {time.strftime('%H:%M:%S')} (delay {delivery_delay:.0f}s)", "WARN")
            log(f"   Preview: {text[:150]}", "INFO")
            log("=" * 70, "INFO")

            handled = await process_message(text, msg_id, channel_name)
            if not handled:
                log("Message not recognized as signal or SL/TP update - ignoring", "DEBUG")
                messages_ignored += 1

        except Exception as e:
            log(f"CRITICAL EXCEPTION in on_new_message: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")

    # ══════════════════════════════════════════════════════════════════════
    # Edit handler
    # ══════════════════════════════════════════════════════════════════════
    @client.on(events.MessageEdited())
    async def on_edit(event):
        """
        Handle edited messages - KEY HANDLER for Set & Forget + Zone-Aware Limit Orders.

        Priority order:
        1. Buffered signal (waiting for zone) → parse zone, place limit order
        2. Pending limit order → parse SL/TP, modify order
        3. Position map → update position SL/TP (set & forget)
        4. Fallback → find most recent bot position
        """
        global messages_received, messages_ignored, last_telegram_activity

        try:
            log(f"EDIT (PUSH): chat_id={event.chat_id} | msg_id={event.message.id}", "INFO")

            last_telegram_activity = time.time()

            messages_received += 1

            if event.chat_id not in ALLOWED_CHAT_IDS:
                messages_ignored += 1
                log(f"Edit ignored - chat_id {event.chat_id} not in allowed list", "DEBUG")
                return

            if DEBUG_LOG_ALL_MESSAGES:
                preview = extract_message_text(event.message).strip()[:80]
                log(f"[RAW EDIT] chat_id={event.chat_id} | {preview}", "DEBUG")

            text = extract_message_text(event.message).strip()
            msg_id = event.message.id
            channel_name = "[MAIN]"

            log("=" * 70, "INFO")
            log(f"{channel_name} MESSAGE EDITED", "INFO")
            log(f"   Message ID: {msg_id}", "INFO")
            log(f"   Preview: {text[:200]}", "INFO")
            log("=" * 70, "INFO")

            # ── 1. Check if this edit provides a zone for a BUFFERED SIGNAL ──
            if msg_id in _buffered_signals:
                buf = _buffered_signals[msg_id]
                side = buf["side"]
                wait_time = time.time() - buf["buffered_at"]
                log(f"BUFFERED SIGNAL FOUND for msg_id={msg_id} (waited {wait_time:.1f}s) - checking for zone...", "INFO")

                zone = parse_entry_zone(text)
                if zone:
                    zone_low, zone_high = zone
                    log(f"ZONE RECEIVED via edit: ${zone_low:.2f}-${zone_high:.2f}", "INFO")
                    record_entry_stat("zone_from_edit", side=side, zone_low=zone_low, zone_high=zone_high, wait_time=wait_time)

                    result = await run_mt5(lambda: execute_limit_trade(side, msg_id, text, 0, zone_low, zone_high))
                    if result:
                        result_type = result[0]
                        if result_type == "MARKET":
                            _, ticket, entry_price, fsl, lot = result
                            sl = parse_stop_loss(text)
                            tp1 = parse_tp1(text)
                            all_tps = parse_tp_levels(text)
                            signal_queue.add_signal(signal_type="TRADE", side=side, entry_price=entry_price,
                                                    sl_price=fsl, lot_size=lot, tp_levels={}, msg_id=msg_id, msg_text=text[:100])
                            signal_queue.mark_executed(msg_id)
                            # Register with outcome tracker
                            if outcome_tracker:
                                outcome_tracker.register_trade(ticket, side, entry_price, sl or fsl, all_tps)
                            if sl or tp1:
                                await update_position_sl_tp(ticket, sl, tp1, "", all_tp_levels=all_tps)
                            record_entry_stat("market_entry_in_zone", side=side, entry_price=entry_price, zone_low=zone_low, zone_high=zone_high)
                        else:  # PENDING
                            _, order_ticket, lp, fsl, lot = result
                            sl = parse_stop_loss(text)
                            tp1 = parse_tp1(text)
                            all_tps = parse_tp_levels(text)
                            _pending_limit_orders[msg_id] = {
                                "order_ticket": order_ticket,
                                "side": side,
                                "limit_price": lp,
                                "zone_low": zone_low,
                                "zone_high": zone_high,
                                "failsafe_sl": fsl,
                                "lot": lot,
                                "msg_id": msg_id,
                                "placed_at": time.time(),
                                "sl": sl,
                                "tp1": tp1,
                                "all_tps": all_tps,
                                "signal_market_price": buf.get("signal_market_price"),
                                "price_min_seen": float('inf'),
                                "price_max_seen": float('-inf'),
                            }
                            record_entry_stat("limit_placed", side=side, limit_price=lp, zone_low=zone_low, zone_high=zone_high)

                    # Remove from buffered signals regardless of success
                    del _buffered_signals[msg_id]
                    return
                else:
                    # Edit doesn't have zone yet, but may have SL/TP - update buffer
                    sl = parse_stop_loss(text)
                    tp1 = parse_tp1(text)
                    tp_levels_buf = parse_tp_levels(text)
                    if sl:
                        buf["sl"] = sl
                    if tp1:
                        buf["tp1"] = tp1
                    if tp_levels_buf:
                        buf["text"] = text  # Update text so TPs are available for virtual trade
                    log(f"Edit for buffered signal has no zone yet (SL={'yes' if sl else 'no'}, TP1={'yes' if tp1 else 'no'}, TPs={len(tp_levels_buf)})", "INFO")
                    return

            # ── 2. Check if this edit updates a PENDING LIMIT ORDER ──
            if msg_id in _pending_limit_orders:
                pending = _pending_limit_orders[msg_id]
                order_ticket = pending["order_ticket"]
                log(f"PENDING LIMIT ORDER edit: order={order_ticket} (msg_id={msg_id})", "INFO")

                sl = parse_stop_loss(text)
                tp1 = parse_tp1(text)
                tp_levels_parsed = parse_tp_levels(text)
                if not tp1 and tp_levels_parsed and 1 in tp_levels_parsed:
                    tp1 = tp_levels_parsed[1]

                # Always update all_tps when edit has TP levels (for virtual trade accuracy)
                if tp_levels_parsed:
                    existing_tps = pending.get("all_tps", {})
                    existing_tps.update(tp_levels_parsed)
                    pending["all_tps"] = existing_tps

                if sl or tp1:
                    new_sl = sl if sl else pending.get("sl")
                    new_tp = tp1 if tp1 else pending.get("tp1")
                    if sl:
                        pending["sl"] = sl
                    if tp1:
                        pending["tp1"] = tp1

                    # Try to modify the pending order with real SL/TP
                    success = await run_mt5(lambda: modify_limit_order_sl_tp(order_ticket, new_sl, new_tp))
                    if success:
                        log(f"Pending limit order {order_ticket} SL/TP updated: SL={f'${new_sl:.2f}' if new_sl else 'N/A'} TP={f'${new_tp:.2f}' if new_tp else 'N/A'}", "INFO")
                    else:
                        log(f"Failed to modify pending order {order_ticket} - may already be filled", "WARN")

                    # Also check if order was filled and now we need to update the position
                    pos_ticket = await run_mt5(lambda: find_position_from_order(order_ticket))
                    if pos_ticket:
                        log(f"Order {order_ticket} already filled → position {pos_ticket}, updating SL/TP", "INFO")
                        await update_position_sl_tp(pos_ticket, sl, tp1, " (EDIT-LIMIT)", all_tp_levels=tp_levels_parsed)
                return

            # ── 3. Standard edit: update existing position SL/TP (set & forget) ──
            sl = parse_stop_loss(text)
            tp1 = parse_tp1(text)
            tp_levels_parsed = parse_tp_levels(text)

            if not tp1 and tp_levels_parsed and 1 in tp_levels_parsed:
                tp1 = tp_levels_parsed[1]

            if not sl and not tp1:
                log(f"Edit contains no SL/TP data - ignoring (msg_id={msg_id})", "DEBUG")
                return

            log(f"Parsed from edit: SL={f'${sl:.5f}' if sl else 'None'} | TP1={f'${tp1:.5f}' if tp1 else 'None'}", "INFO")

            # Find the position for this message
            if msg_id in position_map:
                ticket = position_map[msg_id]
                log(f"   Found mapped ticket: {ticket} for msg_id={msg_id}", "DEBUG")
            else:
                log(f"Message ID {msg_id} not in position_map - looking for recent bot positions", "WARN")
                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                if not positions:
                    log("No open positions at all - ignoring edit", "WARN")
                    return

                bot_positions = [p for p in positions if p.magic == MAGIC]
                if not bot_positions:
                    log("No bot positions found - ignoring edit", "WARN")
                    return

                ticket = int(bot_positions[-1].ticket)
                log(f"   Using most recent bot position: ticket {ticket}", "INFO")

            await update_position_sl_tp(ticket, sl, tp1, " (EDIT)", all_tp_levels=tp_levels_parsed)

        except Exception as e:
            log(f"CRITICAL EXCEPTION in on_edit: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")

    # ══════════════════════════════════════════════════════════════════════
    # Limit order & buffer monitor (runs every 1 second)
    # ══════════════════════════════════════════════════════════════════════
    async def limit_order_monitor():
        """
        Every 1 second:
        - Check buffered signals for ZONE_WAIT_TIMEOUT → fallback to market order
        - Check pending limit orders for fills, price invalidation, or timeout
        """
        log("Limit order monitor started (1s interval)", "INFO")
        while True:
            try:
                await asyncio.sleep(1)

                now = time.time()

                # ── Check BUFFERED SIGNALS for zone wait timeout ──
                expired_buffers = []
                for mid, buf in list(_buffered_signals.items()):
                    elapsed = now - buf["buffered_at"]
                    if elapsed >= ZONE_WAIT_TIMEOUT:
                        expired_buffers.append(mid)

                for mid in expired_buffers:
                    buf = _buffered_signals.pop(mid)
                    side = buf["side"]
                    log(f"ZONE WAIT TIMEOUT: msg_id={mid} waited {ZONE_WAIT_TIMEOUT}s - no zone received, cancelling signal", "WARN")
                    record_entry_stat("zone_wait_timeout", side=side)
                    # Register virtual trade if signal text had TP levels
                    if outcome_tracker:
                        buf_tps = parse_tp_levels(buf.get("text", ""))
                        buf_sl = parse_stop_loss(buf.get("text", ""))
                        if buf_tps:
                            vt_id = -abs(mid)
                            vt_entry = buf.get("signal_market_price") or 0
                            if vt_entry:
                                outcome_tracker.register_virtual_trade(
                                    vt_id, side, vt_entry, buf_sl or 0, buf_tps,
                                    cancel_reason="zone_wait_timeout")
                                log(f"VIRTUAL TRADE registered: id={vt_id} side={side} entry=${vt_entry:.2f}", "INFO")

                # ── Check PENDING LIMIT ORDERS ──
                completed_orders = []
                for mid, info in list(_pending_limit_orders.items()):
                    order_ticket = info["order_ticket"]
                    side = info["side"]
                    limit_price = info["limit_price"]
                    elapsed = now - info["placed_at"]

                    # 1. Check if order was filled (position exists)
                    #    Also check if the pending order still exists on the broker.
                    #    If it's gone from pending orders AND we can't find a deal, log a warning.
                    pos_ticket = await run_mt5(lambda: find_position_from_order(order_ticket))
                    if not pos_ticket:
                        # Double-check: is the pending order still alive on the broker?
                        pending_order = await run_mt5(lambda: mt5.orders_get(ticket=order_ticket))
                        if not pending_order:
                            # Order is gone from broker and no deal found - it was likely filled
                            # Try harder: search full history
                            log(f"Pending order {order_ticket} no longer on broker - searching full deal history...", "WARN")
                            now_dt = datetime.now()
                            all_deals = await run_mt5(lambda: mt5.history_deals_get(now_dt - timedelta(days=7), now_dt + timedelta(minutes=1)))
                            if all_deals:
                                for d in all_deals:
                                    if d.order == order_ticket and d.entry == 0:
                                        pos_ticket = d.position_id
                                        log(f"Found fill in extended history: order {order_ticket} → position {pos_ticket}", "INFO")
                                        break
                            if not pos_ticket:
                                # Check if this order was intentionally canceled by trade closure
                                if mid not in _pending_limit_orders:
                                    log(f"Order {order_ticket} was canceled (trade closure) - skipping", "DEBUG")
                                    completed_orders.append(mid)
                                    continue
                                # Last resort: check open positions for untracked fill
                                all_positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                                if all_positions:
                                    tracked_tickets = set(position_map.values())
                                    for p in all_positions:
                                        if p.magic == MAGIC and int(p.ticket) not in tracked_tickets:
                                            pos_ticket = int(p.ticket)
                                            log(f"Found untracked position {pos_ticket} (MAGIC={MAGIC}) - likely filled from order {order_ticket}", "INFO")
                                            break
                                if not pos_ticket:
                                    # Extra fallback: check order history for filled state
                                    ot_cap = order_ticket  # capture for lambda
                                    now_oh = datetime.now()
                                    hist_orders = await run_mt5(lambda: mt5.history_orders_get(now_oh - timedelta(days=7), now_oh + timedelta(minutes=1)))
                                    if hist_orders:
                                        for ho in hist_orders:
                                            if ho.ticket == ot_cap and hasattr(ho, 'position_id') and ho.position_id:
                                                pos_ticket = ho.position_id
                                                log(f"Found filled order via order history: order {ot_cap} → position {pos_ticket}", "INFO")
                                                break
                                if not pos_ticket:
                                    log(f"Order {order_ticket} disappeared without a fill - broker may have removed it", "ERROR")
                                    _zl, _zh = info["zone_low"], info["zone_high"]
                                    _pmin, _pmax = info.get("price_min_seen", float('inf')), info.get("price_max_seen", float('-inf'))
                                    _best = (_pmin <= _zl) if side == "BUY" else (_pmax >= _zh)
                                    _worst = (_pmin <= _zh) if side == "BUY" else (_pmax >= _zl)
                                    record_entry_stat("limit_invalidated", side=side, limit_price=limit_price, reason="order_disappeared",
                                                      best_reached=_best, worst_reached=_worst, zone_low=_zl, zone_high=_zh, price_min=_pmin, price_max=_pmax)
                                    # Register virtual trade for tracking what would have happened
                                    if outcome_tracker and info.get("all_tps"):
                                        vt_id = -abs(mid)
                                        vt_sl = info.get("sl") or info.get("failsafe_sl") or 0
                                        vt_entry = info.get("signal_market_price") or limit_price
                                        outcome_tracker.register_virtual_trade(
                                            vt_id, side, vt_entry, vt_sl, info["all_tps"],
                                            cancel_reason="order_disappeared")
                                        log(f"VIRTUAL TRADE registered: id={vt_id} side={side} entry=${vt_entry:.2f}", "INFO")
                                    completed_orders.append(mid)
                                    continue
                    if pos_ticket:
                        try:
                            fill_price = await run_mt5(lambda: get_fill_price_from_order(order_ticket))
                            slippage = abs(fill_price - limit_price) if fill_price else 0
                            log(_LOG_SEP, "INFO")
                            log(f"LIMIT ORDER FILLED!", "INFO")
                            log(f"   Order: {order_ticket} → Position: {pos_ticket}", "INFO")
                            if fill_price is not None:
                                log(f"   Fill: ${fill_price:.2f} | Limit: ${limit_price:.2f} | Slippage: ${slippage:.2f}", "INFO")
                            else:
                                log(f"   Fill: N/A | Limit: ${limit_price:.2f} | Slippage: N/A (fill price unavailable)", "WARN")
                            log(f"   Time to fill: {elapsed:.1f}s", "INFO")
                            log(_LOG_SEP, "INFO")

                            # Map position for SL/TP edits
                            position_map[mid] = pos_ticket
                            if fill_price:
                                entry_prices[pos_ticket] = fill_price

                            record_entry_stat("limit_fill", side=side, limit_price=limit_price,
                                              fill_price=fill_price, slippage=slippage, fill_time=elapsed,
                                              best_reached=True, worst_reached=True,
                                              zone_low=info["zone_low"], zone_high=info["zone_high"],
                                              price_min=info.get("price_min_seen"), price_max=info.get("price_max_seen"))

                            # Apply SL/TP if we have them
                            # Register with outcome tracker using ALL TP levels from signal
                            actual_entry = fill_price or limit_price
                            all_tps_from_signal = info.get("all_tps", {})
                            if not all_tps_from_signal and info.get("tp1"):
                                all_tps_from_signal = {1: info["tp1"]}
                            if outcome_tracker:
                                outcome_tracker.register_trade(pos_ticket, side, actual_entry, info.get("sl") or info.get("failsafe_sl"), all_tps_from_signal)

                            sl_val = info.get("sl")
                            tp_val = info.get("tp1")
                            if sl_val or tp_val:
                                await update_position_sl_tp(pos_ticket, sl_val, tp_val, " (LIMIT-FILL)", all_tp_levels=all_tps_from_signal)
                        except Exception as fill_err:
                            log(f"Error processing filled order {order_ticket} → position {pos_ticket}: {fill_err}", "ERROR")
                            # Still map the position so SL/TP edits can work
                            position_map[mid] = pos_ticket

                        completed_orders.append(mid)
                        continue

                    # 2. Check timeout (very generous - only as a safety net)
                    if elapsed >= LIMIT_ORDER_TIMEOUT:
                        log(f"LIMIT ORDER TIMEOUT: order={order_ticket} after {elapsed:.0f}s ({elapsed/3600:.1f}h) - cancelling", "WARN")
                        cancelled = await run_mt5(lambda: cancel_limit_order(order_ticket))
                        _zl, _zh = info["zone_low"], info["zone_high"]
                        _pmin, _pmax = info.get("price_min_seen", float('inf')), info.get("price_max_seen", float('-inf'))
                        _best = (_pmin <= _zl) if side == "BUY" else (_pmax >= _zh)
                        _worst = (_pmin <= _zh) if side == "BUY" else (_pmax >= _zl)
                        record_entry_stat("limit_timeout", side=side, limit_price=limit_price, elapsed=elapsed,
                                          best_reached=_best, worst_reached=_worst, zone_low=_zl, zone_high=_zh, price_min=_pmin, price_max=_pmax)
                        # Register virtual trade for tracking what would have happened
                        if outcome_tracker and info.get("all_tps"):
                            vt_id = -abs(mid)
                            vt_sl = info.get("sl") or info.get("failsafe_sl") or 0
                            vt_entry = info.get("signal_market_price") or limit_price
                            outcome_tracker.register_virtual_trade(
                                vt_id, side, vt_entry, vt_sl, info["all_tps"],
                                cancel_reason=f"limit_timeout:{elapsed:.0f}s")
                            log(f"VIRTUAL TRADE registered: id={vt_id} side={side} entry=${vt_entry:.2f}", "INFO")
                        completed_orders.append(mid)
                        continue

                    # 3. Price invalidation: cancel if price already hit where SL or TP would be
                    current_price = await run_mt5(lambda: get_market_price(side))
                    if current_price:
                        # Update price watermarks for zone entry tracking
                        info["price_min_seen"] = min(info.get("price_min_seen", float('inf')), current_price)
                        info["price_max_seen"] = max(info.get("price_max_seen", float('-inf')), current_price)

                        sl_val = info.get("sl") or info.get("failsafe_sl")
                        tp_val = info.get("tp1")

                        invalidated = False
                        reason = ""

                        if sl_val:
                            if side == "BUY" and current_price <= sl_val:
                                invalidated = True
                                reason = f"Price ${current_price:.2f} <= SL ${sl_val:.2f}"
                            elif side == "SELL" and current_price >= sl_val:
                                invalidated = True
                                reason = f"Price ${current_price:.2f} >= SL ${sl_val:.2f}"

                        if not invalidated and tp_val:
                            if side == "BUY" and current_price >= tp_val:
                                invalidated = True
                                reason = f"Price ${current_price:.2f} >= TP ${tp_val:.2f}"
                            elif side == "SELL" and current_price <= tp_val:
                                invalidated = True
                                reason = f"Price ${current_price:.2f} <= TP ${tp_val:.2f}"

                        if invalidated:
                            # Determine if we missed a winning trade (TP hit) vs avoided a loser (SL hit)
                            if "TP" in reason:
                                log(f"LIMIT ORDER INVALIDATED (MISSED WINNER): order={order_ticket} | {reason}", "WARN")
                                log(f"   Trade was valid but limit at ${limit_price:.2f} never filled - price moved to TP without us", "WARN")
                            else:
                                log(f"LIMIT ORDER INVALIDATED (AVOIDED LOSS): order={order_ticket} | {reason}", "INFO")
                            cancelled = await run_mt5(lambda: cancel_limit_order(order_ticket))
                            _zl, _zh = info["zone_low"], info["zone_high"]
                            _pmin, _pmax = info.get("price_min_seen", float('inf')), info.get("price_max_seen", float('-inf'))
                            _best = (_pmin <= _zl) if side == "BUY" else (_pmax >= _zh)
                            _worst = (_pmin <= _zh) if side == "BUY" else (_pmax >= _zl)
                            record_entry_stat("limit_invalidated", side=side, limit_price=limit_price, reason=reason,
                                              best_reached=_best, worst_reached=_worst, zone_low=_zl, zone_high=_zh, price_min=_pmin, price_max=_pmax)
                            # Register virtual trade for tracking what would have happened
                            if outcome_tracker and info.get("all_tps"):
                                vt_id = -abs(mid)
                                vt_sl = info.get("sl") or info.get("failsafe_sl") or 0
                                vt_entry = info.get("signal_market_price") or limit_price
                                outcome_tracker.register_virtual_trade(
                                    vt_id, side, vt_entry, vt_sl, info["all_tps"],
                                    cancel_reason=f"price_invalidated:{reason}")
                                log(f"VIRTUAL TRADE registered: id={vt_id} side={side} entry=${vt_entry:.2f}", "INFO")
                            completed_orders.append(mid)
                            continue

                for mid in completed_orders:
                    _pending_limit_orders.pop(mid, None)

            except Exception as e:
                log(f"CRITICAL: Limit order monitor exception: {str(e)}", "ERROR")
                import traceback
                log(f"   {traceback.format_exc()}", "ERROR")
                await asyncio.sleep(5)

    # ── Signal log printer ──
    _last_signal_log_len = 0

    async def print_signal_log():
        """Print signal log every 5 minutes, but only when there are new entries."""
        nonlocal _last_signal_log_len
        while True:
            try:
                await asyncio.sleep(300)

                if signal_log and len(signal_log) > _last_signal_log_len:
                    new_entries = signal_log[_last_signal_log_len:]
                    _last_signal_log_len = len(signal_log)
                    log("=" * 70, "INFO")
                    log(f"SIGNAL LOG ({len(new_entries)} new entries)", "INFO")
                    log("=" * 70, "INFO")
                    for entry in new_entries[-10:]:
                        msg = f"  {entry['timestamp']} | {entry['signal']:4s} | {entry['result']:10s} | {entry['detail']}"
                        log(msg, "INFO")
                    log("=" * 70, "INFO")

            except Exception as e:
                log(f"CRITICAL: Signal log printer exception: {str(e)}", "ERROR")
                import traceback
                log(f"   {traceback.format_exc()}", "ERROR")
                await asyncio.sleep(10)

    async def trade_outcome_monitor():
        """
        Every 2 seconds, check current price against TP1-TP5, BE, and SL
        for all trades being tracked by the outcome tracker.

        Shadow tracking: when the real position closes (e.g. at TP1), the
        tracker keeps monitoring price to record which remaining levels
        (TP2-TP5, SL) would have been hit.  This allows evaluating
        alternative hold strategies from the recorded data.

        Tracking finalizes when the highest TP or SL is hit, or after 24h.
        """
        log("Trade outcome monitor started (0.5s interval, shadow tracking enabled)", "INFO")
        while True:
            try:
                await asyncio.sleep(0.5)

                if not outcome_tracker:
                    continue

                active_tickets = outcome_tracker.get_active_tickets()
                if not active_tickets:
                    continue

                # Get current market price
                tick = await run_mt5(lambda: mt5.symbol_info_tick(SYMBOL))
                if tick is None:
                    continue
                bid = float(tick.bid)
                ask = float(tick.ask)

                # Sanity check: skip stale/bogus ticks (bid or ask near zero, or spread > $5)
                if bid < 100 or ask < 100 or abs(ask - bid) > 5:
                    log(f"OUTCOME TRACKER: Skipping bogus tick (bid=${bid:.2f} ask=${ask:.2f})", "WARN")
                    continue

                for ticket in active_tickets:
                    info = outcome_tracker.get_trade_info(ticket)
                    if info is None:
                        continue

                    # Use bid for BUY exits (selling), ask for SELL exits (buying)
                    current_price = bid if info["side"] == "BUY" else ask

                    # Sanity check: if price is absurdly far from entry, skip (stale MT5 tick)
                    entry = info.get("entry_price", 0)
                    if entry > 0 and abs(current_price - entry) > 50:
                        log(f"OUTCOME TRACKER: Skipping stale tick for ticket {ticket} "
                            f"(price=${current_price:.2f} vs entry=${entry:.2f}, diff=${abs(current_price - entry):.2f})", "WARN")
                        continue

                    newly_hit = outcome_tracker.check_levels(ticket, current_price)
                    for level in newly_hit:
                        if info.get("virtual"):
                            tag = " [VIRTUAL]"
                        elif info.get("position_closed"):
                            tag = " [SHADOW]"
                        else:
                            tag = ""
                        log(f"OUTCOME TRACKER: Ticket {ticket} hit {level} at ${current_price:.2f}{tag}", "INFO")

                    # If position is already shadow-tracked, check if we should finalize
                    if outcome_tracker.is_shadow_tracking(ticket):
                        if outcome_tracker.should_finalize(ticket):
                            seq = info.get("sequence_so_far", [])
                            seq_str = " → ".join(seq) if seq else "NONE"
                            profit = info.get("position_profit", 0.0) or 0.0
                            # Determine why shadow tracking ended
                            levels_hit = set(info.get("levels_hit", []))
                            tp_nums = [k for k in info["tp_levels"].keys() if isinstance(k, int)]
                            highest_tp = f"TP{max(tp_nums)}" if tp_nums else None
                            if highest_tp and highest_tp in levels_hit:
                                reason = f"shadow:{highest_tp}_hit"
                            elif "SL" in levels_hit:
                                reason = "shadow:SL_hit"
                            else:
                                reason = "shadow:timeout"
                            virt_tag = "VIRTUAL" if info.get("virtual") else "SHADOW"
                            log(f"OUTCOME TRACKER: Ticket {ticket} {virt_tag} tracking FINALIZED | "
                                f"P&L=${profit:.2f} | Sequence: {seq_str} | Reason: {reason}", "INFO")
                            outcome_tracker.close_trade(ticket, profit, reason)
                        continue

                    # Position still open — check if broker closed it
                    pos = await run_mt5(lambda t=ticket: mt5.positions_get(ticket=t))
                    if not pos:
                        # Position closed by broker → start shadow tracking
                        final_profit = 0.0
                        close_price = 0.0
                        close_reason = "unknown"
                        now_dt = datetime.now()
                        deals = await run_mt5(lambda: mt5.history_deals_get(
                            now_dt - timedelta(hours=24), now_dt + timedelta(minutes=1)))
                        if deals:
                            for d in deals:
                                if d.position_id == ticket and d.entry == 1:  # DEAL_ENTRY_OUT
                                    final_profit = float(d.profit)
                                    close_price = float(d.price)
                                    close_reason = d.comment if d.comment else "broker"
                                    break

                        seq = info.get("sequence_so_far", [])
                        seq_str = " → ".join(seq) if seq else "NONE"
                        log(f"OUTCOME TRACKER: Ticket {ticket} position CLOSED | P&L=${final_profit:.2f} | "
                            f"Close price=${close_price:.2f} | Sequence so far: {seq_str} | Reason: {close_reason}", "INFO")
                        log(f"OUTCOME TRACKER: Starting SHADOW TRACKING for ticket {ticket} "
                            f"(will monitor until highest TP or SL is hit, max 24h)", "INFO")
                        outcome_tracker.mark_position_closed(ticket, final_profit, close_reason, close_price)

                        # Clean up position tracking dicts so trade closure handler
                        # correctly sees no open positions for this ticket
                        tp_sl_updated.pop(ticket, None)
                        entry_prices.pop(ticket, None)
                        for mid_key, tid in list(position_map.items()):
                            if tid == ticket:
                                position_map.pop(mid_key, None)
                                break

            except Exception as e:
                log(f"Trade outcome monitor error: {str(e)}", "ERROR")
                import traceback
                log(f"   {traceback.format_exc()}", "ERROR")
                await asyncio.sleep(5)

    # ══════════════════════════════════════════════════════════════════════
    # Start background monitoring tasks
    # ══════════════════════════════════════════════════════════════════════
    log("Starting background monitoring tasks...", "INFO")

    asyncio.create_task(heartbeat_monitor())
    log("  Heartbeat monitor task created", "INFO")

    asyncio.create_task(connection_health_monitor())
    log("  Connection health monitor task created", "INFO")

    asyncio.create_task(monitor_stale_failsafe())
    log("  Stale failsafe monitor task created", "INFO")

    asyncio.create_task(print_signal_log())
    log("  Signal log printer task created", "INFO")

    asyncio.create_task(cleanup_and_report())
    log("  Cleanup and reporting task created", "INFO")

    asyncio.create_task(mt5_cache_refresher())
    log("  MT5 cache refresher task created", "INFO")

    asyncio.create_task(limit_order_monitor())
    log("  Limit order & buffer monitor task created (1s interval)", "INFO")

    asyncio.create_task(trade_outcome_monitor())
    log("  Trade outcome monitor task created (0.5s interval)", "INFO")

    # ══════════════════════════════════════════════════════════════════════
    # Connect to Telegram
    # ══════════════════════════════════════════════════════════════════════
    log("Connecting to Telegram...", "INFO")
    await client.start()
    telegram_connected = True
    last_telegram_activity = time.time()
    log("Telegram connected!", "INFO")

    # CRITICAL: Fetch dialogs to initialize Telethon's internal update state
    log("Initializing Telegram update state (get_dialogs)...", "INFO")
    try:
        dialogs = await client.get_dialogs()
        log(f"Loaded {len(dialogs)} dialogs - update state initialized", "INFO")
    except Exception as e:
        log(f"get_dialogs failed: {e} - events may not be delivered!", "WARN")

    # Verify channel access
    log("Verifying channel access...", "INFO")
    for chat_id in ALLOWED_CHAT_IDS:
        try:
            entity = await client.get_entity(chat_id)
            chat_title = getattr(entity, "title", None) or getattr(entity, "username", None) or f"Chat {chat_id}"
            log(f"   Can access: {chat_title} (ID: {chat_id})", "INFO")
        except Exception as e:
            log(f"   Cannot access chat {chat_id}: {e}", "ERROR")
            log("   Make sure the bot account has joined this channel!", "ERROR")

    # Fetch recent messages to catch up
    log("Checking for recent messages (last 5 minutes)...", "INFO")
    try:
        five_min_ago = time.time() - 300
        recent_count = 0

        for chat_id in ALLOWED_CHAT_IDS:
            try:
                messages = await client.get_messages(chat_id, limit=20)
                for msg in reversed(messages):
                    if msg.date and msg.date.timestamp() >= five_min_ago:
                        recent_count += 1
                        text = extract_message_text(msg).strip() if msg else ""
                        log(f"   Recent: [{msg.date.strftime('%H:%M:%S')}] {text[:60]}...", "INFO")
            except Exception as e:
                log(f"   Could not fetch from {chat_id}: {e}", "WARN")

        if recent_count > 0:
            log(f"Found {recent_count} recent message(s) - they will be processed as new messages arrive", "INFO")
        else:
            log("   No recent messages found in monitored channels", "INFO")
    except Exception as e:
        log(f"Error checking recent messages: {e}", "WARN")

    log("=" * 70, "INFO")
    log("BOT READY - LISTENING FOR SIGNALS", "INFO")
    log("=" * 70, "INFO")

    # ══════════════════════════════════════════════════════════════════════
    # Message Polling Fallback
    # ══════════════════════════════════════════════════════════════════════
    async def message_poller():
        """Poll channels for new messages as a fallback to push events."""
        global messages_received, messages_ignored, last_message_time, last_telegram_activity

        log("Message poller started (fallback for push events)", "INFO")
        last_seen_ids: Dict[int, int] = {}

        # Seed last_seen_ids
        for chat_id in ALLOWED_CHAT_IDS:
            try:
                msgs = await client.get_messages(chat_id, limit=1)
                if msgs:
                    last_seen_ids[chat_id] = msgs[0].id
                    log(f"   Poller seed: chat {chat_id} latest msg_id={msgs[0].id}", "DEBUG")
            except Exception as e:
                log(f"   Poller seed failed for {chat_id}: {e}", "WARN")

        while True:
            try:
                await asyncio.sleep(POLLING_INTERVAL)

                for chat_id in ALLOWED_CHAT_IDS:
                    try:
                        msgs = await client.get_messages(chat_id, limit=5)
                        if not msgs:
                            continue

                        min_id = last_seen_ids.get(chat_id, 0)

                        for msg in reversed(msgs):
                            if msg.id <= min_id:
                                continue
                            if msg.id in _processed_msg_ids:
                                continue

                            _processed_msg_ids.add(msg.id)
                            if len(_processed_msg_ids) > _MAX_PROCESSED_IDS:
                                sorted_ids = sorted(_processed_msg_ids)
                                _processed_msg_ids.difference_update(sorted_ids[:len(sorted_ids) // 2])

                            last_seen_ids[chat_id] = max(last_seen_ids.get(chat_id, 0), msg.id)

                            text = extract_message_text(msg).strip()
                            if not text:
                                continue

                            last_telegram_activity = time.time()
                            last_message_time = time.time()
                            messages_received += 1

                            channel_name = "[MAIN]"
                            log("=" * 70, "INFO")
                            log(f"{channel_name} NEW MESSAGE (POLLED)", "INFO")
                            log(f"   Message ID: {msg.id}", "INFO")
                            log(f"   Chat ID: {chat_id}", "INFO")
                            log(f"   Length: {len(text)} chars", "INFO")
                            log(f"   Preview: {text[:150]}", "INFO")
                            log("=" * 70, "INFO")

                            handled = await process_message(text, msg.id, channel_name, source=" (POLLED)")
                            if not handled:
                                log("Polled message not recognized as signal", "DEBUG")
                                messages_ignored += 1

                        if msgs:
                            last_seen_ids[chat_id] = max(last_seen_ids.get(chat_id, 0), msgs[0].id)

                    except Exception as ce:
                        log(f"Poller error for chat {chat_id}: {ce}", "WARN")

            except Exception as e:
                log(f"Message poller exception: {e}", "ERROR")
                import traceback
                log(f"   {traceback.format_exc()}", "ERROR")
                await asyncio.sleep(10)

    asyncio.create_task(message_poller())
    log("  Message poller fallback task created", "INFO")

    # Print initial status
    print_bot_status()

    await replay_pending_signals(client)

    # ══════════════════════════════════════════════════════════════════════
    # Main loop with robust reconnection
    # ══════════════════════════════════════════════════════════════════════
    consecutive_failures = 0

    try:
        while True:
            # ── Pre-flight: make sure we're connected before entering run_until_disconnected ──
            if not client.is_connected():
                log("Client not connected before run_until_disconnected - reconnecting first...", "WARN")
                reconnected = await reconnect_telegram(client, reason="pre_flight_check")
                if reconnected:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    log(f"Pre-flight reconnection failed (consecutive failures: {consecutive_failures})", "ERROR")
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        log(f"CRITICAL: {consecutive_failures} consecutive failures - initiating self-restart", "ERROR")
                        restart_bot()
                    # Wait before retrying the whole loop
                    backoff = min(30 * (2 ** min(consecutive_failures - 1, 4)), 300)
                    log(f"Waiting {backoff}s before next reconnection attempt...", "WARN")
                    await asyncio.sleep(backoff)
                    continue

            try:
                await client.run_until_disconnected()
                # run_until_disconnected() returned normally - connection was lost
                telegram_connected = False
                telegram_connection_losses += 1
                log("Telegram disconnected (run_until_disconnected returned) - will reconnect...", "WARN")

                reconnect_monitor.record_reconnect("Disconnected", "run_until_disconnected returned")

                reconnected = await reconnect_telegram(client, reason="run_until_disconnected_returned")
                if reconnected:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    log(f"Reconnection failed after disconnect (consecutive failures: {consecutive_failures})", "ERROR")
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        log(f"CRITICAL: {consecutive_failures} consecutive failures - initiating self-restart", "ERROR")
                        restart_bot()

                continue

            except KeyboardInterrupt:
                log("Keyboard interrupt received - shutting down", "INFO")
                break
            except TypeNotFoundError as e:
                telegram_connected = False
                telegram_connection_losses += 1

                log("Telethon TypeNotFoundError - unknown TLObject received. Attempting reconnect...", "ERROR")
                log(f"   {str(e)}", "DEBUG")
                import traceback
                log(f"   {traceback.format_exc()}", "DEBUG")

                reconnect_monitor.record_reconnect("TypeNotFoundError", str(e)[:50])

                error_count = reconnect_monitor.error_counters.get("TypeNotFoundError", 0)
                if session_manager.should_rotate("tlnotfound", error_count):
                    log("Rotating session file due to repeated TLObject errors", "INFO")
                    session_manager.rotate_session()
                    client = TelegramClient(session_manager.get_current_session_file(), API_ID, API_HASH)
                    _telegram_client = client
                    # Re-register event handlers on the new client
                    client.on(events.NewMessage())(on_new_message)
                    client.on(events.MessageEdited())(on_edit)

                reconnected = await reconnect_telegram(client, reason="TypeNotFoundError")
                if reconnected:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        log(f"CRITICAL: {consecutive_failures} consecutive failures - initiating self-restart", "ERROR")
                        restart_bot()
                continue

            except ConnectionError as e:
                # This catches "Cannot send requests while disconnected" and similar
                telegram_connected = False
                telegram_connection_losses += 1
                log(f"ConnectionError in main loop: {e} - attempting reconnection...", "ERROR")

                reconnect_monitor.record_reconnect("ConnectionError", str(e)[:50])

                reconnected = await reconnect_telegram(client, reason=f"ConnectionError: {e}")
                if reconnected:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    log(f"Reconnection after ConnectionError failed (consecutive failures: {consecutive_failures})", "ERROR")
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        log(f"CRITICAL: {consecutive_failures} consecutive failures - initiating self-restart", "ERROR")
                        restart_bot()
                continue

            except Exception as e:
                log(f"CRITICAL: Unexpected error in main loop: {str(e)}", "ERROR")
                import traceback
                log(f"   {traceback.format_exc()}", "ERROR")

                # Check if we're actually disconnected (common root cause)
                if not client.is_connected():
                    log("Client is disconnected - this error is likely due to lost connection. Reconnecting...", "WARN")
                    telegram_connected = False
                    reconnected = await reconnect_telegram(client, reason=f"exception_while_disconnected: {e}")
                    if reconnected:
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            log(f"CRITICAL: {consecutive_failures} consecutive failures - initiating self-restart", "ERROR")
                            restart_bot()
                else:
                    # Truly unexpected error, but connection is fine
                    log("Bot will attempt to continue (connection still active)...", "WARN")
                    consecutive_failures = 0
                    await asyncio.sleep(10)
                continue
    finally:
        log("Bot shutting down...", "INFO")
        mt5.shutdown()
        log("MT5 shutdown complete", "INFO")


if __name__ == "__main__":
    asyncio.run(main())
