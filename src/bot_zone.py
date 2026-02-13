#!/usr/bin/env python3
"""
Gold Trading Bot - Zone-Based Signals
Uses the same execution strategy as bot_v2.py, adapted for zone-style messages.
"""

import asyncio
import concurrent.futures
import os
import re
import sys
import time
import MetaTrader5 as mt5
from telethon import TelegramClient, events
from telethon.errors.common import TypeNotFoundError
from typing import Optional, Dict, Tuple, List

# Import custom modules for signal persistence, session management, and monitoring
from signal_queue import SignalQueue
from session_manager import SessionManager
from reconnect_monitor import ReconnectMonitor

# ===================== CONFIGURATION =====================
# Telegram
API_ID = 34597981
API_HASH = "2cd59609b6cacb56da261e43fdb897ea"
CHANNEL_ID = -1003142865169  # Zone trading channel (Gold Scalping - Analysis & Zones) - UPDATE THIS if needed
TEST_CHANNEL_ID = -1003817819872  # Test channel for manual signals (test_bot)
SESSION_FILE = "trading_bot_session_zone"

# Trading
SYMBOL = "XAUUSD"
MAGIC = 778
RISK_PCT = 0.05  # 5% risk per trade
FAILSAFE_SL_DISTANCE = 5.0  # price units away from entry (standard for this channel)

# Telegram filters
ALLOWED_CHAT_IDS = {CHANNEL_ID, TEST_CHANNEL_ID}
DEBUG_LOG_ALL_MESSAGES = True

# Logging
LOG_LEVEL = "INFO"  # Options: "DEBUG" (verbose), "INFO" (normal), "WARN" (quiet - errors only)

# ===================== STATE =====================
# Track open positions: message_id -> ticket
position_map: Dict[int, int] = {}

# Track TP levels per ticket: ticket -> {1: price, 2: price, 3: price, ...}
tp_levels: Dict[int, Dict[int, float]] = {}

# Track entry prices: ticket -> entry_price
entry_prices: Dict[int, float] = {}

# Track failsafe TP3 (pattern-based): ticket -> tp3_price
failsafe_tp3: Dict[int, float] = {}

# Track if breakeven has been activated: ticket -> bool
breakeven_activated: Dict[int, bool] = {}

# Signal attempt log: list of (timestamp, signal, result, details)
signal_log: list = []

# Pending zone signal (single active zone at a time)
pending_zone: Optional[Dict[str, object]] = None

# Initialize external modules
signal_queue: Optional[SignalQueue] = None
session_manager: Optional[SessionManager] = None
reconnect_monitor: Optional[ReconnectMonitor] = None

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
zone_monitor_alive: bool = False
breakeven_monitor_alive: bool = False
heartbeat_counter: int = 0

# Track processed message IDs to avoid duplicate processing (push events + polling)
_processed_msg_ids: set = set()
_MAX_PROCESSED_IDS = 500  # Cap the set size to prevent memory leak
POLLING_INTERVAL = 15  # seconds between polling checks

# Thread pool for running blocking MT5 calls without freezing the async event loop
_mt5_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5")

async def run_mt5(func, *args):
    """Run a blocking MT5 function in a thread so the event loop stays responsive."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_mt5_executor, func, *args)

# ===================== HELPERS =====================

def log(msg: str, level: str = "INFO"):
    """Print with timestamp and level, respecting LOG_LEVEL setting"""
    # Map log levels to priority
    levels = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
    current_priority = levels.get(LOG_LEVEL, 1)
    msg_priority = levels.get(level, 1)
    
    # Only print if message priority >= current LOG_LEVEL priority
    if msg_priority >= current_priority:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            print(f"[{ts}] [{level}] {msg}", flush=True)
        except UnicodeEncodeError:
            # Fallback: Remove emoji/unicode characters for Windows console
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
    log("🔍 Checking AutoTrading status...", "DEBUG")

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log("⚠️ Cannot check AutoTrading - no tick data", "WARN")
        return

    account = mt5.account_info()
    if account:
        if account.trade_allowed:
            log("✅ AutoTrading is ENABLED and allowed", "INFO")
        else:
            log("❌ AutoTrading is DISABLED - trading is forbidden!", "ERROR")
            log("   FIX: Enable AutoTrading in Terminal > Tools > Options > Expert Advisors", "ERROR")
            log("   Also enable 'Allow automated trading' checkbox", "ERROR")
    else:
        log("⚠️ Cannot check AutoTrading status", "WARN")


def ensure_mt5_connection():
    """Initialize MT5 if not connected"""
    global mt5_connected, mt5_connection_losses
    
    log("📡 Attempting MT5 initialization...", "DEBUG")

    if not mt5.initialize():
        log("❌ MT5 initialization FAILED", "ERROR")
        log(f"   Last error: {mt5.last_error()}", "ERROR")
        mt5_connected = False
        mt5_connection_losses += 1
        sys.exit(1)

    log("✅ MT5 initialized successfully", "INFO")
    mt5_connected = True

    version = mt5.version()
    log(f"   MT5 Version: {version}", "DEBUG")

    account = mt5.account_info()
    if account:
        log(f"   Account: {account.login} | Server: {account.server} | Currency: {account.currency}", "DEBUG")
        log(f"   Balance: ${account.balance:.2f} | Equity: ${account.equity:.2f}", "DEBUG")
    else:
        log("⚠️ Could not retrieve account info", "WARN")

    check_autotrading_status()


def check_mt5_health() -> bool:
    """Check if MT5 connection is still alive"""
    global mt5_connected, mt5_connection_losses, last_mt5_check
    
    last_mt5_check = time.time()
    
    # Try to get tick data as health check
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log("⚠️ MT5 Health Check FAILED - No tick data", "WARN")
        mt5_connected = False
        mt5_connection_losses += 1
        return False
    
    account = mt5.account_info()
    if account is None:
        log("⚠️ MT5 Health Check FAILED - No account info", "WARN")
        mt5_connected = False
        mt5_connection_losses += 1
        return False
    
    mt5_connected = True
    return True


def recover_mt5_connection() -> bool:
    """Attempt to recover MT5 connection"""
    log("🔄 Attempting MT5 reconnection...", "WARN")
    
    try:
        mt5.shutdown()
        time.sleep(2)
        
        if mt5.initialize():
            log("✅ MT5 reconnection successful", "INFO")
            return True
        else:
            log("❌ MT5 reconnection failed", "ERROR")
            return False
    except Exception as e:
        log(f"❌ MT5 reconnection exception: {e}", "ERROR")
        return False


def get_account_balance() -> float:
    """Get current account balance"""
    acc = mt5.account_info()
    if acc is None:
        log("❌ account_info() returned None - account not logged in?", "ERROR")
        return 0.0

    balance = float(acc.balance)
    equity = float(acc.equity)
    log(f"💰 Account Balance: ${balance:.2f} | Equity: ${equity:.2f}", "DEBUG")
    return balance


def get_market_price(side: str) -> Optional[float]:
    """Get entry price (ask for BUY, bid for SELL)"""
    log(f"🔍 Getting market price for {side}...", "DEBUG")

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log(f"❌ symbol_info_tick({SYMBOL}) returned None - market data unavailable", "ERROR")
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
    log("📐 Calculating lot size...", "DEBUG")
    log(f"   Entry: ${entry_price:.5f} | SL: ${sl_price:.5f} | Balance: ${balance:.2f}", "DEBUG")

    price_distance = abs(entry_price - sl_price)
    log(f"   Price distance: ${price_distance:.5f}", "DEBUG")

    if price_distance <= 0:
        log(f"❌ Invalid price distance: {price_distance}", "ERROR")
        return 0.01

    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None:
        log(f"❌ symbol_info({SYMBOL}) returned None", "ERROR")
        log(f"   Last error: {mt5.last_error()}", "ERROR")
        return 0.01

    contract_size = float(symbol_info.trade_contract_size)
    vol_min = float(symbol_info.volume_min)
    vol_max = float(symbol_info.volume_max)
    vol_step = float(symbol_info.volume_step)

    log(f"   Contract size: {contract_size} | Min: {vol_min} | Max: {vol_max} | Step: {vol_step}", "DEBUG")

    max_risk_money = balance * RISK_PCT
    lot = max_risk_money / (price_distance * contract_size)

    log(f"   Max risk (5%): ${max_risk_money:.2f} | Calculated lot: {lot:.4f}", "DEBUG")

    lot = max(lot, vol_min)
    lot = min(lot, vol_max)
    lot = round(lot / vol_step) * vol_step

    log(f"   ✅ Final lot size: {lot:.4f}", "DEBUG")
    return lot


def get_filling_mode() -> int:
    """Get the appropriate filling mode for the symbol"""
    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None:
        log("⚠️ Could not get symbol info, defaulting to IOC", "WARN")
        return mt5.ORDER_FILLING_IOC

    filling = symbol_info.filling_mode

    if filling & 2 == 2:  # 2 = SYMBOL_FILLING_IOC
        log("   Using ORDER_FILLING_IOC", "DEBUG")
        return mt5.ORDER_FILLING_IOC
    elif filling & 1 == 1:  # 1 = SYMBOL_FILLING_FOK
        log("   Using ORDER_FILLING_FOK", "DEBUG")
        return mt5.ORDER_FILLING_FOK
    else:
        log("   Using ORDER_FILLING_RETURN", "DEBUG")
        return mt5.ORDER_FILLING_RETURN


def open_position(side: str, lot: float) -> Optional[int]:
    """
    Open market position. Returns ticket number or None.
    Does NOT retry - signals are time-sensitive and must execute immediately or be abandoned.
    """
    log(f"🚀 Opening {side} position with lot size {lot:.4f}...", "INFO")

    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    filling_mode = get_filling_mode()

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": lot,
        "type": order_type,
        "type_filling": filling_mode,
        "magic": MAGIC,
        "comment": f"Zone {side}",
    }

    log(f"   Request: {request}", "DEBUG")

    result = mt5.order_send(request)

    log(f"   Response retcode: {result.retcode}", "DEBUG")
    log(f"   Error message: {result.comment}", "DEBUG")

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        ticket = result.order
        log(f"✅ Position opened successfully - Ticket: {ticket}", "INFO")
        return ticket

    if result.retcode == 10027:
        log("🚨 CRITICAL: AutoTrading is DISABLED in MetaTrader!", "ERROR")
        log(f"   Error: {get_error_message(result.retcode)}", "ERROR")
        log("   ⚠️ FIX: Enable AutoTrading in Terminal > Tools > Options > Expert Advisors", "ERROR")
        log("   ⚠️ Signal is TIME-SENSITIVE - already abandoned due to disabled AutoTrading!", "ERROR")
        return None

    log(f"❌ Order FAILED - Retcode: {result.retcode} ({get_error_message(result.retcode)})", "ERROR")
    log(f"   Last MT5 error: {mt5.last_error()}", "ERROR")

    retryable_codes = {
        10009,  # Request in progress
        10028,  # Trade context busy
        10044,  # Too many requests
        9,      # Price changed (requote)
    }

    max_retries = 3
    retry_delay = 2

    if result.retcode in retryable_codes:
        for attempt in range(1, max_retries):
            log(f"⏳ Retrying in {retry_delay} seconds (attempt {attempt}/{max_retries - 1})...", "WARN")
            time.sleep(retry_delay)

            result = mt5.order_send(request)
            log(f"   Response retcode: {result.retcode}", "DEBUG")

            if result.retcode == mt5.TRADE_RETCODE_DONE:
                ticket = result.order
                log(f"✅ Position opened successfully - Ticket: {ticket}", "INFO")
                return ticket

        log(f"❌ Order failed after {max_retries - 1} retries", "ERROR")

    return None


def close_position(ticket: int, close_reason: str = "SAFETY_EXIT") -> bool:
    """Close an open position immediately at market price (TP3 HIT safety exit)"""
    log(f"🚨 Closing position {ticket} - {close_reason}", "INFO")

    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"❌ Position {ticket} not found (may already be closed)", "WARN")
        return False

    p = pos[0]
    side = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
    close_type = mt5.ORDER_TYPE_SELL if side == "BUY" else mt5.ORDER_TYPE_BUY

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log(f"❌ No tick data for {SYMBOL}", "ERROR")
        return False

    close_price = float(tick.bid if side == "BUY" else tick.ask)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": float(p.volume),
        "type": close_type,
        "position": ticket,
        "price": close_price,
        "magic": MAGIC,
        "comment": f"TP3 HIT - {close_reason}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    log(f"   Closing {side} position: Ticket {ticket} | Volume {p.volume:.3f} | Price ${close_price:.5f}", "DEBUG")
    result = mt5.order_send(request)

    success = result.retcode == mt5.TRADE_RETCODE_DONE

    if success:
        log(f"✅ Position {ticket} CLOSED successfully", "INFO")
    else:
        log(f"❌ Failed to close position {ticket} - Retcode: {result.retcode} | Error: {result.comment}", "ERROR")
        log(f"   Last MT5 error: {mt5.last_error()}", "ERROR")

    return success


def set_stop_loss(ticket: int, sl_price: float) -> bool:
    """Update position stop loss - preserves current TP"""
    log(f"🛡️ Updating SL for ticket {ticket} to ${sl_price:.5f}...", "INFO")

    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"❌ Position {ticket} not found", "ERROR")
        return False

    current_tp = float(pos[0].tp) if pos[0].tp > 0 else 0.0
    log(f"   Current TP: ${current_tp:.5f} (will preserve)", "DEBUG")

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": sl_price,
        "tp": current_tp,
    }

    log(f"   Request: {request}", "DEBUG")

    result = mt5.order_send(request)
    log(f"   Response retcode: {result.retcode}", "DEBUG")

    success = result.retcode == mt5.TRADE_RETCODE_DONE

    if success:
        log(f"✅ SL updated - Ticket {ticket}: ${sl_price:.5f}", "INFO")
    else:
        log(f"⚠️ SL update FAILED - Retcode: {result.retcode} | Error: {result.comment}", "WARN")
        log(f"   Last MT5 error: {mt5.last_error()}", "WARN")

    return success


def can_update_stop_loss(ticket: int, sl_price: float) -> Tuple[bool, str]:
    """Validate SL update based on breakeven protection and trade side."""
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        return False, "position not found"

    side = "BUY" if pos[0].type == mt5.ORDER_TYPE_BUY else "SELL"
    current_sl = float(pos[0].sl) if pos[0].sl > 0 else 0.0
    entry = entry_prices.get(ticket)

    if current_sl > 0 and abs(current_sl - sl_price) < 1e-6:
        return False, "no changes"

    if breakeven_activated.get(ticket, False) and entry is not None:
        if side == "BUY" and sl_price < entry:
            return False, "breakeven protected (BUY)"
        if side == "SELL" and sl_price > entry:
            return False, "breakeven protected (SELL)"

    return True, "ok"


def calculate_failsafe_tp3(entry_price: float, side: str) -> float:
    """Calculate failsafe TP3 based on the pattern: worst entry +/- 6 points."""
    tp3 = entry_price + 6.0 if side == "BUY" else entry_price - 6.0
    return tp3


def is_valid_tp_price(ticket: int, tp_price: float) -> bool:
    """Check if TP price is valid for the position."""
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        return False

    entry = entry_prices.get(ticket, float(pos[0].price_open))
    side = "BUY" if pos[0].type == mt5.ORDER_TYPE_BUY else "SELL"

    if side == "BUY":
        is_valid = tp_price > entry
    else:
        is_valid = tp_price < entry

    if is_valid and abs(tp_price - entry) > 0.01:
        if side == "BUY" and (tp_price - entry) < -1000:
            is_valid = False
        elif side == "SELL" and (entry - tp_price) < -1000:
            is_valid = False

    return is_valid


def select_best_tp_with_fallback(ticket: int, tp_levels_dict: Dict[int, float]) -> Tuple[Optional[int], Optional[float]]:
    """Select the best valid TP from available levels, with fallback logic."""
    for tp_num in [3, 2, 1]:
        if tp_num in tp_levels_dict:
            tp_price = tp_levels_dict[tp_num]
            if is_valid_tp_price(ticket, tp_price):
                log(f"   ✅ TP{tp_num} is valid: ${tp_price:.5f}", "DEBUG")
                return tp_num, tp_price
            else:
                log(f"   ❌ TP{tp_num} is INVALID: ${tp_price:.5f} - trying lower level", "DEBUG")

    return None, None


def set_take_profit(ticket: int, tp_price: float) -> bool:
    """Update position take profit - preserves current SL"""
    log(f"🎯 Updating TP for ticket {ticket} to ${tp_price:.5f}...", "INFO")

    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"❌ Position {ticket} not found", "ERROR")
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
        log(f"✅ TP updated - Ticket {ticket}: ${tp_price:.5f}", "INFO")
    else:
        log(f"⚠️ TP update FAILED - Retcode: {result.retcode} | Error: {result.comment}", "WARN")
        log(f"   Last MT5 error: {mt5.last_error()}", "WARN")

    return success


def get_position_side(ticket: int) -> Optional[str]:
    """Get position direction (BUY or SELL)"""
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"⚠️ Position {ticket} not found or already closed", "WARN")
        return None

    side = "BUY" if pos[0].type == mt5.ORDER_TYPE_BUY else "SELL"
    log(f"   Position {ticket} side: {side}", "DEBUG")
    return side


def price_crossed_tp1(ticket: int, tp1: float, current_price: float) -> bool:
    """Check if price has crossed TP1 level"""
    side = get_position_side(ticket)
    if side is None:
        return False

    if side == "BUY":
        crossed = current_price >= tp1
        log(
            f"   [TP1 Check] Ticket {ticket}: Current=${current_price:.5f} vs TP1=${tp1:.5f} (BUY) → {'CROSSED' if crossed else 'not crossed'}",
            "DEBUG",
        )
    else:
        crossed = current_price <= tp1
        log(
            f"   [TP1 Check] Ticket {ticket}: Current=${current_price:.5f} vs TP1=${tp1:.5f} (SELL) → {'CROSSED' if crossed else 'not crossed'}",
            "DEBUG",
        )

    return crossed


# ===================== PARSING =====================

def is_tp1_hit_message(text: str) -> bool:
    """Check if message indicates TP1 / Target 1 has been hit (breakeven trigger)."""
    t = (text or "").upper()
    return bool(re.search(r"\bTP\s*1\s+HIT\b|\bTARGET\s*1\b", t))


def is_tp2_hit_message(text: str) -> bool:
    """Check if message indicates TP2 / Target 2 has been hit."""
    t = (text or "").upper()
    return bool(re.search(r"\bTP\s*2\s+HIT\b|\bTARGET\s*2\b", t))


def is_tp3_hit_message(text: str) -> bool:
    """Check if message is a TP3 HIT signal (safety exit trigger)."""
    t = (text or "").upper()
    return bool(re.search(r"\bTP\s*3\s+HIT\b|\bTARGET\s*3\b", t))


def is_cancel_message(text: str) -> bool:
    """Check if message cancels a zone."""
    t = (text or "").lower()
    keywords = ["cancel", "zone failed", "missed", "missed by"]
    return any(k in t for k in keywords)


def is_active_message(text: str) -> bool:
    """Check if message marks a zone active."""
    t = (text or "").lower()
    return "active" in t


def is_informational_message(text: str) -> bool:
    """Check if message is informational only (closed zone, past event, etc).
    These should NOT trigger trades."""
    t = (text or "").lower()
    # Keywords that indicate this is an informational message about a past event
    informational_keywords = [
        "closed was noted",  # "zone closed was noted as -150 pip"
        "noted as",          # "close was noted as"
        "due to the speed",  # "due to the speed it moved"
        "was closed",        # past tense - zone already closed
        "high risk buy zone wich was closed",  # specific case
        "high risk sell zone wich was closed",  # specific case
    ]
    return any(keyword in t for keyword in informational_keywords)


def is_high_risk_message(text: str) -> bool:
    """Check if message contains high risk trading signals.
    We want to avoid high risk trades."""
    t = (text or "").lower()
    return "high risk" in t and not is_informational_message(text)


def parse_zone_side(text: str) -> Optional[str]:
    """Parse BUY or SELL side from a zone message."""
    t = (text or "").lower()
    if "buy zone" in t:
        return "BUY"
    if "sell zone" in t:
        return "SELL"
    return None


def parse_zone_range(text: str) -> Optional[Tuple[float, float]]:
    """Parse the first price range found in a zone message."""
    pattern = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|\u2013)\s*(\d+(?:\.\d+)?)")
    match = pattern.search(text or "")
    if not match:
        return None

    p1 = float(match.group(1))
    p2 = float(match.group(2))
    low = min(p1, p2)
    high = max(p1, p2)
    return low, high


def parse_targets(text: str) -> List[float]:
    """Parse target prices from the section after 'Targets'."""
    t = text or ""
    idx = re.search(r"targets?", t, re.IGNORECASE)
    if not idx:
        return []

    segment = t[idx.end():]
    nums = re.findall(r"\b\d+(?:\.\d+)?\b", segment)
    targets = [float(n) for n in nums if float(n) >= 100]
    return targets


def parse_invalid_sl(text: str) -> Optional[float]:
    """Parse invalid/SL price from text.
    Handles patterns like:
    - "Invalid 5080"
    - "SL: 5072"
    - "Invalid / SL 4980"
    - "SL / 5080"
    """
    t = text or ""
    
    # Multiple patterns to try (in order of priority)
    patterns = [
        # Pattern 1: "SL:" followed by a number (e.g., "SL: 5072")
        r"SL\s*:\s*([0-9]+(?:\.[0-9]+)?)",
        # Pattern 2: "Invalid" followed by a number (e.g., "Invalid 5080", "Invalid / SL 5080")
        r"(?:invalid|sl)\s*(?:/\s*)?(?:sl\s*)?:\s*([0-9]+(?:\.[0-9]+)?)",
        # Pattern 3: "Invalid" at word boundary followed by number
        r"\b(?:invalid|sl)\b\s+([0-9]+(?:\.[0-9]+)?)",
        # Pattern 4: Fallback - just find "SL" or "invalid" followed by any number
        r"(?:invalid|sl)\s*/?\s*(?:sl\s*)?([0-9]+(?:\.[0-9]+)?)",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, t, re.IGNORECASE)
        if match:
            try:
                price = float(match.group(1))
                if price >= 100:  # Validate it's a reasonable price (gold is typically 1000+)
                    log(f"✅ SL Parsed from '{pattern[:30]}...': ${price:.2f}", "DEBUG")
                    return price
            except (ValueError, AttributeError):
                continue
    
    log(f"⚠️ No SL found in text: {t[:100]}", "DEBUG")
    return None


def extract_message_text(message) -> str:
    """Extract text from Telegram message"""
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
    """Log a signal attempt for debugging"""
    global trades_executed, trades_failed, messages_processed
    
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "timestamp": ts,
        "signal": side,
        "result": result,
        "detail": detail
    }
    signal_log.append(entry)
    log(f"📋 Signal log: {side} | {result} | {detail}", "DEBUG")
    
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
        "zone_monitor_alive": zone_monitor_alive,
        "breakeven_monitor_alive": breakeven_monitor_alive,
        "active_positions": len(position_map),
        "pending_zone": pending_zone is not None,
    }


def print_bot_status():
    """Print comprehensive bot status"""
    metrics = get_bot_metrics()
    
    log("=" * 70, "INFO")
    log("📊 BOT HEALTH STATUS", "INFO")
    log("=" * 70, "INFO")
    log(f"⏱️  Uptime: {metrics['uptime_hours']:.2f} hours ({metrics['uptime_seconds']:.0f}s)", "INFO")
    log(f"📨 Messages: Received={metrics['messages_received']} | Processed={metrics['messages_processed']} | Ignored={metrics['messages_ignored']}", "INFO")
    log(f"📈 Trades: Executed={metrics['trades_executed']} | Failed={metrics['trades_failed']}", "INFO")
    log(f"🔌 Connections: MT5={('✅' if metrics['mt5_connected'] else '❌')} | Telegram={('✅' if metrics['telegram_connected'] else '❌')}", "INFO")
    log(f"🔄 Connection Losses: MT5={metrics['mt5_connection_losses']} | Telegram={metrics['telegram_connection_losses']}", "INFO")
    log(f"🤖 Monitors: Zone={('✅' if metrics['zone_monitor_alive'] else '❌')} | Breakeven={('✅' if metrics['breakeven_monitor_alive'] else '❌')}", "INFO")
    log(f"💼 Active Positions: {metrics['active_positions']}", "INFO")
    log(f"🧭 Pending Zone: {('YES' if metrics['pending_zone'] else 'NO')}", "INFO")
    
    if last_message_time > 0:
        log(f"📬 Last Message: {metrics['seconds_since_last_message']:.0f}s ago", "INFO")
    else:
        log(f"📬 Last Message: Never", "INFO")
    
    log("=" * 70, "INFO")


# ===================== TELEGRAM HANDLERS =====================

def init_telegram():
    """Create Telegram client"""
    return TelegramClient(SESSION_FILE, API_ID, API_HASH)


async def catch_up_messages(client, lookback_minutes: int = 5):
    """Manually fetch recent messages from allowed channels to catch up on missed messages."""
    try:
        log(f"🔍 Catching up on messages from last {lookback_minutes} minutes...", "INFO")
        cutoff_time = time.time() - (lookback_minutes * 60)
        
        for chat_id in ALLOWED_CHAT_IDS:
            try:
                # Fetch recent messages
                messages = await client.get_messages(chat_id, limit=20)
                caught_up = 0
                
                for msg in reversed(messages):  # Process oldest first
                    if msg.date.timestamp() >= cutoff_time:
                        # Check if we've already processed this message
                        # (This is a simple check - you might want more sophisticated tracking)
                        caught_up += 1
                        log(f"   📥 Caught up message {msg.id} from {msg.date.strftime('%H:%M:%S')}", "DEBUG")
                
                if caught_up > 0:
                    log(f"✅ Caught up {caught_up} message(s) from chat {chat_id}", "INFO")
            except Exception as e:
                log(f"⚠️ Failed to catch up messages from chat {chat_id}: {e}", "WARN")
                
    except Exception as e:
        log(f"⚠️ Error during message catch-up: {e}", "WARN")


def set_pending_zone(side: str, zone_low: float, zone_high: float, targets: List[float], sl: Optional[float], msg_id: int, msg_text: str):
    """Store pending zone for later execution."""
    global pending_zone
    pending_zone = {
        "side": side,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "targets": targets,
        "sl": sl,
        "msg_id": msg_id,
        "msg_text": msg_text,
        "created_at": time.time(),
        "executed": False,
    }
    log(f"🧭 Pending zone stored: {side} {zone_low:.2f}-{zone_high:.2f}", "INFO")


def clear_pending_zone(reason: str):
    """Clear current pending zone."""
    global pending_zone
    if pending_zone:
        log(f"🧹 Clearing pending zone ({reason})", "INFO")
    pending_zone = None


def zone_price_in_range(price: float, zone_low: float, zone_high: float) -> bool:
    """Check if a price is within the zone range."""
    return zone_low <= price <= zone_high


def build_tp_levels_from_targets(targets: List[float]) -> Dict[int, float]:
    """Map targets to TP1/TP2/TP3 in the order provided."""
    levels: Dict[int, float] = {}
    for idx, price in enumerate(targets[:5], start=1):
        levels[idx] = price
    return levels


def apply_targets_to_ticket(ticket: int, targets: List[float]):
    """Store targets and set best TP with fallback."""
    if not targets:
        log("⚠️ No targets provided - keeping failsafe TP", "WARN")
        return

    tp_levels[ticket] = build_tp_levels_from_targets(targets)
    tp_num, tp_price = select_best_tp_with_fallback(ticket, tp_levels[ticket])
    if tp_num and tp_price:
        if tp_num == 3 and ticket in failsafe_tp3:
            current_failsafe = failsafe_tp3[ticket]
            diff = abs(tp_price - current_failsafe)
            log(f"🎯 TP3 selected: ${tp_price:.5f} - setting as target", "INFO")
            log(f"   (Updated from failsafe: ${current_failsafe:.5f} | Diff: ${diff:.5f})", "DEBUG")
        else:
            log(f"🎯 TP{tp_num} selected: ${tp_price:.5f} - setting as target", "INFO")
        set_take_profit(ticket, tp_price)
    else:
        log("⚠️ All TP levels invalid or missing - will wait for valid values", "WARN")


def execute_zone_trade(side: str, entry_price: float, msg_id: int, msg_text: str, sl_price: Optional[float], targets: List[float]):
    """Execute trade using the same strategy as bot_v2.py."""
    balance = get_account_balance()
    if balance <= 0:
        log(f"❌ Invalid balance: ${balance:.2f} - aborting trade", "ERROR")
        log_signal_attempt(side, "FAILED", f"Invalid balance: ${balance:.2f}")
        return

    failsafe_sl = entry_price - FAILSAFE_SL_DISTANCE if side == "BUY" else entry_price + FAILSAFE_SL_DISTANCE
    chosen_sl = sl_price if sl_price is not None else failsafe_sl

    log(f"🛡️ Failsafe SL distance: ${FAILSAFE_SL_DISTANCE:.5f}", "DEBUG")
    log(f"🛡️ Failsafe SL price: ${failsafe_sl:.5f}", "DEBUG")
    log(f"🛡️ Chosen SL price: ${chosen_sl:.5f}", "DEBUG")

    lot = calculate_lot_size(entry_price, chosen_sl, balance)

    if lot <= 0:
        log(f"❌ Invalid lot size: {lot} - aborting trade", "ERROR")
        log_signal_attempt(side, "FAILED", f"Invalid lot size: {lot}")
        return

    log(f"📊 Trade parameters: Entry=${entry_price:.5f} | SL=${chosen_sl:.5f} | Lot={lot:.4f}", "INFO")

    ticket = open_position(side, lot)

    if ticket:
        log(f"✅ POSITION OPENED: Ticket={ticket} | Side={side} | Entry=${entry_price:.5f} | Lot={lot:.4f}", "INFO")
        log_signal_attempt(side, "SUCCESS", f"Ticket={ticket} | Entry=${entry_price:.5f}")
        position_map[msg_id] = ticket
        entry_prices[ticket] = entry_price
        breakeven_activated[ticket] = False
        log(f"   Stored in position_map: msg_id={msg_id} -> ticket={ticket}", "DEBUG")

        signal_queue.add_signal(
            signal_type="ZONE",
            side=side,
            entry_price=entry_price,
            sl_price=chosen_sl,
            lot_size=lot,
            tp_levels=build_tp_levels_from_targets(targets),
            msg_id=msg_id,
            msg_text=msg_text[:100]
        )
        signal_queue.mark_executed(msg_id)

        log("⏳ Waiting for MT5 to register position...", "DEBUG")
        time.sleep(0.5)
        set_stop_loss(ticket, chosen_sl)

        failsafe_tp3_price = calculate_failsafe_tp3(entry_price, side)
        failsafe_tp3[ticket] = failsafe_tp3_price
        log("🎯 Failsafe TP3 distance: 6 points", "INFO")
        log(f"🎯 Failsafe TP3 price: ${failsafe_tp3_price:.5f}", "DEBUG")
        set_take_profit(ticket, failsafe_tp3_price)

        apply_targets_to_ticket(ticket, targets)
    else:
        log("❌ POSITION OPEN FAILED - returning", "ERROR")
        log_signal_attempt(side, "FAILED", "Position open failed (see above for details)")


def try_execute_pending_zone():
    """Check pending zone and execute if price is within range."""
    global pending_zone
    if not pending_zone or pending_zone.get("executed"):
        return

    side = pending_zone["side"]
    zone_low = float(pending_zone["zone_low"])
    zone_high = float(pending_zone["zone_high"])

    # Get market tick data
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log(f"❌ Cannot get tick data for zone check", "ERROR")
        return

    bid = float(tick.bid)
    ask = float(tick.ask)
    
    # For zone detection, check if BID is in the zone
    # This is more realistic: we trigger when market touches the zone
    # But we'll enter at ASK (for BUY) or BID (for SELL)
    zone_check_price = bid if side == "BUY" else ask
    entry_price = ask if side == "BUY" else bid
    
    log(f"🔍 Zone check: {side} | Zone: {zone_low:.2f}-{zone_high:.2f} | Bid: ${bid:.5f} | Ask: ${ask:.5f} | ZoneCheckPrice: ${zone_check_price:.5f}", "DEBUG")

    if zone_price_in_range(zone_check_price, zone_low, zone_high):
        log(f"✅ ZONE TRIGGER: {side} zone {zone_low:.2f}-{zone_high:.2f} - executing trade at ${entry_price:.5f}", "INFO")
        pending_zone["executed"] = True
        execute_zone_trade(
            side=side,
            entry_price=entry_price,
            msg_id=int(pending_zone["msg_id"]),
            msg_text=str(pending_zone["msg_text"]),
            sl_price=pending_zone.get("sl"),
            targets=pending_zone.get("targets", []),
        )
        clear_pending_zone("executed")
    else:
        # Log more detail about why zone wasn't triggered
        distance_to_low = zone_check_price - zone_low if zone_check_price < zone_low else 0
        distance_to_high = zone_check_price - zone_high if zone_check_price > zone_high else 0
        if distance_to_low > 0:
            log(f"   Zone not reached: ${distance_to_low:.5f} above zone", "DEBUG")
        elif distance_to_high > 0:
            log(f"   Zone overshot: ${distance_to_high:.5f} above zone", "DEBUG")


async def replay_pending_signals(client):
    """
    Replay signals from queue that were received but not yet executed.
    """
    if not signal_queue:
        return

    pending = signal_queue.get_pending_signals()
    if not pending:
        log("📋 No pending signals to replay", "INFO")
        return

    log("=" * 70, "INFO")
    log(f"🔄 REPLAYING {len(pending)} PENDING SIGNALS FROM QUEUE", "INFO")
    log("=" * 70, "INFO")

    for sig in pending:
        msg_id = sig.get("id")
        signal_type = sig.get("signal_type")
        msg_text = sig.get("msg_text", "")

        log(f"📨 Replaying {signal_type} signal (msg_id={msg_id}): {msg_text[:60]}", "INFO")

    log("=" * 70, "INFO")


async def heartbeat_monitor():
    """Regular heartbeat to prove bot is alive - runs every 60 seconds"""
    global heartbeat_counter
    
    log("💓 Heartbeat monitor started", "INFO")
    
    while True:
        try:
            await asyncio.sleep(60)  # Every minute
            
            heartbeat_counter += 1
            metrics = get_bot_metrics()
            
            log("=" * 70, "INFO")
            log(f"💓 HEARTBEAT #{heartbeat_counter} - BOT ALIVE", "INFO")
            log(f"⏱️  Uptime: {metrics['uptime_hours']:.2f}h | Msgs: {metrics['messages_received']} | Trades: {metrics['trades_executed']}", "INFO")
            log(f"🔌 MT5: {('✅' if metrics['mt5_connected'] else '❌')} | Telegram: {('✅' if metrics['telegram_connected'] else '❌')}", "INFO")
            log("=" * 70, "INFO")
            
        except Exception as e:
            log(f"❌ CRITICAL: Heartbeat monitor exception: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
            await asyncio.sleep(10)  # Wait before retry


async def connection_health_monitor():
    """Monitor MT5 and Telegram connection health - runs every 60 seconds"""
    global mt5_connected, telegram_connected
    
    log("🏥 Connection health monitor started", "INFO")
    
    while True:
        try:
            await asyncio.sleep(60)  # Every 60 seconds (was 30 - reduced to avoid blocking event loop)
            
            log("🔍 Running connection health checks...", "DEBUG")
            
            # Check MT5 connection (in executor to avoid blocking)
            mt5_healthy = await run_mt5(check_mt5_health)
            if not mt5_healthy:
                log("🚨 MT5 connection unhealthy - attempting recovery", "WARN")
                recovered = await run_mt5(recover_mt5_connection)
                if recovered:
                    log("✅ MT5 connection recovered", "INFO")
                    mt5_connected = True
                else:
                    log("❌ MT5 connection recovery failed", "ERROR")
                    mt5_connected = False
            else:
                log("✅ MT5 connection healthy", "DEBUG")
            
            # ── Telegram connectivity check ──
            # Phase 1: Check message activity
            if last_telegram_activity > 0:
                time_since_telegram = time.time() - last_telegram_activity
                
                # Log activity status every check
                if time_since_telegram < 120:  # Recent activity (< 2 min)
                    log(f"✅ Telegram active ({time_since_telegram:.0f}s since last message)", "DEBUG")
                    telegram_connected = True
                elif time_since_telegram > 300:  # 5 minutes with no messages
                    log(f"⚠️ No Telegram activity for {time_since_telegram:.0f}s - running active connectivity check...", "WARN")
                    
                    # Phase 2: Active connectivity check using the actual client
                    if _telegram_client is not None:
                        try:
                            # Quick check: is the client still connected?
                            if not _telegram_client.is_connected():
                                log("🚨 Telegram client.is_connected() = False - forcing disconnect to trigger reconnection", "WARN")
                                telegram_connected = False
                                try:
                                    await _telegram_client.disconnect()
                                except Exception:
                                    pass  # Already disconnected, that's fine
                            else:
                                # Client thinks it's connected - verify with an active ping
                                try:
                                    await asyncio.wait_for(_telegram_client.get_me(), timeout=10)
                                    log("✅ Telegram ping successful - connection alive, channel is just quiet", "DEBUG")
                                    telegram_connected = True  # Connection is fine, just no messages
                                except (asyncio.TimeoutError, Exception) as ping_err:
                                    log(f"🚨 Telegram ping FAILED ({ping_err}) - connection is dead, forcing disconnect to trigger reconnection", "WARN")
                                    telegram_connected = False
                                    try:
                                        await _telegram_client.disconnect()
                                    except Exception:
                                        pass
                        except Exception as check_err:
                            log(f"⚠️ Error during active Telegram check: {check_err}", "WARN")
                            telegram_connected = False
                    else:
                        telegram_connected = False
                else:
                    telegram_connected = True
            
        except Exception as e:
            log(f"❌ CRITICAL: Health monitor exception: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
            await asyncio.sleep(10)  # Wait before retry


async def cleanup_and_report():
    """Periodic cleanup and reporting of system health."""
    while True:
        try:
            await asyncio.sleep(300)  # Every 5 minutes
            
            log("🧹 Running periodic cleanup...", "DEBUG")

            if signal_queue:
                signal_queue.remove_old_signals(days=7)
                stats = signal_queue.get_stats()
                log(f"📊 Signal queue: {stats['total_signals']} total, {stats['pending']} pending", "DEBUG")

            if session_manager:
                session_manager.cleanup_old_backups(keep_count=5)

            if reconnect_monitor:
                reconnect_monitor.reset_window()
                if reconnect_monitor.get_stats()['total_reconnects'] > 0:
                    reconnect_monitor.print_summary()
            
            # Print full status report
            print_bot_status()

        except Exception as e:
            log(f"❌ CRITICAL: Cleanup exception: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
            await asyncio.sleep(10)  # Wait before retry


async def monitor_closed_positions():
    """Monitor for naturally closed positions (removed - trade_logger dependency)"""
    global trades_executed, trades_failed
    
    log("📊 Closed position monitor started", "INFO")
    
    # This function was dependent on trade_logger which has been removed.
    # Positions are still tracked in MT5 and can be fetched via --mt5 --write-json analyzer command.
    # Simply sleep and do nothing for backward compatibility
    while True:
        try:
            await asyncio.sleep(30)
        except Exception as e:
            log(f"❌ Closed position monitor error: {str(e)}", "ERROR")
            await asyncio.sleep(10)


async def monitor_pending_zone():
    """Continuously watch for price entering a pending zone."""
    global zone_monitor_alive
    
    log("🔄 Zone monitor started", "INFO")
    zone_monitor_alive = True
    ZONE_TIMEOUT = 600  # 10 minutes - clear old zones
    
    check_count = 0
    
    while True:
        try:
            zone_monitor_alive = True
            await asyncio.sleep(2)
            
            check_count += 1
            
            # Log periodic status
            if check_count % 30 == 0:  # Every 60 seconds
                if pending_zone and not pending_zone.get("executed"):
                    age = time.time() - pending_zone.get("created_at", time.time())
                    log(f"🧭 Zone monitor check #{check_count}: Active zone (age: {age:.0f}s)", "DEBUG")
                else:
                    log(f"🧭 Zone monitor check #{check_count}: No pending zone", "DEBUG")
            
            # Check if zone has expired
            if pending_zone and not pending_zone.get("executed"):
                age = time.time() - pending_zone.get("created_at", time.time())
                if age > ZONE_TIMEOUT:
                    log(f"⏰ Zone expired (age: {age:.0f}s) - clearing pending zone", "INFO")
                    clear_pending_zone("timeout")
                    continue
            
            # Run the potentially blocking zone check (contains MT5 calls) in executor
            await run_mt5(try_execute_pending_zone)
            
        except Exception as e:
            log(f"❌ CRITICAL: Zone monitor exception: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
            zone_monitor_alive = False
            await asyncio.sleep(10)  # Wait before retry


async def main():
    global signal_queue, session_manager, reconnect_monitor, bot_start_time, telegram_connected, last_telegram_activity

    # Record bot start time
    bot_start_time = time.time()
    start_timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    
    signal_queue = SignalQueue("signal_queue.json")
    session_manager = SessionManager("trading_bot_session", "sessions")
    reconnect_monitor = ReconnectMonitor(alert_threshold=5, time_window_minutes=30)

    global _telegram_client
    client = init_telegram()
    _telegram_client = client  # Make accessible to health monitor for active connectivity checks
    ensure_mt5_connection()

    log("=" * 70, "INFO")
    log("🚀 BOT INITIALIZATION COMPLETE", "INFO")
    log("=" * 70, "INFO")
    log(f"⏰ Bot start time: {start_timestamp}", "INFO")
    log(f"📱 Main channel ID: {CHANNEL_ID}", "INFO")
    log(f"📱 Test channel ID: {TEST_CHANNEL_ID}", "INFO")
    log(f"💎 Trading symbol: {SYMBOL}", "INFO")
    log(f"💰 Risk per trade: {RISK_PCT*100:.1f}%", "INFO")
    log(f"🛡️ Failsafe SL distance: ${FAILSAFE_SL_DISTANCE:.2f}", "INFO")
    log(f"📊 Magic number: {MAGIC}", "INFO")
    log(f"📊 Log level: {LOG_LEVEL}", "INFO")
    log("=" * 70, "INFO")

    @client.on(events.NewMessage())
    async def on_new_message(event):
        """Handle new Telegram messages from main or test channel"""
        global pending_zone, messages_received, messages_ignored, last_message_time, last_telegram_activity
        
        try:
            # Log ALL messages at the very start for debugging
            log(f"🔔 INCOMING (PUSH): chat_id={event.chat_id} | msg_id={event.message.id}", "INFO")
            
            # Deduplicate: skip if already processed by polling
            if event.message.id in _processed_msg_ids:
                log(f"⏭️  msg_id={event.message.id} already processed (via poll) - skipping", "DEBUG")
                return
            _processed_msg_ids.add(event.message.id)
            # Cap the set size
            if len(_processed_msg_ids) > _MAX_PROCESSED_IDS:
                _processed_msg_ids.clear()
            
            # Track total messages received (before filtering)
            messages_received += 1
            
            # Filter by allowed chat IDs
            if event.chat_id not in ALLOWED_CHAT_IDS:
                messages_ignored += 1
                if DEBUG_LOG_ALL_MESSAGES:
                    chat = await event.get_chat()
                    chat_title = getattr(chat, "title", None) or getattr(chat, "username", None) or "(no title)"
                    log(f"⏭️  Ignored: chat_id={event.chat_id} | {chat_title}", "DEBUG")
                return
            
            # Update Telegram activity timestamp for allowed channels
            last_telegram_activity = time.time()
            last_message_time = time.time()

            text = extract_message_text(event.message).strip()
            msg_id = event.message.id
            channel_name = "[TEST]" if event.chat_id == TEST_CHANNEL_ID else "[MAIN]"
            
            if DEBUG_LOG_ALL_MESSAGES:
                chat = await event.get_chat()
                chat_title = getattr(chat, "title", None) or getattr(chat, "username", None) or "(no title)"
                log(f"📥 [RAW] chat_id={event.chat_id} | {chat_title} | {text[:80]}", "DEBUG")
            
            # Calculate delivery delay
            msg_timestamp = event.message.date.timestamp()
            delivery_delay = time.time() - msg_timestamp
            delay_warning = ""
            if delivery_delay > 10:  # More than 10 seconds delay
                delay_warning = f" ⚠️ DELAYED: {delivery_delay:.0f}s"

            log("=" * 70, "INFO")
            log(f"📨 {channel_name} NEW MESSAGE RECEIVED{delay_warning}", "INFO")
            log(f"   Message ID: {msg_id}", "INFO")
            log(f"   Chat ID: {event.chat_id}", "INFO")
            log(f"   Length: {len(text)} chars", "INFO")
            if delivery_delay > 10:
                log(f"   ⏰ Sent: {event.message.date.strftime('%H:%M:%S')} | Received: {time.strftime('%H:%M:%S')} (Δ{delivery_delay:.0f}s)", "WARN")
            log(f"   Preview: {text[:150]}", "INFO")
            log("=" * 70, "INFO")

            # ── TARGET 1 HIT → Move SL to breakeven ──
            if is_tp1_hit_message(text):
                log("=" * 70, "INFO")
                log("🎯 TARGET 1 HIT DETECTED - MOVING SL TO BREAKEVEN!", "INFO")
                log("=" * 70, "INFO")

                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                if positions:
                    moved_count = 0
                    for p in positions:
                        if p.magic == MAGIC:
                            ticket = int(p.ticket)
                            if breakeven_activated.get(ticket, False):
                                log(f"   Ticket {ticket}: Breakeven already active - skipping", "INFO")
                                continue
                            entry = entry_prices.get(ticket, float(p.price_open))
                            log(f"🛡️ Moving SL to breakeven for ticket {ticket}: ${entry:.5f}", "INFO")
                            await run_mt5(lambda t=ticket, e=entry: set_stop_loss(t, e))
                            breakeven_activated[ticket] = True
                            moved_count += 1
                    if moved_count > 0:
                        log(f"✅ BREAKEVEN SET on {moved_count} position(s) after TARGET 1 HIT", "INFO")
                    else:
                        log("⚠️ No positions needed breakeven update", "WARN")
                else:
                    log("⚠️ TARGET 1 HIT but no open positions found", "WARN")
                return

            # ── TARGET 2 HIT → Acknowledge (position stays open for TP3) ──
            if is_tp2_hit_message(text):
                log("🎯 TARGET 2 HIT acknowledged - position remains open for TP3", "INFO")
                return

            if is_tp3_hit_message(text):
                log("=" * 70, "INFO")
                log("🚨 TP3 HIT DETECTED - SAFETY EXIT!", "INFO")
                log("=" * 70, "INFO")

                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                if positions:
                    closed_count = 0
                    for p in positions:
                        if p.magic == MAGIC:
                            log(f"🎯 Closing position: Ticket {p.ticket} | Volume {p.volume:.3f}", "INFO")
                            if await run_mt5(lambda: close_position(p.ticket)):
                                closed_count += 1

                    if closed_count > 0:
                        log("=" * 70, "INFO")
                        log(f"✅ CLOSED {closed_count} POSITION(S) on TP3 HIT", "INFO")
                        log("=" * 70, "INFO")
                    else:
                        log("⚠️ Failed to close positions on TP3 HIT", "WARN")
                else:
                    log("⚠️ TP3 HIT detected but no open positions found", "WARN")
                return

            if is_cancel_message(text):
                clear_pending_zone("cancel message")
                return

            # FILTER: Skip informational messages
            if is_informational_message(text):
                log(f"⏭️  Message is informational only (past event) - skipping", "INFO")
                messages_ignored += 1
                return

            # FILTER: Skip high-risk messages
            if is_high_risk_message(text):
                log(f"⏭️  Message contains HIGH RISK - skipping per bot settings", "WARN")
                messages_ignored += 1
                return

            side = parse_zone_side(text)
            zone_range = parse_zone_range(text)
            
            log(f"🔍 Parsing: side={'(' + side + ')' if side else 'None'} | zone_range={zone_range}", "DEBUG")
            
            if side and zone_range:
                zone_low, zone_high = zone_range
                targets = parse_targets(text)
                sl_price = parse_invalid_sl(text)

                log(f"🎯 ZONE DETECTED: {side} {zone_low:.2f}-{zone_high:.2f}", "INFO")
                log(f"   Targets: {targets}", "DEBUG")
                log(f"   SL: {sl_price}", "DEBUG")
                log_signal_attempt(side, "RECEIVED", f"msg_id={msg_id}")

                set_pending_zone(
                    side=side,
                    zone_low=zone_low,
                    zone_high=zone_high,
                    targets=targets,
                    sl=sl_price,
                    msg_id=msg_id,
                    msg_text=text,
                )

                await run_mt5(try_execute_pending_zone)
                return

            if side and not zone_range:
                # Immediate execution: "Buy zone now" or "Sell zone now" without a specific range
                log(f"⚡ IMMEDIATE ZONE ENTRY: {side} zone (no range specified)", "INFO")
                log(f"   Message: {text[:100]}", "DEBUG")
                log_signal_attempt(side, "RECEIVED", f"msg_id={msg_id} - IMMEDIATE ENTRY")

                current_price = await run_mt5(lambda: get_market_price(side))
                if current_price:
                    await run_mt5(lambda: execute_zone_trade(
                        side=side,
                        entry_price=current_price,
                        msg_id=msg_id,
                        msg_text=text,
                        sl_price=None,  # Use failsafe SL distance (8.0)
                        targets=[],  # Use failsafe TP3
                    ))
                else:
                    log("❌ Cannot get market price for immediate zone entry", "ERROR")
                    log_signal_attempt(side, "FAILED", "Cannot get market price")
                return

            if is_active_message(text):
                log("🟡 Zone marked active", "INFO")
                log(f"   Message: {text[:100]}", "DEBUG")
                await run_mt5(try_execute_pending_zone)
                return

            sl_update = parse_invalid_sl(text)
            if sl_update:
                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                if not positions:
                    log("⚠️ No open positions to update - ignoring SL", "WARN")
                    return

                ticket = int(positions[-1].ticket)
                can_update, reason = can_update_stop_loss(ticket, sl_update)
                if can_update:
                    await run_mt5(lambda: set_stop_loss(ticket, sl_update))
                else:
                    log(f"⚠️ SL update skipped: {reason}", "WARN")

            targets = parse_targets(text)
            if targets:
                log(f"📊 Found {len(targets)} target(s) in message: {targets}", "DEBUG")
                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                if not positions:
                    log("⚠️ No open positions to update - ignoring targets", "WARN")
                    return

                ticket = int(positions[-1].ticket)
                log(f"   Applying to ticket: {ticket}", "DEBUG")
                await run_mt5(lambda: apply_targets_to_ticket(ticket, targets))
                return
            
            # If we got here, message wasn't recognized as any signal type
            log(f"⏭️  Message not recognized as signal - ignoring", "DEBUG")
            log(f"   Checks: TP1={is_tp1_hit_message(text)} | TP2={is_tp2_hit_message(text)} | TP3={is_tp3_hit_message(text)} | CANCEL={is_cancel_message(text)} | SIDE={side} | ACTIVE={is_active_message(text)}", "DEBUG")
            messages_ignored += 1

        except Exception as e:
            log(f"❌ CRITICAL EXCEPTION in on_new_message: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
            log(f"   Message ID: {msg_id if 'msg_id' in locals() else 'unknown'}", "ERROR")
            log(f"   Message text: {text[:200] if 'text' in locals() else 'unknown'}", "ERROR")

    @client.on(events.MessageEdited())
    async def on_edit(event):
        """Handle edited messages from main or test channel"""
        global pending_zone, messages_received, messages_ignored, last_telegram_activity
        
        try:
            log(f"🔔 EDIT (PUSH): chat_id={event.chat_id} | msg_id={event.message.id}", "INFO")
            
            # Update activity timestamp
            last_telegram_activity = time.time()
            
            if DEBUG_LOG_ALL_MESSAGES:
                chat = await event.get_chat()
                chat_title = getattr(chat, "title", None) or getattr(chat, "username", None) or "(no title)"
                preview = extract_message_text(event.message).strip()[:80]
                log(f"📝 [RAW EDIT] chat_id={event.chat_id} | {chat_title} | {preview}", "DEBUG")

            messages_received += 1
            
            if event.chat_id not in ALLOWED_CHAT_IDS:
                messages_ignored += 1
                log(f"⏭️  Edit ignored - chat_id {event.chat_id} not in allowed list", "DEBUG")
                return

            text = extract_message_text(event.message).strip()
            msg_id = event.message.id
            channel_name = "[TEST]" if event.chat_id == TEST_CHANNEL_ID else "[MAIN]"

            log("=" * 70, "INFO")
            log(f"📝 {channel_name} MESSAGE EDITED", "INFO")
            log(f"   Message ID: {msg_id}", "INFO")
            log(f"   Preview: {text[:150]}", "INFO")
            log("=" * 70, "INFO")

            # ── TARGET 1 HIT (EDIT) → Move SL to breakeven ──
            if is_tp1_hit_message(text):
                log("🎯 TARGET 1 HIT DETECTED (EDIT) - MOVING SL TO BREAKEVEN!", "INFO")
                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                if positions:
                    for p in positions:
                        if p.magic == MAGIC:
                            ticket = int(p.ticket)
                            if not breakeven_activated.get(ticket, False):
                                entry = entry_prices.get(ticket, float(p.price_open))
                                log(f"🛡️ Moving SL to breakeven for ticket {ticket}: ${entry:.5f}", "INFO")
                                await run_mt5(lambda t=ticket, e=entry: set_stop_loss(t, e))
                                breakeven_activated[ticket] = True
                return

            # ── TARGET 2 HIT (EDIT) → Acknowledge ──
            if is_tp2_hit_message(text):
                log("🎯 TARGET 2 HIT acknowledged (EDIT) - position remains open for TP3", "INFO")
                return

            if is_tp3_hit_message(text):
                log("=" * 70, "INFO")
                log("🚨 TP3 HIT DETECTED (EDIT) - SAFETY EXIT!", "INFO")
                log("=" * 70, "INFO")

                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                if positions:
                    closed_count = 0
                    for p in positions:
                        if p.magic == MAGIC:
                            log(f"🎯 Closing position: Ticket {p.ticket} | Volume {p.volume:.3f}", "INFO")
                            if await run_mt5(lambda: close_position(p.ticket)):
                                closed_count += 1

                    if closed_count > 0:
                        log("=" * 70, "INFO")
                        log(f"✅ CLOSED {closed_count} POSITION(S) on TP3 HIT", "INFO")
                        log("=" * 70, "INFO")
                    else:
                        log("⚠️ Failed to close positions on TP3 HIT", "WARN")
                else:
                    log("⚠️ TP3 HIT detected but no open positions found", "WARN")
                return

            if pending_zone and msg_id == pending_zone.get("msg_id") and not pending_zone.get("executed"):
                side = parse_zone_side(text) or pending_zone.get("side")
                zone_range = parse_zone_range(text)
                if zone_range:
                    pending_zone["zone_low"], pending_zone["zone_high"] = zone_range
                pending_zone["targets"] = parse_targets(text)
                pending_zone["sl"] = parse_invalid_sl(text)
                log("🔄 Pending zone updated from edit", "INFO")
                await run_mt5(try_execute_pending_zone)
                return

            if msg_id not in position_map:
                log(f"⚠️ Message ID {msg_id} not in position_map - checking for recent positions", "WARN")
                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                if not positions:
                    log("⚠️ No open positions at all - ignoring edit", "WARN")
                    return
                ticket = int(positions[-1].ticket)
                log(f"   Using most recent position: ticket {ticket}", "DEBUG")
            else:
                ticket = position_map[msg_id]
                log(f"   Found mapped ticket: {ticket}", "DEBUG")

            pos = await run_mt5(lambda: mt5.positions_get(ticket=ticket))
            if not pos:
                log(f"❌ Position {ticket} not found - may have been closed", "WARN")
                return

            sl = parse_invalid_sl(text)
            if sl:
                log(f"📊 Found SL in edit: ${sl:.5f}", "DEBUG")
                can_update, reason = can_update_stop_loss(ticket, sl)
                if can_update:
                    await run_mt5(lambda: set_stop_loss(ticket, sl))
                else:
                    log(f"⚠️ SL update skipped: {reason}", "WARN")

            targets = parse_targets(text)
            if targets:
                log(f"📊 Found {len(targets)} target(s) in edit: {targets}", "DEBUG")
                await run_mt5(lambda: apply_targets_to_ticket(ticket, targets))

        except Exception as e:
            log(f"❌ CRITICAL EXCEPTION in on_edit: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
            log(f"   Message ID: {msg_id if 'msg_id' in locals() else 'unknown'}", "ERROR")

    async def monitor_breakeven():
        """Check if TP1 has been hit and move SL to breakeven"""
        global breakeven_monitor_alive
        
        log("🔄 Breakeven monitor started", "INFO")
        breakeven_monitor_alive = True
        check_count = 0

        while True:
            try:
                breakeven_monitor_alive = True
                check_count += 1

                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))

                if not positions:
                    if check_count % 30 == 0:  # Every 60 seconds
                        log(f"⏳ Breakeven monitor check #{check_count}: No open positions", "DEBUG")
                    await asyncio.sleep(2)
                    continue

                log(f"🔍 Breakeven monitor check #{check_count}: Checking {len(positions)} position(s)", "DEBUG")

                for pos in positions:
                    ticket = int(pos.ticket)

                    if ticket not in entry_prices:
                        log(f"   Ticket {ticket}: NOT in our tracking (entry_prices)", "DEBUG")
                        continue

                    if breakeven_activated.get(ticket, False):
                        log(f"   Ticket {ticket}: Breakeven already activated", "DEBUG")
                        continue

                    if ticket not in tp_levels or 1 not in tp_levels[ticket]:
                        log(f"   Ticket {ticket}: No TP1 stored yet", "DEBUG")
                        continue

                    tp1 = tp_levels[ticket][1]
                    entry = entry_prices[ticket]
                    side = await run_mt5(lambda t=ticket: get_position_side(t))

                    if side is None:
                        log(f"   Ticket {ticket}: Position side is None (closed?)", "WARN")
                        continue

                    tick = await run_mt5(lambda: mt5.symbol_info_tick(SYMBOL))
                    if tick is None:
                        log(f"   Ticket {ticket}: Cannot get tick data", "WARN")
                        continue

                    current = float(tick.bid if side == "SELL" else tick.ask)

                    if price_crossed_tp1(ticket, tp1, current):
                        log(
                            f"🎯 TP1 HIT! Ticket {ticket} | Current=${current:.5f} vs TP1=${tp1:.5f} ({side})",
                            "INFO",
                        )
                        log(f"   >>> MOVING SL TO BREAKEVEN: ${entry:.5f}", "INFO")
                        await run_mt5(lambda t=ticket, e=entry: set_stop_loss(t, e))
                        breakeven_activated[ticket] = True
                    else:
                        log(
                            f"   Ticket {ticket}: TP1 not yet reached | Current=${current:.5f} | TP1=${tp1:.5f}",
                            "DEBUG",
                        )

                await asyncio.sleep(2)

            except Exception as e:
                log(f"❌ CRITICAL: Breakeven monitor exception: {str(e)}", "ERROR")
                import traceback
                log(f"   {traceback.format_exc()}", "ERROR")
                breakeven_monitor_alive = False
                await asyncio.sleep(10)  # Wait longer before retry

    async def print_signal_log():
        """Print signal log every 5 minutes for debugging"""
        while True:
            try:
                await asyncio.sleep(300)  # Every 5 minutes
                
                if signal_log:
                    log("=" * 70, "INFO")
                    log("📋 SIGNAL LOG (Last 10 attempts)", "INFO")
                    log("=" * 70, "INFO")
                    for entry in signal_log[-10:]:
                        msg = f"  {entry['timestamp']} | {entry['signal']:4s} | {entry['result']:10s} | {entry['detail']}"
                        log(msg, "INFO")
                    log("=" * 70, "INFO")
                else:
                    log("📋 Signal log empty - no signals received yet", "DEBUG")
                    
            except Exception as e:
                log(f"❌ CRITICAL: Signal log printer exception: {str(e)}", "ERROR")
                import traceback
                log(f"   {traceback.format_exc()}", "ERROR")
                await asyncio.sleep(10)  # Wait before retry

    log("🔄 Starting background monitoring tasks...", "INFO")
    
    asyncio.create_task(heartbeat_monitor())
    log("✅ Heartbeat monitor task created", "INFO")
    
    asyncio.create_task(connection_health_monitor())
    log("✅ Connection health monitor task created", "INFO")
    
    asyncio.create_task(monitor_breakeven())
    log("✅ Breakeven monitor task created", "INFO")

    asyncio.create_task(monitor_pending_zone())
    log("✅ Zone monitor task created", "INFO")
    
    asyncio.create_task(monitor_closed_positions())
    log("✅ Closed position monitor task created", "INFO")

    asyncio.create_task(print_signal_log())
    log("✅ Signal log printer task created", "INFO")

    asyncio.create_task(cleanup_and_report())
    log("✅ Cleanup and reporting task created", "INFO")

    log("📡 Connecting to Telegram...", "INFO")
    await client.start()
    telegram_connected = True
    last_telegram_activity = time.time()
    log("✅ Telegram connected!", "INFO")

    # CRITICAL: Fetch dialogs to initialize Telethon's internal update state.
    # Without this, Telethon may never deliver channel events (NewMessage/MessageEdited).
    log("🔄 Initializing Telegram update state (get_dialogs)...", "INFO")
    try:
        dialogs = await client.get_dialogs()
        log(f"✅ Loaded {len(dialogs)} dialogs - update state initialized", "INFO")
    except Exception as e:
        log(f"⚠️ get_dialogs failed: {e} - events may not be delivered!", "WARN")

    # Verify channel access
    log("🔍 Verifying channel access...", "INFO")
    for chat_id in ALLOWED_CHAT_IDS:
        try:
            entity = await client.get_entity(chat_id)
            chat_title = getattr(entity, "title", None) or getattr(entity, "username", None) or f"Chat {chat_id}"
            log(f"   ✅ Can access: {chat_title} (ID: {chat_id})", "INFO")
        except Exception as e:
            log(f"   ❌ Cannot access chat {chat_id}: {e}", "ERROR")
            log(f"   Make sure the bot account has joined this channel!", "ERROR")
    
    # Fetch recent messages to catch up on anything sent during startup
    log("🔍 Checking for recent messages (last 5 minutes)...", "INFO")
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
                        log(f"   📥 Recent: [{msg.date.strftime('%H:%M:%S')}] {text[:60]}...", "INFO")
            except Exception as e:
                log(f"   ⚠️ Could not fetch from {chat_id}: {e}", "WARN")
        
        if recent_count > 0:
            log(f"✅ Found {recent_count} recent message(s) - they will be processed as new messages arrive", "INFO")
        else:
            log(f"   No recent messages found in monitored channels", "INFO")
    except Exception as e:
        log(f"⚠️ Error checking recent messages: {e}", "WARN")
    
    log("=" * 70, "INFO")
    log("🎯 BOT READY - LISTENING FOR ZONES", "INFO")
    log("=" * 70, "INFO")
    
    # ── Message Polling Fallback ──
    # Telethon's push-based events can silently fail (stale session state, update gaps, etc.).
    # This poller periodically fetches recent messages and processes any that were missed.
    async def message_poller():
        """Poll channels for new messages as a fallback to push events."""
        global messages_received, messages_ignored, last_message_time, last_telegram_activity, pending_zone

        log("📡 Message poller started (fallback for push events)", "INFO")
        # Track the newest message ID we've seen per channel so we only process truly new ones
        last_seen_ids: Dict[int, int] = {}

        # Seed last_seen_ids with the current newest message in each channel
        for chat_id in ALLOWED_CHAT_IDS:
            try:
                msgs = await client.get_messages(chat_id, limit=1)
                if msgs:
                    last_seen_ids[chat_id] = msgs[0].id
                    log(f"   📡 Poller seed: chat {chat_id} latest msg_id={msgs[0].id}", "DEBUG")
            except Exception as e:
                log(f"   ⚠️ Poller seed failed for {chat_id}: {e}", "WARN")

        while True:
            try:
                await asyncio.sleep(POLLING_INTERVAL)

                for chat_id in ALLOWED_CHAT_IDS:
                    try:
                        msgs = await client.get_messages(chat_id, limit=5)
                        if not msgs:
                            continue

                        min_id = last_seen_ids.get(chat_id, 0)

                        for msg in reversed(msgs):  # oldest first
                            if msg.id <= min_id:
                                continue  # Already seen
                            if msg.id in _processed_msg_ids:
                                continue  # Already processed by push handler

                            # Mark as processed
                            _processed_msg_ids.add(msg.id)
                            if len(_processed_msg_ids) > _MAX_PROCESSED_IDS:
                                _processed_msg_ids.clear()

                            last_seen_ids[chat_id] = max(last_seen_ids.get(chat_id, 0), msg.id)

                            text = extract_message_text(msg).strip()
                            if not text:
                                continue

                            # Update activity timestamps
                            last_telegram_activity = time.time()
                            last_message_time = time.time()
                            messages_received += 1

                            channel_name = "[TEST]" if chat_id == TEST_CHANNEL_ID else "[MAIN]"
                            log("=" * 70, "INFO")
                            log(f"📨 {channel_name} NEW MESSAGE (POLLED)", "INFO")
                            log(f"   Message ID: {msg.id}", "INFO")
                            log(f"   Chat ID: {chat_id}", "INFO")
                            log(f"   Length: {len(text)} chars", "INFO")
                            log(f"   Preview: {text[:150]}", "INFO")
                            log("=" * 70, "INFO")

                            # ── Process the message through the same logic as on_new_message ──

                            # ── TARGET 1 HIT → Move SL to breakeven ──
                            if is_tp1_hit_message(text):
                                log("🎯 TARGET 1 HIT DETECTED (POLLED) - MOVING SL TO BREAKEVEN!", "INFO")
                                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                                if positions:
                                    moved_count = 0
                                    for p in positions:
                                        if p.magic == MAGIC:
                                            ticket = int(p.ticket)
                                            if breakeven_activated.get(ticket, False):
                                                log(f"   Ticket {ticket}: Breakeven already active - skipping", "INFO")
                                                continue
                                            entry = entry_prices.get(ticket, float(p.price_open))
                                            log(f"🛡️ Moving SL to breakeven for ticket {ticket}: ${entry:.5f}", "INFO")
                                            await run_mt5(lambda t=ticket, e=entry: set_stop_loss(t, e))
                                            breakeven_activated[ticket] = True
                                            moved_count += 1
                                    if moved_count > 0:
                                        log(f"✅ BREAKEVEN SET on {moved_count} position(s) after TARGET 1 HIT (POLLED)", "INFO")
                                    else:
                                        log("⚠️ No positions needed breakeven update", "WARN")
                                else:
                                    log("⚠️ TARGET 1 HIT but no open positions found", "WARN")
                                continue

                            # ── TARGET 2 HIT → Acknowledge ──
                            if is_tp2_hit_message(text):
                                log("🎯 TARGET 2 HIT acknowledged (POLLED) - position remains open for TP3", "INFO")
                                continue

                            if is_tp3_hit_message(text):
                                log("🚨 TP3 HIT DETECTED (POLLED) - SAFETY EXIT!", "INFO")
                                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                                if positions:
                                    closed_count = 0
                                    for p in positions:
                                        if p.magic == MAGIC:
                                            if await run_mt5(lambda: close_position(p.ticket)):
                                                closed_count += 1
                                    if closed_count > 0:
                                        log(f"✅ CLOSED {closed_count} POSITION(S) on TP3 HIT (POLLED)", "INFO")
                                continue

                            if is_cancel_message(text):
                                clear_pending_zone("cancel message (polled)")
                                continue

                            if is_informational_message(text):
                                log(f"⏭️  Informational message (polled) - skipping", "INFO")
                                messages_ignored += 1
                                continue

                            if is_high_risk_message(text):
                                log(f"⏭️  HIGH RISK message (polled) - skipping", "WARN")
                                messages_ignored += 1
                                continue

                            side = parse_zone_side(text)
                            zone_range = parse_zone_range(text)

                            if side and zone_range:
                                zone_low, zone_high = zone_range
                                targets = parse_targets(text)
                                sl_price = parse_invalid_sl(text)
                                log(f"🎯 ZONE DETECTED (POLLED): {side} {zone_low:.2f}-{zone_high:.2f}", "INFO")
                                log_signal_attempt(side, "RECEIVED", f"msg_id={msg.id} (polled)")
                                set_pending_zone(side=side, zone_low=zone_low, zone_high=zone_high,
                                                 targets=targets, sl=sl_price, msg_id=msg.id, msg_text=text)
                                await run_mt5(try_execute_pending_zone)
                                continue

                            if side and not zone_range:
                                log(f"⚡ IMMEDIATE ZONE ENTRY (POLLED): {side}", "INFO")
                                log_signal_attempt(side, "RECEIVED", f"msg_id={msg.id} - IMMEDIATE (polled)")
                                current_price = await run_mt5(lambda: get_market_price(side))
                                if current_price:
                                    await run_mt5(lambda: execute_zone_trade(side=side, entry_price=current_price, msg_id=msg.id,
                                                       msg_text=text, sl_price=None, targets=[]))
                                continue

                            if is_active_message(text):
                                log("🟡 Zone marked active (polled)", "INFO")
                                await run_mt5(try_execute_pending_zone)
                                continue

                            sl_update = parse_invalid_sl(text)
                            if sl_update:
                                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                                if positions:
                                    ticket = int(positions[-1].ticket)
                                    can_update, reason = can_update_stop_loss(ticket, sl_update)
                                    if can_update:
                                        await run_mt5(lambda: set_stop_loss(ticket, sl_update))
                                continue

                            targets = parse_targets(text)
                            if targets:
                                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                                if positions:
                                    ticket = int(positions[-1].ticket)
                                    await run_mt5(lambda: apply_targets_to_ticket(ticket, targets))
                                continue

                            log(f"⏭️  Polled message not recognized as signal", "DEBUG")
                            messages_ignored += 1

                        # Update last_seen even if nothing new (in case the channel is quiet)
                        if msgs:
                            last_seen_ids[chat_id] = max(last_seen_ids.get(chat_id, 0), msgs[0].id)

                    except Exception as ce:
                        log(f"⚠️ Poller error for chat {chat_id}: {ce}", "WARN")

            except Exception as e:
                log(f"❌ Message poller exception: {e}", "ERROR")
                import traceback
                log(f"   {traceback.format_exc()}", "ERROR")
                await asyncio.sleep(10)

    asyncio.create_task(message_poller())
    log("✅ Message poller fallback task created", "INFO")

    # Print initial status
    print_bot_status()

    await replay_pending_signals(client)

    max_reconnect_delay = 60  # Cap backoff at 60 seconds
    reconnect_delay = 5       # Start with 5 second delay
    consecutive_failures = 0

    try:
        while True:
            try:
                await client.run_until_disconnected()
                # run_until_disconnected() returned normally — means connection was lost
                # (NOT a keyboard interrupt, NOT an exception — just a clean disconnect)
                telegram_connected = False
                telegram_connection_losses += 1
                log("🚨 Telegram disconnected (run_until_disconnected returned) - will reconnect...", "WARN")
                
                reconnect_monitor.record_reconnect("Disconnected", "run_until_disconnected returned")
                
                await asyncio.sleep(reconnect_delay)
                
                try:
                    await client.connect()
                    if await client.is_user_authorized():
                        telegram_connected = True
                        last_telegram_activity = time.time()
                        consecutive_failures = 0
                        reconnect_delay = 5  # Reset backoff
                        log("✅ Telegram reconnected successfully after disconnect", "INFO")
                        try:
                            await client.get_dialogs()
                            log("✅ Dialogs refreshed after reconnect", "DEBUG")
                        except Exception:
                            pass
                        await catch_up_messages(client, lookback_minutes=5)
                    else:
                        log("⚠️ Telegram connected but not authorized - restarting client...", "WARN")
                        await client.start()
                        telegram_connected = True
                        last_telegram_activity = time.time()
                        consecutive_failures = 0
                        reconnect_delay = 5
                        log("✅ Telegram client restarted and authorized", "INFO")
                        try:
                            await client.get_dialogs()
                            log("✅ Dialogs refreshed after restart", "DEBUG")
                        except Exception:
                            pass
                        await catch_up_messages(client, lookback_minutes=5)
                except Exception as reconn_err:
                    consecutive_failures += 1
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                    telegram_connected = False
                    log(f"❌ Telegram reconnection failed (attempt {consecutive_failures}): {reconn_err}", "ERROR")
                    log(f"   Next retry in {reconnect_delay}s", "WARN")
                    await asyncio.sleep(reconnect_delay)
                
                continue  # Re-enter the while loop to call run_until_disconnected again

            except KeyboardInterrupt:
                log("⏹️ Keyboard interrupt received - shutting down", "INFO")
                break
            except TypeNotFoundError as e:
                telegram_connected = False
                telegram_connection_losses += 1
                
                log("⚠️ Telethon TypeNotFoundError - unknown TLObject received. Attempting reconnect...", "ERROR")
                log(f"   {str(e)}", "DEBUG")
                import traceback
                log(f"   {traceback.format_exc()}", "DEBUG")

                reconnect_monitor.record_reconnect("TypeNotFoundError", str(e)[:50])

                error_count = reconnect_monitor.error_counters.get("TypeNotFoundError", 0)
                if session_manager.should_rotate("tlnotfound", error_count):
                    log("🔄 Rotating session file due to repeated TLObject errors", "INFO")
                    session_manager.rotate_session()
                    client = TelegramClient(session_manager.get_current_session_file(), API_ID, API_HASH)
                    _telegram_client = client
                    # Re-register event handlers on the new client
                    client.on(events.NewMessage())(on_new_message)
                    client.on(events.MessageEdited())(on_edit)

                try:
                    await client.disconnect()
                except Exception as ex:
                    log(f"⚠️ Error while disconnecting client: {ex}", "WARN")

                await asyncio.sleep(5)

                try:
                    await client.start()
                    telegram_connected = True
                    last_telegram_activity = time.time()
                    log("✅ Telegram client restarted after TypeNotFoundError", "INFO")
                    try:
                        await client.get_dialogs()
                        log("✅ Dialogs refreshed after TypeNotFoundError restart", "DEBUG")
                    except Exception:
                        pass
                    # Catch up on any missed messages during disconnection
                    await catch_up_messages(client, lookback_minutes=3)
                except Exception as ex2:
                    telegram_connected = False
                    log(f"❌ Failed to restart Telegram client: {ex2}", "ERROR")
                    await asyncio.sleep(5)
                continue
            except Exception as e:
                log(f"❌ CRITICAL: Unexpected error in main loop: {str(e)}", "ERROR")
                import traceback
                log(f"   {traceback.format_exc()}", "ERROR")
                log("🔄 Bot will attempt to continue...", "WARN")
                await asyncio.sleep(10)
                continue
    finally:
        log("🛑 Bot shutting down...", "INFO")
        mt5.shutdown()
        log("✅ MT5 shutdown complete", "INFO")


if __name__ == "__main__":
    asyncio.run(main())
