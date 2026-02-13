#!/usr/bin/env python3
"""
Gold Trading Bot - Clean Implementation (Robust Edition)
Connects to Telegram channel for gold trading signals and executes trades automatically.
Includes full health monitoring, connection recovery, message polling fallback,
and MT5 thread pool executor — matching bot_zone.py robustness.
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
from typing import Optional, Dict, Tuple

# Import custom modules for signal persistence, session management, and monitoring
from signal_queue import SignalQueue
from session_manager import SessionManager
from reconnect_monitor import ReconnectMonitor

# ===================== CONFIGURATION =====================
# Telegram
API_ID = 34597981
API_HASH = "2cd59609b6cacb56da261e43fdb897ea"
CHANNEL_ID = -1003349563414  # Main trading channel
TEST_CHANNEL_ID = -1003817819872  # Test channel for manual signals
SESSION_FILE = "trading_bot_session"

# Trading
SYMBOL = "XAUUSD"
MAGIC = 777
RISK_PCT = 0.05  # 5% risk per trade
FAILSAFE_SL_DISTANCE = 8.0  # price units away from entry

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
    """Initialize MT5 if not connected"""
    global mt5_connected, mt5_connection_losses

    log("Attempting MT5 initialization...", "DEBUG")

    if not mt5.initialize():
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

        if mt5.initialize():
            log("MT5 reconnection successful", "INFO")
            return True
        else:
            log("MT5 reconnection failed", "ERROR")
            return False
    except Exception as e:
        log(f"MT5 reconnection exception: {e}", "ERROR")
        return False


def get_account_balance() -> float:
    """Get current account balance"""
    acc = mt5.account_info()
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

    symbol_info = mt5.symbol_info(SYMBOL)
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
    """Get the appropriate filling mode for the symbol"""
    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None:
        log("Could not get symbol info, defaulting to IOC", "WARN")
        return mt5.ORDER_FILLING_IOC

    filling = symbol_info.filling_mode

    if filling & 2 == 2:
        log("   Using ORDER_FILLING_IOC", "DEBUG")
        return mt5.ORDER_FILLING_IOC
    elif filling & 1 == 1:
        log("   Using ORDER_FILLING_FOK", "DEBUG")
        return mt5.ORDER_FILLING_FOK
    else:
        log("   Using ORDER_FILLING_RETURN", "DEBUG")
        return mt5.ORDER_FILLING_RETURN


def open_position(side: str, lot: float) -> Optional[int]:
    """
    Open market position. Returns ticket number or None.
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

    log(f"   Request: {request}", "DEBUG")

    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        ticket = result.order
        log(f"Position opened successfully - Ticket: {ticket}", "INFO")
        return ticket

    if result.retcode == 10027:
        log("AutoTrading DISABLED - cannot trade!", "ERROR")
        return None

    log(f"Order failed - Retcode: {result.retcode} | Error: {result.comment}", "ERROR")
    log(f"   Human-readable: {get_error_message(result.retcode)}", "ERROR")
    log(f"   Last MT5 error: {mt5.last_error()}", "ERROR")

    # Retry for certain errors
    retryable_codes = {10009, 10028, 10044, 9}

    if result.retcode in retryable_codes:
        for attempt in range(1, 3):
            log(f"   Retry attempt {attempt}/2 (retcode was {result.retcode})...", "INFO")
            time.sleep(2)
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                ticket = result.order
                log(f"Position opened on retry - Ticket: {ticket}", "INFO")
                return ticket
            log(f"   Retry {attempt} failed: {result.comment}", "WARN")

    return None


def close_position(ticket: int, close_reason: str = "SAFETY_EXIT") -> bool:
    """Close an open position immediately at market price (TP3 HIT safety exit)"""
    log(f"Closing position {ticket} - {close_reason}", "INFO")

    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"Position {ticket} not found (may already be closed)", "WARN")
        return False

    p = pos[0]
    side = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
    close_type = mt5.ORDER_TYPE_SELL if side == "BUY" else mt5.ORDER_TYPE_BUY

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log(f"No tick data for {SYMBOL}", "ERROR")
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
        log(f"Position {ticket} CLOSED successfully", "INFO")
    else:
        log(f"Failed to close position {ticket} - Retcode: {result.retcode} | Error: {result.comment}", "ERROR")
        log(f"   Last MT5 error: {mt5.last_error()}", "ERROR")

    return success


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
    """Calculate failsafe TP3 based on the pattern: worst entry +/- 7 points."""
    tp3 = entry_price + 7.0 if side == "BUY" else entry_price - 7.0
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
    """Select the best valid TP from available levels, with fallback logic.
    Prefers TP3 > TP2 > TP1."""
    for tp_num in [3, 2, 1]:
        if tp_num in tp_levels_dict:
            tp_price = tp_levels_dict[tp_num]
            if is_valid_tp_price(ticket, tp_price):
                log(f"   TP{tp_num} is valid: ${tp_price:.5f}", "DEBUG")
                return tp_num, tp_price
            else:
                log(f"   TP{tp_num} is INVALID: ${tp_price:.5f} - trying lower level", "DEBUG")

    return None, None


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


def get_position_side(ticket: int) -> Optional[str]:
    """Get position direction (BUY or SELL)"""
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"Position {ticket} not found or already closed", "WARN")
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
            f"   [TP1 Check] Ticket {ticket}: Current=${current_price:.5f} vs TP1=${tp1:.5f} (BUY) -> {'CROSSED' if crossed else 'not crossed'}",
            "DEBUG",
        )
    else:
        crossed = current_price <= tp1
        log(
            f"   [TP1 Check] Ticket {ticket}: Current=${current_price:.5f} vs TP1=${tp1:.5f} (SELL) -> {'CROSSED' if crossed else 'not crossed'}",
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
    """Check if message is a TP3 HIT signal (safety exit trigger)"""
    t = (text or "").upper()
    return bool(re.search(r"\bTP\s*3\s+HIT\b|\bTARGET\s*3\b", t))


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

    matches = re.finditer(r"TP\s*(\d+)\s+([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)

    for match in matches:
        tp_num = int(match.group(1))
        tp_price = float(match.group(2))

        if tp_price >= 100:
            levels[tp_num] = tp_price
            log(f"   Found TP{tp_num}: ${tp_price:.5f}", "DEBUG")

    if not levels:
        log("   No TP levels found", "DEBUG")

    return levels


def parse_stop_loss(text: str) -> Optional[float]:
    """
    Parse stop loss value from text.
    Looks for patterns like "SL 4888" or "SL 4873"
    """
    log("Parsing stop loss...", "DEBUG")

    match = re.search(r"SL\s+([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)

    if match:
        sl_price = float(match.group(1))
        if sl_price >= 100:
            log(f"   Found SL: ${sl_price:.5f}", "DEBUG")
            return sl_price

    log("   No valid SL found", "DEBUG")
    return None


def parse_tp1(text: str) -> Optional[float]:
    """Parse TP1 value from text"""
    match = re.search(r"TP\s*1\s+([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)

    if match:
        tp1_price = float(match.group(1))
        if tp1_price >= 100:
            return tp1_price

    return None


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
        "breakeven_monitor_alive": breakeven_monitor_alive,
        "active_positions": len(position_map),
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
    log(f"  Monitors: Breakeven={('OK' if metrics['breakeven_monitor_alive'] else 'DOWN')}", "INFO")
    log(f"  Active Positions: {metrics['active_positions']}", "INFO")

    if last_message_time > 0:
        log(f"  Last Message: {metrics['seconds_since_last_message']:.0f}s ago", "INFO")
    else:
        log("  Last Message: Never", "INFO")

    log("=" * 70, "INFO")


# ===================== TELEGRAM HANDLERS =====================

def init_telegram():
    """Create Telegram client"""
    return TelegramClient(SESSION_FILE, API_ID, API_HASH)


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
    """Regular heartbeat to prove bot is alive - runs every 60 seconds"""
    global heartbeat_counter

    log("Heartbeat monitor started", "INFO")

    while True:
        try:
            await asyncio.sleep(60)

            heartbeat_counter += 1
            metrics = get_bot_metrics()

            log("=" * 70, "INFO")
            log(f"HEARTBEAT #{heartbeat_counter} - BOT ALIVE", "INFO")
            log(f"  Uptime: {metrics['uptime_hours']:.2f}h | Msgs: {metrics['messages_received']} | Trades: {metrics['trades_executed']}", "INFO")
            log(f"  MT5: {('OK' if metrics['mt5_connected'] else 'DOWN')} | Telegram: {('OK' if metrics['telegram_connected'] else 'DOWN')}", "INFO")
            log("=" * 70, "INFO")

        except Exception as e:
            log(f"CRITICAL: Heartbeat monitor exception: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
            await asyncio.sleep(10)


async def connection_health_monitor():
    """Monitor MT5 and Telegram connection health - runs every 60 seconds"""
    global mt5_connected, telegram_connected

    log("Connection health monitor started", "INFO")

    while True:
        try:
            await asyncio.sleep(60)

            log("Running connection health checks...", "DEBUG")

            # Check MT5 connection (in executor to avoid blocking)
            mt5_healthy = await run_mt5(check_mt5_health)
            if not mt5_healthy:
                log("MT5 connection unhealthy - attempting recovery", "WARN")
                recovered = await run_mt5(recover_mt5_connection)
                if recovered:
                    log("MT5 connection recovered", "INFO")
                    mt5_connected = True
                else:
                    log("MT5 connection recovery failed", "ERROR")
                    mt5_connected = False
            else:
                log("MT5 connection healthy", "DEBUG")

            # Telegram connectivity check
            if last_telegram_activity > 0:
                time_since_telegram = time.time() - last_telegram_activity

                if time_since_telegram < 120:
                    log(f"Telegram active ({time_since_telegram:.0f}s since last message)", "DEBUG")
                    telegram_connected = True
                elif time_since_telegram > 300:
                    log(f"No Telegram activity for {time_since_telegram:.0f}s - running active connectivity check...", "WARN")

                    if _telegram_client is not None:
                        try:
                            if not _telegram_client.is_connected():
                                log("Telegram client.is_connected() = False - forcing disconnect to trigger reconnection", "WARN")
                                telegram_connected = False
                                try:
                                    await _telegram_client.disconnect()
                                except Exception:
                                    pass
                            else:
                                try:
                                    await asyncio.wait_for(_telegram_client.get_me(), timeout=10)
                                    log("Telegram ping successful - connection alive, channel is just quiet", "DEBUG")
                                    telegram_connected = True
                                except (asyncio.TimeoutError, Exception) as ping_err:
                                    log(f"Telegram ping FAILED ({ping_err}) - connection is dead, forcing disconnect", "WARN")
                                    telegram_connected = False
                                    try:
                                        await _telegram_client.disconnect()
                                    except Exception:
                                        pass
                        except Exception as check_err:
                            log(f"Error during active Telegram check: {check_err}", "WARN")
                            telegram_connected = False
                    else:
                        telegram_connected = False
                else:
                    telegram_connected = True

        except Exception as e:
            log(f"CRITICAL: Health monitor exception: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
            await asyncio.sleep(10)


async def monitor_closed_positions():
    """Monitor for naturally closed positions (stub for forward compatibility)"""
    log("Closed position monitor started", "INFO")

    while True:
        try:
            await asyncio.sleep(30)
        except Exception as e:
            log(f"Closed position monitor error: {str(e)}", "ERROR")
            await asyncio.sleep(10)


async def cleanup_and_report():
    """Periodic cleanup and reporting of system health."""
    while True:
        try:
            await asyncio.sleep(300)  # Every 5 minutes

            log("Running periodic cleanup...", "DEBUG")

            if signal_queue:
                signal_queue.remove_old_signals(days=7)
                stats = signal_queue.get_stats()
                log(f"Signal queue: {stats['total_signals']} total, {stats['pending']} pending", "DEBUG")

            if session_manager:
                session_manager.cleanup_old_backups(keep_count=5)

            if reconnect_monitor:
                reconnect_monitor.reset_window()
                if reconnect_monitor.get_stats()['total_reconnects'] > 0:
                    reconnect_monitor.print_summary()

            # Print full status report
            print_bot_status()

        except Exception as e:
            log(f"CRITICAL: Cleanup exception: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
            await asyncio.sleep(10)


# ===================== PROCESS SIGNAL MESSAGE =====================

def execute_signal_trade(side: str, entry_price: float, msg_id: int, msg_text: str):
    """Execute a trade from a parsed signal - runs inside the MT5 executor."""
    balance = get_account_balance()
    if balance <= 0:
        log(f"Invalid balance: ${balance:.2f} - aborting trade", "ERROR")
        log_signal_attempt(side, "FAILED", f"Invalid balance: ${balance:.2f}")
        return

    failsafe_sl = entry_price - FAILSAFE_SL_DISTANCE if side == "BUY" else entry_price + FAILSAFE_SL_DISTANCE

    log(f"Failsafe SL distance: ${FAILSAFE_SL_DISTANCE:.5f}", "DEBUG")
    log(f"Failsafe SL price: ${failsafe_sl:.5f}", "DEBUG")

    lot = calculate_lot_size(entry_price, failsafe_sl, balance)

    if lot <= 0:
        log(f"Invalid lot size: {lot} - aborting trade", "ERROR")
        log_signal_attempt(side, "FAILED", f"Invalid lot size: {lot}")
        return

    log(f"Trade parameters: Entry=${entry_price:.5f} | SL=${failsafe_sl:.5f} | Lot={lot:.4f}", "INFO")

    ticket = open_position(side, lot)

    if ticket:
        log(f"POSITION OPENED: Ticket={ticket} | Side={side} | Entry=${entry_price:.5f} | Lot={lot:.4f}", "INFO")
        log_signal_attempt(side, "SUCCESS", f"Ticket={ticket} | Entry=${entry_price:.5f}")
        position_map[msg_id] = ticket
        entry_prices[ticket] = entry_price
        breakeven_activated[ticket] = False
        log(f"   Stored in position_map: msg_id={msg_id} -> ticket={ticket}", "DEBUG")

        signal_queue.add_signal(
            signal_type="TRADE",
            side=side,
            entry_price=entry_price,
            sl_price=failsafe_sl,
            lot_size=lot,
            tp_levels={},
            msg_id=msg_id,
            msg_text=msg_text[:100],
        )
        signal_queue.mark_executed(msg_id)

        log("Waiting for MT5 to register position...", "DEBUG")
        time.sleep(0.5)
        set_stop_loss(ticket, failsafe_sl)

        failsafe_tp3_price = calculate_failsafe_tp3(entry_price, side)
        failsafe_tp3[ticket] = failsafe_tp3_price
        log("Failsafe TP3 distance: 7 points", "INFO")
        log(f"Failsafe TP3 price: ${failsafe_tp3_price:.5f}", "DEBUG")
        set_take_profit(ticket, failsafe_tp3_price)
    else:
        log("POSITION OPEN FAILED - returning", "ERROR")
        log_signal_attempt(side, "FAILED", "Position open failed (see above for details)")


# ===================== MAIN =====================

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

    client = init_telegram()
    _telegram_client = client
    ensure_mt5_connection()

    log("=" * 70, "INFO")
    log("BOT INITIALIZATION COMPLETE", "INFO")
    log("=" * 70, "INFO")
    log(f"  Bot start time: {start_timestamp}", "INFO")
    log(f"  Main channel ID: {CHANNEL_ID}", "INFO")
    log(f"  Test channel ID: {TEST_CHANNEL_ID}", "INFO")
    log(f"  Trading symbol: {SYMBOL}", "INFO")
    log(f"  Risk per trade: {RISK_PCT*100:.1f}%", "INFO")
    log(f"  Failsafe SL distance: ${FAILSAFE_SL_DISTANCE:.2f}", "INFO")
    log(f"  Magic number: {MAGIC}", "INFO")
    log(f"  Log level: {LOG_LEVEL}", "INFO")
    log("=" * 70, "INFO")

    # ── Helper: move SL to breakeven on all open bot positions ──
    async def handle_tp1_breakeven(source_label: str = ""):
        """Move SL to breakeven on all open bot positions."""
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
                    log(f"Moving SL to breakeven for ticket {ticket}: ${entry:.5f}", "INFO")
                    await run_mt5(lambda t=ticket, e=entry: set_stop_loss(t, e))
                    breakeven_activated[ticket] = True
                    moved_count += 1
            if moved_count > 0:
                log(f"BREAKEVEN SET on {moved_count} position(s) after TARGET 1 HIT{source_label}", "INFO")
            else:
                log("No positions needed breakeven update", "WARN")
        else:
            log("TARGET 1 HIT but no open positions found", "WARN")

    async def handle_tp3_close(source_label: str = ""):
        """Close all open bot positions on TP3 hit."""
        positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
        if positions:
            closed_count = 0
            for p in positions:
                if p.magic == MAGIC:
                    log(f"Closing position: Ticket {p.ticket} | Volume {p.volume:.3f}", "INFO")
                    if await run_mt5(lambda t=int(p.ticket): close_position(t)):
                        closed_count += 1
            if closed_count > 0:
                log(f"CLOSED {closed_count} POSITION(S) on TP3 HIT{source_label}", "INFO")
            else:
                log("Failed to close positions on TP3 HIT", "WARN")
        else:
            log("TP3 HIT detected but no open positions found", "WARN")

    # ── Process any message text through the signal pipeline ──
    async def process_message(text: str, msg_id: int, channel_name: str, source: str = ""):
        """Core message processing shared by push handler and poller."""
        global messages_ignored

        # ── TARGET 1 HIT -> Move SL to breakeven ──
        if is_tp1_hit_message(text):
            log("=" * 70, "INFO")
            log(f"TARGET 1 HIT DETECTED{source} - MOVING SL TO BREAKEVEN!", "INFO")
            log("=" * 70, "INFO")
            await handle_tp1_breakeven(source)
            return True

        # ── TARGET 2 HIT -> Acknowledge ──
        if is_tp2_hit_message(text):
            log(f"TARGET 2 HIT acknowledged{source} - position remains open for TP3", "INFO")
            return True

        # ── TP3 HIT -> Safety close ──
        if is_tp3_hit_message(text):
            log("=" * 70, "INFO")
            log(f"TP3 HIT DETECTED{source} - SAFETY EXIT!", "INFO")
            log("=" * 70, "INFO")
            await handle_tp3_close(source)
            return True

        # ── Trade signal (BUY NOW / SELL NOW) ──
        signal = parse_signal(text)
        if signal:
            symbol, side = signal
            log(f"SIGNAL DETECTED{source}: {side} {symbol}", "INFO")
            log_signal_attempt(side, "RECEIVED", f"msg_id={msg_id}{source}")

            entry_price = await run_mt5(lambda: get_market_price(side))
            if entry_price is None:
                log("Cannot get market price - aborting trade", "ERROR")
                log_signal_attempt(side, "FAILED", "Cannot get market price")
                return True

            await run_mt5(lambda: execute_signal_trade(side, entry_price, msg_id, text))
            return True

        # ── SL / TP update messages ──
        sl = parse_stop_loss(text)
        tp_levels_parsed = parse_tp_levels(text)

        if sl or tp_levels_parsed:
            positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
            if not positions:
                log("No open positions to update", "WARN")
                return True

            ticket = int(positions[-1].ticket)

            if sl:
                can_update, reason = can_update_stop_loss(ticket, sl)
                if can_update:
                    await run_mt5(lambda: set_stop_loss(ticket, sl))
                else:
                    log(f"SL skipped: {reason}", "WARN")

            if tp_levels_parsed:
                tp_levels[ticket] = tp_levels_parsed
                tp_num, tp_price = select_best_tp_with_fallback(ticket, tp_levels_parsed)
                if tp_num and tp_price:
                    log(f"TP{tp_num} set (${tp_price:.5f})", "INFO")
                    await run_mt5(lambda: set_take_profit(ticket, tp_price))
                else:
                    log("TP levels invalid - waiting for valid values", "WARN")
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
                _processed_msg_ids.clear()

            messages_received += 1

            # Filter by allowed chat IDs
            if event.chat_id not in ALLOWED_CHAT_IDS:
                messages_ignored += 1
                if DEBUG_LOG_ALL_MESSAGES:
                    chat = await event.get_chat()
                    chat_title = getattr(chat, "title", None) or getattr(chat, "username", None) or "(no title)"
                    log(f"Ignored: chat_id={event.chat_id} | {chat_title}", "DEBUG")
                return

            last_telegram_activity = time.time()
            last_message_time = time.time()

            text = extract_message_text(event.message).strip()
            msg_id = event.message.id
            channel_name = "[TEST]" if event.chat_id == TEST_CHANNEL_ID else "[MAIN]"

            if DEBUG_LOG_ALL_MESSAGES:
                chat = await event.get_chat()
                chat_title = getattr(chat, "title", None) or getattr(chat, "username", None) or "(no title)"
                log(f"[RAW] chat_id={event.chat_id} | {chat_title} | {text[:80]}", "DEBUG")

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
                log("Message not recognized as signal - ignoring", "DEBUG")
                log(f"   Checks: TP1={is_tp1_hit_message(text)} | TP2={is_tp2_hit_message(text)} | TP3={is_tp3_hit_message(text)} | SIGNAL={parse_signal(text) is not None}", "DEBUG")
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
        """Handle edited messages from main or test channel"""
        global messages_received, messages_ignored, last_telegram_activity

        try:
            log(f"EDIT (PUSH): chat_id={event.chat_id} | msg_id={event.message.id}", "INFO")

            last_telegram_activity = time.time()

            if DEBUG_LOG_ALL_MESSAGES:
                chat = await event.get_chat()
                chat_title = getattr(chat, "title", None) or getattr(chat, "username", None) or "(no title)"
                preview = extract_message_text(event.message).strip()[:80]
                log(f"[RAW EDIT] chat_id={event.chat_id} | {chat_title} | {preview}", "DEBUG")

            messages_received += 1

            if event.chat_id not in ALLOWED_CHAT_IDS:
                messages_ignored += 1
                log(f"Edit ignored - chat_id {event.chat_id} not in allowed list", "DEBUG")
                return

            text = extract_message_text(event.message).strip()
            msg_id = event.message.id
            channel_name = "[TEST]" if event.chat_id == TEST_CHANNEL_ID else "[MAIN]"

            log("=" * 70, "INFO")
            log(f"{channel_name} MESSAGE EDITED", "INFO")
            log(f"   Message ID: {msg_id}", "INFO")
            log(f"   Preview: {text[:150]}", "INFO")
            log("=" * 70, "INFO")

            # ── TARGET 1 HIT (EDIT) -> Move SL to breakeven ──
            if is_tp1_hit_message(text):
                log("TARGET 1 HIT DETECTED (EDIT) - MOVING SL TO BREAKEVEN!", "INFO")
                await handle_tp1_breakeven(" (EDIT)")
                return

            # ── TARGET 2 HIT (EDIT) -> Acknowledge ──
            if is_tp2_hit_message(text):
                log("TARGET 2 HIT acknowledged (EDIT) - position remains open for TP3", "INFO")
                return

            # ── TP3 HIT (EDIT) -> Safety close ──
            if is_tp3_hit_message(text):
                log("=" * 70, "INFO")
                log("TP3 HIT DETECTED (EDIT) - SAFETY EXIT!", "INFO")
                log("=" * 70, "INFO")
                await handle_tp3_close(" (EDIT)")
                return

            # Find position for this message
            if msg_id not in position_map:
                log(f"Message ID {msg_id} not in position_map - checking for recent positions", "WARN")
                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                if not positions:
                    log("No open positions at all - ignoring edit", "WARN")
                    return
                ticket = int(positions[-1].ticket)
                log(f"   Using most recent position: ticket {ticket}", "DEBUG")
            else:
                ticket = position_map[msg_id]
                log(f"   Found mapped ticket: {ticket}", "DEBUG")

            pos = await run_mt5(lambda: mt5.positions_get(ticket=ticket))
            if not pos:
                log(f"Position {ticket} not found - may have been closed", "WARN")
                return

            # Update SL if present
            sl = parse_stop_loss(text)
            if sl:
                log(f"Found SL in edit: ${sl:.5f}", "DEBUG")
                can_update, reason = can_update_stop_loss(ticket, sl)
                if can_update:
                    await run_mt5(lambda: set_stop_loss(ticket, sl))
                else:
                    log(f"SL update skipped: {reason}", "WARN")

            # Update TP levels if present
            tp_levels_parsed = parse_tp_levels(text)
            if tp_levels_parsed:
                log(f"Found {len(tp_levels_parsed)} TP level(s) in edit: {tp_levels_parsed}", "DEBUG")
                tp_levels[ticket] = tp_levels_parsed
                tp_num, tp_price = select_best_tp_with_fallback(ticket, tp_levels_parsed)
                if tp_num and tp_price:
                    log(f"TP{tp_num} set (${tp_price:.5f})", "INFO")
                    await run_mt5(lambda: set_take_profit(ticket, tp_price))
                else:
                    log("TP levels invalid - waiting for valid values", "WARN")

        except Exception as e:
            log(f"CRITICAL EXCEPTION in on_edit: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")

    # ══════════════════════════════════════════════════════════════════════
    # Breakeven monitor (price-based fallback)
    # ══════════════════════════════════════════════════════════════════════
    async def monitor_breakeven():
        """Check if TP1 has been hit and move SL to breakeven"""
        global breakeven_monitor_alive

        log("Breakeven monitor started", "INFO")
        breakeven_monitor_alive = True
        check_count = 0

        while True:
            try:
                breakeven_monitor_alive = True
                check_count += 1

                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))

                if not positions:
                    if check_count % 30 == 0:
                        log(f"Breakeven monitor check #{check_count}: No open positions", "DEBUG")
                    await asyncio.sleep(2)
                    continue

                log(f"Breakeven monitor check #{check_count}: Checking {len(positions)} position(s)", "DEBUG")

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
                            f"TP1 HIT! Ticket {ticket} | Current=${current:.5f} vs TP1=${tp1:.5f} ({side})",
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
                log(f"CRITICAL: Breakeven monitor exception: {str(e)}", "ERROR")
                import traceback
                log(f"   {traceback.format_exc()}", "ERROR")
                breakeven_monitor_alive = False
                await asyncio.sleep(10)

    # ── Signal log printer ──
    async def print_signal_log():
        """Print signal log every 5 minutes for debugging"""
        while True:
            try:
                await asyncio.sleep(300)

                if signal_log:
                    log("=" * 70, "INFO")
                    log("SIGNAL LOG (Last 10 attempts)", "INFO")
                    log("=" * 70, "INFO")
                    for entry in signal_log[-10:]:
                        msg = f"  {entry['timestamp']} | {entry['signal']:4s} | {entry['result']:10s} | {entry['detail']}"
                        log(msg, "INFO")
                    log("=" * 70, "INFO")
                else:
                    log("Signal log empty - no signals received yet", "DEBUG")

            except Exception as e:
                log(f"CRITICAL: Signal log printer exception: {str(e)}", "ERROR")
                import traceback
                log(f"   {traceback.format_exc()}", "ERROR")
                await asyncio.sleep(10)

    # ══════════════════════════════════════════════════════════════════════
    # Start background monitoring tasks
    # ══════════════════════════════════════════════════════════════════════
    log("Starting background monitoring tasks...", "INFO")

    asyncio.create_task(heartbeat_monitor())
    log("  Heartbeat monitor task created", "INFO")

    asyncio.create_task(connection_health_monitor())
    log("  Connection health monitor task created", "INFO")

    asyncio.create_task(monitor_breakeven())
    log("  Breakeven monitor task created", "INFO")

    asyncio.create_task(monitor_closed_positions())
    log("  Closed position monitor task created", "INFO")

    asyncio.create_task(print_signal_log())
    log("  Signal log printer task created", "INFO")

    asyncio.create_task(cleanup_and_report())
    log("  Cleanup and reporting task created", "INFO")

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
                                _processed_msg_ids.clear()

                            last_seen_ids[chat_id] = max(last_seen_ids.get(chat_id, 0), msg.id)

                            text = extract_message_text(msg).strip()
                            if not text:
                                continue

                            last_telegram_activity = time.time()
                            last_message_time = time.time()
                            messages_received += 1

                            channel_name = "[TEST]" if chat_id == TEST_CHANNEL_ID else "[MAIN]"
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
    max_reconnect_delay = 60
    reconnect_delay = 5
    consecutive_failures = 0

    try:
        while True:
            try:
                await client.run_until_disconnected()
                # run_until_disconnected() returned normally - connection was lost
                telegram_connected = False
                telegram_connection_losses += 1
                log("Telegram disconnected (run_until_disconnected returned) - will reconnect...", "WARN")

                reconnect_monitor.record_reconnect("Disconnected", "run_until_disconnected returned")

                await asyncio.sleep(reconnect_delay)

                try:
                    await client.connect()
                    if await client.is_user_authorized():
                        telegram_connected = True
                        last_telegram_activity = time.time()
                        consecutive_failures = 0
                        reconnect_delay = 5
                        log("Telegram reconnected successfully after disconnect", "INFO")
                        try:
                            await client.get_dialogs()
                            log("Dialogs refreshed after reconnect", "DEBUG")
                        except Exception:
                            pass
                        await catch_up_messages(client, lookback_minutes=5)
                    else:
                        log("Telegram connected but not authorized - restarting client...", "WARN")
                        await client.start()
                        telegram_connected = True
                        last_telegram_activity = time.time()
                        consecutive_failures = 0
                        reconnect_delay = 5
                        log("Telegram client restarted and authorized", "INFO")
                        try:
                            await client.get_dialogs()
                            log("Dialogs refreshed after restart", "DEBUG")
                        except Exception:
                            pass
                        await catch_up_messages(client, lookback_minutes=5)
                except Exception as reconn_err:
                    consecutive_failures += 1
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                    telegram_connected = False
                    log(f"Telegram reconnection failed (attempt {consecutive_failures}): {reconn_err}", "ERROR")
                    log(f"   Next retry in {reconnect_delay}s", "WARN")
                    await asyncio.sleep(reconnect_delay)

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

                try:
                    await client.disconnect()
                except Exception as ex:
                    log(f"Error while disconnecting client: {ex}", "WARN")

                await asyncio.sleep(5)

                try:
                    await client.start()
                    telegram_connected = True
                    last_telegram_activity = time.time()
                    log("Telegram client restarted after TypeNotFoundError", "INFO")
                    try:
                        await client.get_dialogs()
                        log("Dialogs refreshed after TypeNotFoundError restart", "DEBUG")
                    except Exception:
                        pass
                    await catch_up_messages(client, lookback_minutes=3)
                except Exception as ex2:
                    telegram_connected = False
                    log(f"Failed to restart Telegram client: {ex2}", "ERROR")
                    await asyncio.sleep(5)
                continue
            except Exception as e:
                log(f"CRITICAL: Unexpected error in main loop: {str(e)}", "ERROR")
                import traceback
                log(f"   {traceback.format_exc()}", "ERROR")
                log("Bot will attempt to continue...", "WARN")
                await asyncio.sleep(10)
                continue
    finally:
        log("Bot shutting down...", "INFO")
        mt5.shutdown()
        log("MT5 shutdown complete", "INFO")


if __name__ == "__main__":
    asyncio.run(main())
