#!/usr/bin/env python3
"""
Gold Trading Bot - Split Entry / TP3 Strategy
Connects to Telegram channel for gold trading signals and executes trades automatically.

STRATEGY:
  Entry — splits the calculated lot in half and places two entries depending on price vs zone:
    Case 1 (price past zone toward TP):
        - Half lot pending at WORST entry (zone edge closest to current price)
        - Half lot pending at DEEP entry (worst entry ± SPLIT_ENTRY_DEEP_OFFSET = $2.5)
    Case 2 (price inside zone, above/before deep entry area):
        - Half lot at MARKET immediately
        - Half lot pending limit at DEEP entry
    Case 3 (price on wrong side of zone, hasn't entered yet):
        - Full lot pending STOP at BEST entry (zone edge furthest from current price)

  In-trade adjustments:
    Both filled → when price returns to worst-entry level:
        - Worst position SL → breakeven (entry price)
        - Deep position SL → deep_entry + SPLIT_ENTRY_BE_BUFFER ($0.5)  [BUY: +0.5, SELL: -0.5]
    Only worst filled (deep never filled) → when price hits TP1:
        - Worst position SL → breakeven (entry price)

  Exit:
    - TP target = TP3 (fallback TP2, TP1) for all positions
    - Failsafe TP = $8 above/below entry (until real TP arrives)
    - Failsafe SL = $8 away from entry

Priority: Never miss a trade. Designed to run unattended for weeks/months.
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

from signal_queue import SignalQueue
from session_manager import SessionManager
from reconnect_monitor import ReconnectMonitor
from trade_outcome_tracker import TradeOutcomeTracker
from signal_records import SignalRecordsManager, level_to_int

# ===================== CONFIGURATION =====================
API_ID = 34597981
API_HASH = "2cd59609b6cacb56da261e43fdb897ea"
CHANNEL_ID = -1003349563414
SESSION_FILE = "trading_bot_session_split_entry"

SYMBOL = "XAUUSD"
MAGIC = 780
RISK_PCT = 0.1
FAILSAFE_SL_DISTANCE = 8.0   # $8 away from entry
FAILSAFE_TP_DISTANCE = 8.0   # $8 away from entry (failsafe until real TP arrives)

# Split entry constants
SPLIT_ENTRY_DEEP_OFFSET = 2.5   # $2.5 from worst entry toward zone interior
SPLIT_ENTRY_BE_BUFFER   = 0.5   # $0.5 above deep entry for breakeven SL
TP_EARLY_EXIT_OFFSET    = 0.5   # $0.5 early exit: BUY TP lowered, SELL TP raised

LIMIT_ORDER_TIMEOUT = 14400  # 4 hours
ZONE_WAIT_TIMEOUT   = 120    # 2 min wait for zone via edit

ALLOWED_CHAT_IDS = {CHANNEL_ID}
DEBUG_LOG_ALL_MESSAGES = True
LOG_LEVEL = "INFO"

# ===================== STATE =====================
position_map:    Dict[int, int]  = {}
entry_prices:    Dict[int, float] = {}
tp_sl_updated:   Dict[int, bool]  = {}

# Split-entry order tracking: msg_id -> state dict (see execute_split_entry_trade for schema)
_split_orders: Dict[int, Dict] = {}

# Buffered signals waiting for zone via edit
_buffered_signals: Dict[int, Dict] = {}

signal_log: list = []

# Error/warning log: list of {time, level, message}
_error_log: list = []

signal_queue:      Optional[SignalQueue]          = None
session_manager:   Optional[SessionManager]        = None
reconnect_monitor: Optional[ReconnectMonitor]      = None
outcome_tracker:   Optional[TradeOutcomeTracker]   = None
signal_records:    Optional[SignalRecordsManager]  = None
_ticket_to_msg_id: Dict[int, int] = {}

_telegram_client = None

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

_processed_msg_ids: set = set()
_MAX_PROCESSED_IDS = 500
POLLING_INTERVAL = 0.2

_mt5_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5")

_RE_TP_LEVELS  = re.compile(r"TP\s*(\d+)\s+([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_RE_STOP_LOSS  = re.compile(r"SL\s+([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_RE_TP1        = re.compile(r"TP\s*1\s+([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_RE_TP_BARE    = re.compile(r"\bTP\s+(\d{4,5}(?:\.\d{1,2})?)\b", re.IGNORECASE)
_RE_ENTRY_ZONE = re.compile(r"(\d{4,5}(?:\.\d{1,2})?)\s*[-\u2013\u2014]\s*(\d{4,5}(?:\.\d{1,2})?)")
# Only cancel pending orders on TP3+ HIT or SL HIT (TP1/TP2 hits keep the trade running)
# Matches "TP3 HIT", "TP5 HIT", "TP1,2,3,4 HIT" (any listed digit >=3), "SL HIT"
_RE_TRADE_CLOSURE = re.compile(r"(?:TP[\s\d,]*[3-9][\s,\d]*\s*HIT|SL\s+HIT)", re.IGNORECASE | re.MULTILINE)

_LOG_SEP = "=" * 70
_cached_filling_mode: Optional[int] = None

MAX_CONSECUTIVE_FAILURES = 10
_restart_requested = False

_cached_symbol_info = None
_cached_account_info = None
_cache_last_refresh: float = 0
_CACHE_REFRESH_INTERVAL = 5.0


def refresh_mt5_cache():
    global _cached_symbol_info, _cached_account_info, _cache_last_refresh
    _cached_symbol_info  = mt5.symbol_info(SYMBOL)
    _cached_account_info = mt5.account_info()
    _cache_last_refresh  = time.time()


def get_cached_symbol_info():
    if _cached_symbol_info is None or (time.time() - _cache_last_refresh) > _CACHE_REFRESH_INTERVAL:
        refresh_mt5_cache()
    return _cached_symbol_info


def get_cached_account_info():
    if _cached_account_info is None or (time.time() - _cache_last_refresh) > _CACHE_REFRESH_INTERVAL:
        refresh_mt5_cache()
    return _cached_account_info


async def run_mt5(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_mt5_executor, func, *args)


def is_market_open() -> bool:
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        return False
    if info.trade_mode == 3:
        return False
    if info.trade_mode == 0:
        return False
    return True


# ===================== HELPERS =====================

def _bali_iso() -> str:
    """Return current time as ISO-8601 string with Bali (+08:00) offset."""
    from datetime import timezone, timedelta as _td
    bali = timezone(_td(hours=8))
    return datetime.now(tz=bali).isoformat(timespec='seconds')


def _load_errors_from_json():
    """Seed _error_log from the existing file so history survives restarts."""
    global _error_log
    try:
        if os.path.exists(ERRORS_FILE):
            with open(ERRORS_FILE) as f:
                data = json.load(f)
            _error_log = data.get("errors", [])
    except Exception:
        pass


def _flush_errors_to_json():
    """Append _error_log to data/errors_<MAGIC>.json (accumulates across restarts)."""
    try:
        os.makedirs(os.path.dirname(ERRORS_FILE), exist_ok=True)
        output = {
            "last_updated": _bali_iso(),
            "magic": MAGIC,
            "errors": _error_log,
        }
        with open(ERRORS_FILE, "w") as f:
            json.dump(output, f, indent=2)
    except Exception:
        pass  # never let error logging crash the bot


def log(msg: str, level: str = "INFO"):
    levels = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
    if levels.get(level, 1) >= levels.get(LOG_LEVEL, 1):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            print(f"[{ts}] [{level}] {msg}", flush=True)
        except UnicodeEncodeError:
            print(f"[{ts}] [{level}] {msg.encode('ascii','ignore').decode('ascii')}", flush=True)

    if level in ("WARN", "ERROR"):
        _error_log.append({"time": _bali_iso(), "level": level, "message": msg})
        _flush_errors_to_json()


def get_error_message(retcode: int) -> str:
    error_map = {
        1:"Generic error",2:"Invalid request",3:"Invalid order volume",4:"Invalid order price",
        5:"Invalid stops",6:"Trade disabled",7:"Market closed",8:"No money",9:"Price changed",
        10:"Requote",11:"Order expired",12:"Order cancelled",13:"Invalid response",14:"Invalid request",
        15:"Request timeout",16:"Invalid request repeat",17:"Reference error",18:"Unknown order",
        19:"Order duplicate",20:"Trade busy",21:"No connection",22:"No money",23:"Too frequent requests",
        24:"Malfunctional price",25:"Broker busy",26:"Account suspended",27:"Account forbidden",
        28:"Unknown symbol",29:"Wrong order type",30:"Wrong order size",31:"Wrong order price",
        32:"Wrong stop level",33:"Wrong filling",34:"Request rejected",35:"Order partial filled",
        10004:"Trade disabled by administrator",10005:"Trade forbidden by another manager",
        10006:"Trade disabled by experts",10009:"Request in progress",10011:"Order locked",
        10012:"Only buy allowed",10013:"Only sell allowed",10014:"Position by symbol locked",
        10015:"Close only allowed",10016:"Fifo rule restrictions",10017:"Account disabled",
        10018:"Pending orders limit exceeded",10019:"Order locked for modification",
        10020:"Order locked for deletion",10021:"Order too close to market",
        10022:"Pending orders count exceeded",10023:"Hedge operations prohibited",
        10024:"Prohibited operation",10025:"Invalid order state",10026:"Order state changed",
        10027:"AutoTrading DISABLED by client - enable in Terminal settings!",
        10028:"Trade context busy",10029:"Orders limit exceeded",10030:"Volume limit exceeded",
        10031:"Orders on symbol limit",10032:"Invalid expiration",10033:"Trade type invalid",
        10034:"Wrong color",10035:"Disabled by client",10036:"Permission denied",
        10038:"Order closed by system",10039:"Order closed by broker",10040:"Invalid take profit",
        10041:"Invalid stop loss",10042:"Invalid magic",10043:"Invalid expiration type",
        10044:"Too many requests",10047:"Order not found",10048:"Order status changed",
        10049:"Order info not available",10050:"Depth of market not available",
    }
    return error_map.get(retcode, f"Unknown error code {retcode}")


def check_autotrading_status():
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
    else:
        log("Cannot check AutoTrading status", "WARN")


def ensure_mt5_connection():
    global mt5_connected, mt5_connection_losses
    mt5_path = r"C:\MT5_2\terminal64.exe"
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
    account = mt5.account_info()
    if account:
        log(f"   Account: {account.login} | Balance: ${account.balance:.2f}", "DEBUG")
    check_autotrading_status()
    refresh_mt5_cache()


def check_mt5_health() -> bool:
    global mt5_connected, mt5_connection_losses, last_mt5_check
    last_mt5_check = time.time()
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        mt5_connected = False
        mt5_connection_losses += 1
        return False
    if mt5.account_info() is None:
        mt5_connected = False
        mt5_connection_losses += 1
        return False
    mt5_connected = True
    return True


def recover_mt5_connection() -> bool:
    log("Attempting MT5 reconnection...", "WARN")
    try:
        mt5.shutdown()
        time.sleep(2)
        init_ok = mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe")
        if init_ok:
            log("MT5 reconnection successful", "INFO")
            return True
        log("MT5 reconnection failed", "ERROR")
        return False
    except Exception as e:
        log(f"MT5 reconnection exception: {e}", "ERROR")
        return False


def get_account_balance() -> float:
    acc = get_cached_account_info()
    if acc is None:
        log("account_info() returned None", "ERROR")
        return 0.0
    return float(acc.balance)


def get_market_price(side: str) -> Optional[float]:
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log(f"symbol_info_tick({SYMBOL}) returned None", "ERROR")
        return None
    return float(tick.ask if side == "BUY" else tick.bid)


def calculate_lot_size(entry_price: float, sl_price: float, balance: float) -> float:
    price_distance = abs(entry_price - sl_price)
    if price_distance <= 0:
        return 0.01
    symbol_info = get_cached_symbol_info()
    if symbol_info is None:
        return 0.01
    contract_size = float(symbol_info.trade_contract_size)
    vol_min  = float(symbol_info.volume_min)
    vol_max  = float(symbol_info.volume_max)
    vol_step = float(symbol_info.volume_step)
    lot = (balance * RISK_PCT) / (price_distance * contract_size)
    lot = max(lot, vol_min)
    lot = min(lot, vol_max)
    lot = round(lot / vol_step) * vol_step
    return lot


def round_to_vol_step(lot: float) -> float:
    """Round a lot size to the valid volume step, respecting min/max."""
    symbol_info = get_cached_symbol_info()
    if symbol_info is None:
        return max(round(lot, 2), 0.01)
    vol_min  = float(symbol_info.volume_min)
    vol_max  = float(symbol_info.volume_max)
    vol_step = float(symbol_info.volume_step)
    lot = max(round(lot / vol_step) * vol_step, vol_min)
    lot = min(lot, vol_max)
    return lot


def get_filling_mode() -> int:
    global _cached_filling_mode
    if _cached_filling_mode is not None:
        return _cached_filling_mode
    symbol_info = get_cached_symbol_info()
    if symbol_info is None:
        return mt5.ORDER_FILLING_IOC
    filling = symbol_info.filling_mode
    if filling & 2 == 2:
        _cached_filling_mode = mt5.ORDER_FILLING_IOC
    elif filling & 1 == 1:
        _cached_filling_mode = mt5.ORDER_FILLING_FOK
    else:
        _cached_filling_mode = mt5.ORDER_FILLING_RETURN
    return _cached_filling_mode


def open_position(side: str, lot: float, sl: float = 0.0, tp: float = 0.0) -> Optional[int]:
    order_type   = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    filling_mode = get_filling_mode()
    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       lot,
        "type":         order_type,
        "type_filling": filling_mode,
        "magic":        MAGIC,
        "comment":      f"Signal {side}",
    }
    if sl > 0: request["sl"] = sl
    if tp > 0: request["tp"] = tp

    result = mt5.order_send(request)
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"Position opened - Ticket: {result.order}", "INFO")
        return result.order

    if result.retcode in {5, 10040, 10041} and (sl > 0 or tp > 0):
        request.pop("sl", None); request.pop("tp", None)
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            set_sl_and_tp(result.order, sl, tp)
            return result.order

    if result.retcode == 10027:
        log("AutoTrading DISABLED - cannot trade!", "ERROR")
        return None

    if result.retcode == 10015:
        for attempt in range(1, 4):
            delay = 5 * attempt
            time.sleep(delay)
            if not is_market_open(): continue
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                return result.order
            if result.retcode != 10015: break
        return None

    for attempt in range(1, 3):
        if result.retcode not in {10009, 10028, 10044, 9}: break
        time.sleep(0.15)
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            return result.order

    log(f"Order failed - Retcode: {result.retcode} | {result.comment}", "ERROR")
    return None


def set_stop_loss(ticket: int, sl_price: float) -> bool:
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"Position {ticket} not found", "ERROR")
        return False
    current_tp = float(pos[0].tp) if pos[0].tp > 0 else 0.0
    result = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": sl_price, "tp": current_tp})
    success = result.retcode == mt5.TRADE_RETCODE_DONE
    if not success:
        log(f"SL update FAILED - Retcode: {result.retcode}", "WARN")
    return success


def set_sl_and_tp(ticket: int, sl_price: float, tp_price: float) -> bool:
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"Position {ticket} not found", "ERROR")
        return False
    result = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": sl_price, "tp": tp_price})
    success = result.retcode == mt5.TRADE_RETCODE_DONE
    if not success:
        log(f"SL+TP update FAILED - Retcode: {result.retcode}", "WARN")
    return success


def set_take_profit(ticket: int, tp_price: float) -> bool:
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"Position {ticket} not found", "ERROR")
        return False
    current_sl = float(pos[0].sl) if pos[0].sl > 0 else 0.0
    result = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": current_sl, "tp": tp_price})
    success = result.retcode == mt5.TRADE_RETCODE_DONE
    if not success:
        log(f"TP update FAILED - Retcode: {result.retcode}", "WARN")
    return success


def close_position(ticket: int, side: str) -> bool:
    """Close an open position at market. Used for BE-close of worst entry when deep fills."""
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"close_position: ticket {ticket} not found", "WARN")
        return False
    close_type   = mt5.ORDER_TYPE_SELL if side == "BUY" else mt5.ORDER_TYPE_BUY
    filling_mode = get_filling_mode()
    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       float(pos[0].volume),
        "type":         close_type,
        "position":     ticket,
        "type_filling": filling_mode,
        "magic":        MAGIC,
        "comment":      "BE close worst entry",
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"Worst entry position {ticket} CLOSED at market (BE)", "INFO")
        return True
    log(f"close_position FAILED for {ticket}: {result.comment if result else 'None'}", "WARN")
    return False


# ===================== PARSING =====================

def parse_signal(text: str) -> Optional[Tuple[str, str]]:
    text = text.upper().strip()
    if "BUY NOW"  in text: return (SYMBOL, "BUY")
    if "SELL NOW" in text: return (SYMBOL, "SELL")
    return None


def parse_tp_levels(text: str) -> Dict[int, float]:
    levels = {}
    for m in _RE_TP_LEVELS.finditer(text):
        n, p = int(m.group(1)), float(m.group(2))
        if p >= 100:
            levels[n] = p
    if not levels:
        bm = _RE_TP_BARE.search(text)
        if bm:
            p = float(bm.group(1))
            if p >= 1000:
                levels[1] = p
    return levels


def parse_stop_loss(text: str) -> Optional[float]:
    m = _RE_STOP_LOSS.search(text)
    if m:
        v = float(m.group(1))
        if v >= 100: return v
    return None


def parse_tp1(text: str) -> Optional[float]:
    m = _RE_TP1.search(text)
    if m:
        v = float(m.group(1))
        if v >= 100: return v
    bm = _RE_TP_BARE.search(text)
    if bm:
        v = float(bm.group(1))
        if v >= 1000: return v
    return None


def select_tp3(all_tps: Dict[int, float]) -> Optional[float]:
    """Return TP3 if available, fall back to TP2, then TP1."""
    for level in (3, 2, 1):
        if level in all_tps:
            return all_tps[level]
    return None


def adjust_tp(tp_price: float, side: str) -> float:
    """Shift TP by TP_EARLY_EXIT_OFFSET toward entry so we exit slightly before the signal's TP.
    BUY:  TP lowered by offset (e.g. 5010 → 5009.5)
    SELL: TP raised  by offset (e.g. 5017 → 5017.5)
    """
    if side == "BUY":
        return tp_price - TP_EARLY_EXIT_OFFSET
    return tp_price + TP_EARLY_EXIT_OFFSET


def parse_entry_zone(text: str) -> Optional[Tuple[float, float]]:
    m = _RE_ENTRY_ZONE.search(text)
    if not m: return None
    v1, v2 = float(m.group(1)), float(m.group(2))
    if v1 < 1000 or v2 < 1000: return None
    zone_high = max(v1, v2)
    zone_low  = min(v1, v2)
    w = zone_high - zone_low
    if w > 30 or w < 0.5:
        log(f"Entry zone rejected: ${zone_low:.2f}-${zone_high:.2f} (width ${w:.2f})", "WARN")
        return None
    log(f"Parsed entry zone: ${zone_low:.2f} - ${zone_high:.2f}", "INFO")
    return (zone_low, zone_high)


def parse_trade_closure(text: str) -> Optional[str]:
    m = _RE_TRADE_CLOSURE.search(text)
    if m:
        return m.group(0).strip()
    return None


# ===================== LIMIT ORDER HELPERS =====================

def place_limit_order(side: str, price: float, lot: float, sl: float = 0.0, tp: float = 0.0) -> Optional[int]:
    current_price = get_market_price(side)
    if side == "BUY":
        order_type  = mt5.ORDER_TYPE_BUY_STOP  if (current_price and current_price < price) else mt5.ORDER_TYPE_BUY_LIMIT
        order_label = "BUY STOP"               if (current_price and current_price < price) else "BUY LIMIT"
    else:
        order_type  = mt5.ORDER_TYPE_SELL_STOP  if (current_price and current_price > price) else mt5.ORDER_TYPE_SELL_LIMIT
        order_label = "SELL STOP"               if (current_price and current_price > price) else "SELL LIMIT"

    log(f"Placing {order_label} at ${price:.2f} | Lot={lot:.4f} | SL=${sl:.2f} | TP=${tp:.2f}", "INFO")
    filling_mode = get_filling_mode()
    request = {
        "action":       mt5.TRADE_ACTION_PENDING,
        "symbol":       SYMBOL,
        "volume":       lot,
        "type":         order_type,
        "price":        price,
        "type_filling": filling_mode,
        "type_time":    mt5.ORDER_TIME_GTC,
        "magic":        MAGIC,
        "comment":      f"Zone {side}",
    }
    if sl > 0: request["sl"] = sl
    if tp > 0: request["tp"] = tp

    result = mt5.order_send(request)
    if result is None:
        log(f"Limit order failed - None result | {mt5.last_error()}", "ERROR")
        return None
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"Limit order placed - ticket: {result.order}", "INFO")
        return result.order

    if result.retcode in {5, 10040, 10041} and (sl > 0 or tp > 0):
        request.pop("sl", None); request.pop("tp", None)
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            return result.order

    if result.retcode == 10015:
        for attempt in range(1, 4):
            time.sleep(5 * attempt)
            if not is_market_open(): continue
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                return result.order
            if result and result.retcode != 10015: break

    log(f"Limit order failed - Retcode: {result.retcode} | {result.comment}", "ERROR")
    return None


def cancel_limit_order(order_ticket: int) -> bool:
    result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": order_ticket})
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"Limit order {order_ticket} canceled", "INFO")
        return True
    log(f"Failed to cancel order {order_ticket}: {result.comment if result else 'None'}", "WARN")
    return False


def modify_limit_order_sl_tp(order_ticket: int, sl: float = 0.0, tp: float = 0.0) -> bool:
    orders = mt5.orders_get(ticket=order_ticket)
    if not orders:
        log(f"Pending order {order_ticket} not found", "WARN")
        return False
    order = orders[0]
    request = {
        "action":    mt5.TRADE_ACTION_MODIFY,
        "order":     order_ticket,
        "price":     float(order.price_open),
        "sl":        sl if sl > 0 else float(order.sl),
        "tp":        tp if tp > 0 else float(order.tp),
        "type_time": mt5.ORDER_TIME_GTC,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"Pending order {order_ticket} modified: SL=${sl:.2f} TP=${tp:.2f}", "INFO")
        return True
    log(f"Failed to modify order {order_ticket}: {result.comment if result else 'None'}", "WARN")
    return False


def find_position_from_order(order_ticket: int) -> Optional[int]:
    now = datetime.now()
    deals = mt5.history_deals_get(now - timedelta(hours=24), now + timedelta(minutes=1))
    if deals:
        for d in deals:
            if d.order == order_ticket and d.entry == 0:
                return d.position_id
    # Fallback: if deal history missed it (timezone mismatch), check if a position
    # exists with the same ticket (MT5 assigns the order ticket to the position)
    pending = mt5.orders_get(ticket=order_ticket)
    if not pending:
        pos = mt5.positions_get(ticket=order_ticket)
        if pos:
            return int(pos[0].ticket)
    return None


def get_fill_price_from_order(order_ticket: int) -> Optional[float]:
    now = datetime.now()
    deals = mt5.history_deals_get(now - timedelta(hours=24), now + timedelta(minutes=1))
    if deals:
        for d in deals:
            if d.order == order_ticket and d.entry == 0:
                return float(d.price)
    # Fallback: check position's open price directly
    pos = mt5.positions_get(ticket=order_ticket)
    if pos:
        return float(pos[0].price_open)
    return None


# ===================== ENTRY STATS TRACKER =====================

STATS_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", f"entry_stats_{MAGIC}.json")
ERRORS_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", f"errors_{MAGIC}.json")


def _load_entry_stats() -> Dict:
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "total_signals": 0, "limit_orders_placed": 0, "limit_fills": 0,
        "limit_timeouts": 0, "limit_invalidated": 0, "signals_buffered": 0,
        "zones_received_from_edit": 0, "zone_wait_timeouts": 0,
        "market_orders_no_zone": 0, "market_orders_in_zone": 0, "market_orders_fallback": 0,
        "total_limit_slippage": 0.0, "zone_tracking_total": 0,
        "zone_best_entry_reached": 0, "zone_worst_entry_reached": 0, "entries": [],
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
    global _entry_stats
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    rec = {"time": ts, "type": event_type}
    rec.update(kwargs)
    counters = {
        "signal": "total_signals", "limit_placed": "limit_orders_placed",
        "limit_fill": "limit_fills", "limit_timeout": "limit_timeouts",
        "limit_invalidated": "limit_invalidated", "signal_buffered": "signals_buffered",
        "zone_from_edit": "zones_received_from_edit", "zone_wait_timeout": "zone_wait_timeouts",
        "market_no_zone": "market_orders_no_zone", "market_entry_in_zone": "market_orders_in_zone",
        "market_fallback": "market_orders_fallback",
    }
    key = counters.get(event_type)
    if key:
        _entry_stats.setdefault(key, 0)
        _entry_stats[key] += 1
    if event_type == "limit_fill":
        _entry_stats["total_limit_slippage"] += kwargs.get("slippage", 0.0)
    if "best_reached" in kwargs:
        _entry_stats.setdefault("zone_tracking_total", 0)
        _entry_stats["zone_tracking_total"] += 1
        if kwargs["best_reached"]:
            _entry_stats.setdefault("zone_best_entry_reached", 0)
            _entry_stats["zone_best_entry_reached"] += 1
        if kwargs.get("worst_reached"):
            _entry_stats.setdefault("zone_worst_entry_reached", 0)
            _entry_stats["zone_worst_entry_reached"] += 1
    _entry_stats["entries"].append(rec)
    _entry_stats["entries"] = _entry_stats["entries"][-500:]
    _save_entry_stats()


def extract_message_text(message) -> str:
    for attr in ("raw_text", "message", "text", "caption"):
        v = getattr(message, attr, None)
        if v: return v
    return ""


def log_signal_attempt(side: str, result: str, detail: str = ""):
    global trades_executed, trades_failed, messages_processed
    signal_log.append({"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "signal": side, "result": result, "detail": detail})
    if result == "SUCCESS":
        trades_executed += 1
        messages_processed += 1
    elif result == "FAILED":
        trades_failed += 1
        messages_processed += 1


def get_bot_metrics() -> Dict:
    uptime = time.time() - bot_start_time if bot_start_time > 0 else 0
    return {
        "uptime_hours": uptime / 3600,
        "uptime_seconds": uptime,
        "messages_received": messages_received,
        "messages_processed": messages_processed,
        "messages_ignored": messages_ignored,
        "trades_executed": trades_executed,
        "trades_failed": trades_failed,
        "mt5_connection_losses": mt5_connection_losses,
        "telegram_connection_losses": telegram_connection_losses,
        "seconds_since_last_message": time.time() - last_message_time if last_message_time > 0 else 0,
        "mt5_connected": mt5_connected,
        "telegram_connected": telegram_connected,
        "active_positions": len(position_map),
        "positions_with_real_tp": sum(1 for v in tp_sl_updated.values() if v),
        "buffered_signals": len(_buffered_signals),
        "pending_split_orders": len(_split_orders),
    }


def print_bot_status():
    m = get_bot_metrics()
    log(_LOG_SEP, "INFO")
    log("BOT HEALTH STATUS (SPLIT ENTRY / TP3)", "INFO")
    log(_LOG_SEP, "INFO")
    log(f"  Uptime: {m['uptime_hours']:.2f}h | Msgs: rcv={m['messages_received']} proc={m['messages_processed']} ign={m['messages_ignored']}", "INFO")
    log(f"  Trades: OK={m['trades_executed']} FAIL={m['trades_failed']}", "INFO")
    log(f"  MT5={('OK' if m['mt5_connected'] else 'DOWN')} | TG={('OK' if m['telegram_connected'] else 'DOWN')}", "INFO")
    log(f"  Active positions: {m['active_positions']} ({m['positions_with_real_tp']} with real TP)", "INFO")
    log(f"  Buffered signals: {m['buffered_signals']} | Split orders active: {m['pending_split_orders']}", "INFO")
    log(_LOG_SEP, "INFO")


def init_telegram():
    return TelegramClient(SESSION_FILE, API_ID, API_HASH)


async def reconnect_telegram(client, reason: str = "unknown") -> bool:
    global telegram_connected, last_telegram_activity, telegram_connection_losses
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        delay = min(5 * (2 ** (attempt - 1)), 120)
        log(f"  Reconnect attempt {attempt}/{max_attempts} ({delay}s delay)...", "INFO")
        try:
            if client.is_connected(): await client.disconnect()
        except Exception:
            pass
        await asyncio.sleep(delay)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.start()
            me = await asyncio.wait_for(client.get_me(), timeout=15)
            if me:
                telegram_connected = True
                last_telegram_activity = time.time()
                log(f"  Telegram reconnected on attempt {attempt}", "INFO")
                try: await client.get_dialogs()
                except Exception: pass
                await catch_up_messages(client, lookback_minutes=5)
                return True
        except Exception as e:
            log(f"  Reconnect attempt {attempt} failed: {e}", "ERROR")
            telegram_connected = False
    log(f"ALL {max_attempts} reconnection attempts failed", "ERROR")
    return False


def restart_bot():
    log(_LOG_SEP, "WARN")
    log("SELF-RESTART: Bot is restarting...", "WARN")
    log(_LOG_SEP, "WARN")
    try: mt5.shutdown()
    except Exception: pass
    time.sleep(2)
    os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])


async def catch_up_messages(client, lookback_minutes: int = 5):
    try:
        cutoff = time.time() - (lookback_minutes * 60)
        for chat_id in ALLOWED_CHAT_IDS:
            try:
                messages = await client.get_messages(chat_id, limit=20)
                for msg in reversed(messages):
                    if msg.date.timestamp() >= cutoff:
                        log(f"   Caught up message {msg.id}", "DEBUG")
            except Exception as e:
                log(f"Catch-up failed for {chat_id}: {e}", "WARN")
    except Exception as e:
        log(f"Catch-up error: {e}", "WARN")


async def replay_pending_signals(client):
    if not signal_queue: return
    pending = signal_queue.get_pending_signals()
    if not pending:
        log("No pending signals to replay", "INFO")
        return
    log(f"REPLAYING {len(pending)} PENDING SIGNALS FROM QUEUE", "INFO")


# ===================== SPLIT ENTRY EXECUTION =====================

def execute_split_entry_trade(
    side: str, msg_id: int,
    zone_low: float, zone_high: float,
    balance: float,
    signal_market_price: Optional[float] = None,
) -> Optional[Dict]:
    """
    Core entry function for the split-entry strategy.

    Returns a state dict (to be stored in _split_orders[msg_id]) on success, or None on failure.

    State dict schema:
        strategy:            "split_both_pending" | "split_market_and_pending" | "whole_lot" | "full_market"
        side:                str
        zone_low/zone_high:  float
        worst_entry_price:   float  (zone edge first hit by price = worst for this side)
        deep_entry_price:    float  (SPLIT_ENTRY_DEEP_OFFSET inside zone = better entry)
        half_lot / full_lot: float
        failsafe_sl:         float  (based on worst_entry)
        failsafe_tp:         float
        sl / tp1 / tp3:      float | None  (filled in when signal edit arrives)
        all_tps:             dict
        placed_at:           float (timestamp)
        signal_market_price: float | None

        -- split_both_pending --
        worst_order_ticket:  int | None
        deep_order_ticket:   int | None
        worst_pos_ticket:    int | None   (set when order fills)
        deep_pos_ticket:     int | None

        -- split_market_and_pending --
        market_pos_ticket:   int | None   (the immediately opened position)
        deep_order_ticket:   int | None
        deep_pos_ticket:     int | None

        -- whole_lot --
        order_ticket:        int | None
        pos_ticket:          int | None

        -- full_market --
        pos_ticket:          int | None   (whole lot entered immediately at market)

        -- BE tracking --
        worst_be_done:           bool
        deep_be_done:            bool
        only_worst_tp1_be_done:  bool

        -- price watermarks --
        price_min_seen: float
        price_max_seen: float
    """
    current_price = get_market_price(side)
    if current_price is None:
        log("Cannot get market price - aborting split entry", "ERROR")
        log_signal_attempt(side, "FAILED", "Cannot get market price")
        return None

    if not is_market_open():
        log("Market is CLOSED / CLOSE-ONLY - cannot enter", "ERROR")
        log_signal_attempt(side, "FAILED", "Market closed")
        return None

    if balance <= 0:
        log(f"Invalid balance: ${balance:.2f}", "ERROR")
        log_signal_attempt(side, "FAILED", f"Invalid balance: ${balance:.2f}")
        return None

    # ── Determine entry prices ──
    if side == "BUY":
        worst_entry = zone_high
        deep_entry  = zone_high - SPLIT_ENTRY_DEEP_OFFSET
        # Failsafe levels based on worst entry
        failsafe_sl = worst_entry - FAILSAFE_SL_DISTANCE
        failsafe_tp = worst_entry + FAILSAFE_TP_DISTANCE
    else:  # SELL
        worst_entry = zone_low
        deep_entry  = zone_low + SPLIT_ENTRY_DEEP_OFFSET
        failsafe_sl = worst_entry + FAILSAFE_SL_DISTANCE
        failsafe_tp = worst_entry - FAILSAFE_TP_DISTANCE

    # ── Lot sizes ──
    full_lot = calculate_lot_size(worst_entry, failsafe_sl, balance)
    if full_lot <= 0:
        log(f"Invalid lot size: {full_lot}", "ERROR")
        log_signal_attempt(side, "FAILED", f"Invalid lot: {full_lot}")
        return None
    half_lot = round_to_vol_step(full_lot / 2.0)

    log(f"Split entry: {side} | Zone: ${zone_low:.2f}-${zone_high:.2f}", "INFO")
    log(f"  Worst entry: ${worst_entry:.2f} | Deep entry: ${deep_entry:.2f}", "INFO")
    log(f"  Full lot: {full_lot:.4f} | Half lot: {half_lot:.4f}", "INFO")
    log(f"  Failsafe SL: ${failsafe_sl:.2f} | Failsafe TP: ${failsafe_tp:.2f}", "INFO")
    log(f"  Current price: ${current_price:.2f}", "INFO")

    base_state = {
        "side": side,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "worst_entry_price": worst_entry,
        "deep_entry_price": deep_entry,
        "full_lot": full_lot,
        "half_lot": half_lot,
        "failsafe_sl": failsafe_sl,
        "failsafe_tp": failsafe_tp,
        "sl": None, "tp1": None, "tp3": None, "all_tps": {},
        "placed_at": time.time(),
        "signal_market_price": signal_market_price,
        "worst_be_done": False,
        "deep_be_done": False,
        "only_worst_tp1_be_done": False,
        "price_min_seen": float('inf'),
        "price_max_seen": float('-inf'),
    }

    # ════════════════════════════════════════════════
    # CASE 1: Price past zone toward TP → two pending limit orders
    # ════════════════════════════════════════════════
    price_past_zone = (side == "BUY" and current_price > zone_high) or \
                      (side == "SELL" and current_price < zone_low)

    if price_past_zone:
        log(f"Case 1: price ${current_price:.2f} past zone toward TP → two pending limits", "INFO")

        worst_ticket = place_limit_order(side, worst_entry, half_lot, failsafe_sl, failsafe_tp)
        deep_ticket  = place_limit_order(side, deep_entry,  half_lot, failsafe_sl, failsafe_tp)

        if not worst_ticket and not deep_ticket:
            log("Both limit orders failed", "ERROR")
            log_signal_attempt(side, "FAILED", "Both limit orders rejected")
            return None

        log(_LOG_SEP, "INFO")
        log(f"SPLIT ENTRY (Case 1) — TWO PENDING LIMITS", "INFO")
        log(f"   Worst: order {worst_ticket} at ${worst_entry:.2f} | lot {half_lot:.4f}", "INFO")
        log(f"   Deep:  order {deep_ticket}  at ${deep_entry:.2f} | lot {half_lot:.4f}", "INFO")
        log(_LOG_SEP, "INFO")
        log_signal_attempt(side, "LIMIT_PLACED", f"worst={worst_ticket}@${worst_entry:.2f} deep={deep_ticket}@${deep_entry:.2f}")

        return {**base_state,
                "strategy": "split_both_pending",
                "worst_order_ticket": worst_ticket,
                "deep_order_ticket":  deep_ticket,
                "worst_pos_ticket":   None,
                "deep_pos_ticket":    None}

    # ════════════════════════════════════════════════
    # CASE 2: Price inside zone, in shallow area (above deep_entry for BUY)
    # → market half + pending half at deep entry
    # ════════════════════════════════════════════════
    price_in_zone = zone_low <= current_price <= zone_high

    if price_in_zone:
        # Check if price is already at/past the deep entry area
        at_deep_area = (side == "BUY"  and current_price <= deep_entry) or \
                       (side == "SELL" and current_price >= deep_entry)

        if at_deep_area:
            # Price is already deep in zone — enter full lot at market
            log(f"Case 2b: price ${current_price:.2f} at/past deep entry ${deep_entry:.2f} → full market entry", "INFO")
            ticket = open_position(side, full_lot, failsafe_sl, failsafe_tp)
            if not ticket:
                log("Full market entry failed", "ERROR")
                log_signal_attempt(side, "FAILED", "Full market entry rejected")
                return None
            log(_LOG_SEP, "INFO")
            log(f"SPLIT ENTRY (Case 2b) — FULL MARKET (deep area)", "INFO")
            log(f"   Ticket: {ticket} | {side} at ~${current_price:.2f} | lot {full_lot:.4f}", "INFO")
            log(_LOG_SEP, "INFO")
            log_signal_attempt(side, "SUCCESS", f"ticket={ticket} full market deep area")
            position_map[msg_id] = ticket
            entry_prices[ticket] = current_price
            tp_sl_updated[ticket] = False
            return {**base_state,
                    "strategy": "full_market",
                    "pos_ticket": ticket}

        else:
            # Price in shallow part of zone → half market + half limit at deep entry
            log(f"Case 2: price ${current_price:.2f} in zone, above deep ${deep_entry:.2f} → half market + half limit", "INFO")

            market_ticket = open_position(side, half_lot, failsafe_sl, failsafe_tp)
            if not market_ticket:
                log("Market half-lot entry failed", "ERROR")
                log_signal_attempt(side, "FAILED", "Market half-lot rejected")
                return None

            deep_ticket = place_limit_order(side, deep_entry, half_lot, failsafe_sl, failsafe_tp)

            log(_LOG_SEP, "INFO")
            log(f"SPLIT ENTRY (Case 2) — HALF MARKET + HALF PENDING", "INFO")
            log(f"   Market: ticket {market_ticket} at ~${current_price:.2f} | lot {half_lot:.4f}", "INFO")
            log(f"   Deep:   order  {deep_ticket}  at ${deep_entry:.2f} | lot {half_lot:.4f}", "INFO")
            log(_LOG_SEP, "INFO")
            log_signal_attempt(side, "SUCCESS", f"market={market_ticket} deep_order={deep_ticket}")

            position_map[msg_id] = market_ticket
            entry_prices[market_ticket] = current_price
            tp_sl_updated[market_ticket] = False

            return {**base_state,
                    "strategy": "split_market_and_pending",
                    "market_pos_ticket": market_ticket,
                    "deep_order_ticket":  deep_ticket,
                    "deep_pos_ticket":    None,
                    "market_entry_price": current_price}  # actual fill price

    # ════════════════════════════════════════════════
    # CASE 3: Price on wrong side of zone (hasn't reached zone yet)
    # → full lot STOP at best entry
    # ════════════════════════════════════════════════
    price_before_zone = (side == "BUY"  and current_price < zone_low) or \
                        (side == "SELL" and current_price > zone_high)

    if price_before_zone:
        best_entry = zone_low if side == "BUY" else zone_high
        if side == "BUY":
            fsl_best = best_entry - FAILSAFE_SL_DISTANCE
            ftp_best = best_entry + FAILSAFE_TP_DISTANCE
        else:
            fsl_best = best_entry + FAILSAFE_SL_DISTANCE
            ftp_best = best_entry - FAILSAFE_TP_DISTANCE

        full_lot_best = calculate_lot_size(best_entry, fsl_best, balance)
        if full_lot_best <= 0:
            log(f"Invalid lot for best entry: {full_lot_best}", "ERROR")
            log_signal_attempt(side, "FAILED", f"Invalid lot for best entry")
            return None

        log(f"Case 3: price ${current_price:.2f} before zone → full lot STOP at best entry ${best_entry:.2f}", "INFO")
        order_ticket = place_limit_order(side, best_entry, full_lot_best, fsl_best, ftp_best)
        if not order_ticket:
            log("Whole lot stop order failed", "ERROR")
            log_signal_attempt(side, "FAILED", "Whole lot stop order rejected")
            return None

        log(_LOG_SEP, "INFO")
        log(f"SPLIT ENTRY (Case 3) — WHOLE LOT STOP AT BEST ENTRY", "INFO")
        log(f"   Order: {order_ticket} | {side} stop at ${best_entry:.2f} | lot {full_lot_best:.4f}", "INFO")
        log(_LOG_SEP, "INFO")
        log_signal_attempt(side, "LIMIT_PLACED", f"order={order_ticket} best_entry=${best_entry:.2f}")

        return {**base_state,
                "strategy": "whole_lot",
                "order_ticket": order_ticket,
                "pos_ticket": None,
                "full_lot": full_lot_best,
                "failsafe_sl": fsl_best,
                "failsafe_tp": ftp_best,
                "worst_entry_price": best_entry}  # for tracking purposes

    log(f"Could not classify price ${current_price:.2f} vs zone ${zone_low:.2f}-${zone_high:.2f}", "ERROR")
    return None


# ===================== MAIN =====================

async def main():
    global signal_queue, session_manager, reconnect_monitor, outcome_tracker, signal_records
    global bot_start_time, telegram_connected, last_telegram_activity, _telegram_client
    global messages_received, messages_ignored, last_message_time, telegram_connection_losses

    _load_errors_from_json()
    bot_start_time = time.time()
    start_timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    signal_queue      = SignalQueue("signal_queue.json")
    session_manager   = SessionManager("trading_bot_session_split_entry", "sessions")
    reconnect_monitor = ReconnectMonitor(alert_threshold=5, time_window_minutes=30)
    outcome_tracker   = TradeOutcomeTracker(magic=MAGIC)
    _data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
    signal_records    = SignalRecordsManager(_data_dir, MAGIC)

    client = init_telegram()
    _telegram_client = client

    ensure_mt5_connection()

    log(_LOG_SEP, "INFO")
    log("BOT INITIALIZATION COMPLETE — SPLIT ENTRY / TP3 STRATEGY", "INFO")
    log(_LOG_SEP, "INFO")
    log(f"  Start: {start_timestamp}", "INFO")
    log(f"  MAGIC: {MAGIC}  |  Symbol: {SYMBOL}  |  Risk: {RISK_PCT*100:.1f}%", "INFO")
    log(f"  Failsafe SL: ${FAILSAFE_SL_DISTANCE:.2f}  |  Failsafe TP: ${FAILSAFE_TP_DISTANCE:.2f}", "INFO")
    log(f"  Deep entry offset: ${SPLIT_ENTRY_DEEP_OFFSET:.2f}  |  BE buffer: ${SPLIT_ENTRY_BE_BUFFER:.2f}", "INFO")
    log(f"  Zone wait timeout: {ZONE_WAIT_TIMEOUT}s  |  Limit order timeout: {LIMIT_ORDER_TIMEOUT}s", "INFO")
    log(_LOG_SEP, "INFO")

    # ── Helper: apply real SL + TP3 to a position ──
    async def update_position_sl_tp(ticket: int, sl: float = None, all_tps: Dict[int, float] = None, source: str = ""):
        """Apply real SL and TP3 (fallback TP2/TP1) to a position."""
        pos = await run_mt5(lambda: mt5.positions_get(ticket=ticket))
        if not pos:
            log(f"Position {ticket} not found - may have closed already", "WARN")
            return

        current_sl = float(pos[0].sl) if pos[0].sl > 0 else 0.0
        current_tp = float(pos[0].tp) if pos[0].tp > 0 else 0.0
        side       = "BUY" if pos[0].type == 0 else "SELL"
        entry      = entry_prices.get(ticket, float(pos[0].price_open))

        new_sl = current_sl
        new_tp = current_tp

        if sl:
            valid_sl = (side == "BUY" and sl < entry) or (side == "SELL" and sl > entry)
            if valid_sl:
                new_sl = sl
            else:
                log(f"SL ${sl:.5f} invalid for {side} at ${entry:.5f} — ignoring", "WARN")

        if all_tps:
            # Only set TP if TP3 is explicitly present — no fallback to TP1/TP2.
            # Interim messages like "TP1 5081.50 SL 5070" must not overwrite TP.
            tp3 = all_tps.get(3)
            if tp3:
                adjusted = adjust_tp(tp3, side)
                valid_tp = (side == "BUY" and adjusted > entry) or (side == "SELL" and adjusted < entry)
                if valid_tp:
                    new_tp = adjusted
                else:
                    log(f"TP3 ${tp3:.5f} (adjusted ${adjusted:.5f}) invalid for {side} at ${entry:.5f} — ignoring", "WARN")
            else:
                log(f"No TP3 in update — keeping current TP ${current_tp:.5f}", "DEBUG")

        if abs(new_sl - current_sl) < 0.001 and abs(new_tp - current_tp) < 0.001:
            log(f"No SL/TP changes needed for ticket {ticket}", "DEBUG")
            return

        log(_LOG_SEP, "INFO")
        log(f"UPDATING SL+TP3 on ticket {ticket}{source}", "INFO")
        log(f"   Old: SL=${current_sl:.5f} | TP=${current_tp:.5f}", "INFO")
        log(f"   New: SL=${new_sl:.5f} | TP={new_tp:.5f}", "INFO")
        log(_LOG_SEP, "INFO")

        await run_mt5(lambda: set_sl_and_tp(ticket, new_sl, new_tp))
        tp_sl_updated[ticket] = True

        if outcome_tracker and outcome_tracker.is_tracking(ticket):
            outcome_tracker.update_levels(ticket, sl_price=sl, tp_levels=all_tps if all_tps else None)

        # Persist TP levels into signal_records so tp_levels is never left empty
        if signal_records and all_tps:
            mid = _ticket_to_msg_id.get(ticket)
            if mid:
                signal_records.update_tp_sl(mid, all_tps, sl or 0)

    # ── Apply SL+TP3 to ALL active positions for this msg_id ──
    async def apply_real_levels_to_split(msg_id: int, sl: Optional[float], all_tps: Dict[int, float], source: str = ""):
        """Update SL and TP3 on every live position associated with a split-entry signal."""
        info = _split_orders.get(msg_id)
        if not info:
            return

        strategy = info["strategy"]
        tickets_to_update = []

        if strategy == "split_both_pending":
            if info.get("worst_pos_ticket"):
                tickets_to_update.append(info["worst_pos_ticket"])
            if info.get("deep_pos_ticket"):
                tickets_to_update.append(info["deep_pos_ticket"])
        elif strategy == "split_market_and_pending":
            if info.get("market_pos_ticket"):
                tickets_to_update.append(info["market_pos_ticket"])
            if info.get("deep_pos_ticket"):
                tickets_to_update.append(info["deep_pos_ticket"])
        elif strategy in ("whole_lot", "full_market"):
            pos_t = info.get("pos_ticket")
            if pos_t:
                tickets_to_update.append(pos_t)

        # Also update pending orders SL/TP3 if still pending
        tp3 = select_tp3(all_tps) if all_tps else None
        side_for_tp = info["side"]
        tp3_adjusted = adjust_tp(tp3, side_for_tp) if tp3 else None

        async def _modify_or_detect_fill(order_ticket: int, pos_key: str):
            """Try to modify pending order; if gone, detect fill and track the position."""
            ok = await run_mt5(lambda _ot=order_ticket, _sl=sl or 0, _tp=tp3_adjusted or 0:
                               modify_limit_order_sl_tp(_ot, _sl, _tp))
            if not ok and not info.get(pos_key):
                filled = await run_mt5(lambda _ot=order_ticket: find_position_from_order(_ot))
                if filled:
                    fill_px = await run_mt5(lambda _ot=order_ticket: get_fill_price_from_order(_ot))
                    info[pos_key] = filled
                    entry_prices[filled] = fill_px or 0
                    tp_sl_updated[filled] = False
                    log(f"Pending order {order_ticket} already filled → position {filled} @ ${(fill_px if fill_px else 0):.2f}", "INFO")
                    tickets_to_update.append(filled)

        if strategy == "split_both_pending":
            for key, pos_key in (("worst_order_ticket", "worst_pos_ticket"),
                                 ("deep_order_ticket", "deep_pos_ticket")):
                ot = info.get(key)
                if ot:
                    await _modify_or_detect_fill(ot, pos_key)
        elif strategy == "split_market_and_pending":
            ot = info.get("deep_order_ticket")
            if ot:
                await _modify_or_detect_fill(ot, "deep_pos_ticket")
        elif strategy == "whole_lot":
            ot = info.get("order_ticket")
            if ot:
                await _modify_or_detect_fill(ot, "pos_ticket")

        for ticket in tickets_to_update:
            await update_position_sl_tp(ticket, sl, all_tps, source)

        # Cache for later (fill detection will also call update)
        if sl:    info["sl"]   = sl
        if tp3:   info["tp3"]  = tp3
        if all_tps: info["all_tps"] = all_tps
        if all_tps and 1 in all_tps:
            info["tp1"] = all_tps[1]

    # ── Process new signal message ──
    async def process_message(text: str, msg_id: int, channel_name: str, source: str = ""):
        global messages_ignored

        signal = parse_signal(text)
        if signal:
            symbol, side = signal
            log(f"SIGNAL DETECTED{source}: {side} {symbol}", "INFO")
            log_signal_attempt(side, "RECEIVED", f"msg_id={msg_id}{source}")

            signal_market_price = await run_mt5(lambda: get_market_price(side))

            if signal_records:
                signal_records.register_signal(msg_id, side, signal_market_price or 0.0)

            zone = parse_entry_zone(text)
            if zone:
                zone_low, zone_high = zone
                sl_parsed  = parse_stop_loss(text)
                all_tps    = parse_tp_levels(text)
                tp3        = select_tp3(all_tps)

                balance = await run_mt5(get_account_balance)
                state = await run_mt5(lambda: execute_split_entry_trade(
                    side, msg_id, zone_low, zone_high, balance, signal_market_price))

                if state:
                    _split_orders[msg_id] = state
                    state["sl"]      = sl_parsed
                    state["all_tps"] = all_tps
                    state["tp3"]     = tp3
                    if all_tps and 1 in all_tps:
                        state["tp1"] = all_tps[1]

                    strat = state.get("strategy")
                    if signal_records:
                        signal_records.set_zone(msg_id, zone_low, zone_high, all_tps, sl_parsed or state["failsafe_sl"])

                    if strat in ("full_market", "split_market_and_pending"):
                        m_ticket = state.get("pos_ticket") or state.get("market_pos_ticket")
                        m_price  = state.get("market_entry_price") or entry_prices.get(m_ticket) or signal_market_price or zone_low
                        if outcome_tracker and m_ticket:
                            outcome_tracker.register_trade(m_ticket, side, m_price, sl_parsed or state["failsafe_sl"], all_tps or {})
                        if signal_records and m_ticket:
                            signal_records.record_bot_entry(msg_id, entered=True, entry_type="market", entry_price=m_price, ticket=m_ticket)
                            signal_records.record_entry_event(msg_id, "market_entry_in_zone", side=side, entry_price=m_price, zone_low=zone_low, zone_high=zone_high)
                            _ticket_to_msg_id[m_ticket] = msg_id
                        record_entry_stat("market_entry_in_zone", side=side, entry_price=m_price, zone_low=zone_low, zone_high=zone_high)
                        if strat == "split_market_and_pending":
                            if signal_records:
                                signal_records.record_entry_event(msg_id, "limit_placed", side=side, zone_low=zone_low, zone_high=zone_high)
                            record_entry_stat("limit_placed", side=side, zone_low=zone_low, zone_high=zone_high)
                    else:
                        if signal_records:
                            signal_records.record_entry_event(msg_id, "limit_placed", side=side, zone_low=zone_low, zone_high=zone_high)
                        record_entry_stat("limit_placed", side=side, zone_low=zone_low, zone_high=zone_high)

                    # If immediate positions opened, register with outcome tracker and apply real levels
                    if sl_parsed or tp3:
                        await apply_real_levels_to_split(msg_id, sl_parsed, all_tps, source)
                return True
            else:
                # No zone yet → buffer
                _buffered_signals[msg_id] = {
                    "side": side, "symbol": symbol, "text": text, "msg_id": msg_id,
                    "channel_name": channel_name, "buffered_at": time.time(),
                    "signal_market_price": signal_market_price,
                }
                if signal_records:
                    signal_records.record_entry_event(msg_id, "signal_buffered", side=side)
                record_entry_stat("signal_buffered", side=side)
                log(f"SIGNAL BUFFERED — waiting for zone edit (timeout={ZONE_WAIT_TIMEOUT}s)", "INFO")
                return True

        # Trade closure
        closure = parse_trade_closure(text)
        if closure:
            is_sl = "SL" in closure.upper()
            live  = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
            has_open = bool(live and [p for p in live if p.magic == MAGIC])

            if is_sl or has_open or _split_orders:
                for mid, info in list(_split_orders.items()):
                    side_c = info["side"]
                    for key in ("worst_order_ticket", "deep_order_ticket", "order_ticket"):
                        ot = info.get(key)
                        if ot:
                            log(f"CLOSURE '{closure}' → cancelling order {ot}", "INFO")
                            await run_mt5(lambda _ot=ot: cancel_limit_order(_ot))
                    # Register virtual trade for any orders that never filled
                    no_fill = not info.get("worst_pos_ticket") and not info.get("deep_pos_ticket") and not info.get("pos_ticket")
                    if no_fill and outcome_tracker and info.get("all_tps"):
                        vt_id    = -abs(mid)
                        vt_sl    = info.get("sl") or info.get("failsafe_sl") or 0
                        vt_entry = info.get("signal_market_price") or info.get("worst_entry_price") or 0
                        if vt_entry:
                            outcome_tracker.register_virtual_trade(vt_id, side_c, vt_entry, vt_sl, info["all_tps"],
                                                                   cancel_reason=f"channel_closure:{closure}")
                            log(f"VIRTUAL TRADE registered: id={vt_id} side={side_c} entry=${vt_entry:.2f}", "INFO")
                    # Only finalize signal_records if the outcome tracker is NOT
                    # still shadow-tracking tickets for this signal.  Otherwise the
                    # shadow tracker's finalization path will call finalize() once
                    # the highest TP (or SL / timeout) is reached.
                    if signal_records:
                        tickets_for_mid = [t for t, m in _ticket_to_msg_id.items() if m == mid]
                        still_tracking = outcome_tracker and any(
                            outcome_tracker.is_tracking(t) for t in tickets_for_mid
                        )
                        if not still_tracking:
                            log(f"CLOSURE '{closure}': finalizing signal_records for msg_id={mid} "
                                f"(tickets_for_mid={tickets_for_mid}, no active tracking)", "INFO")
                            signal_records.finalize(mid)
                        else:
                            log(f"CLOSURE '{closure}': deferring signal_records finalize for msg_id={mid} "
                                f"(outcome tracker still shadow-tracking, tickets={tickets_for_mid})", "INFO")
                _split_orders.clear()

            for mid, buf in list(_buffered_signals.items()):
                log(f"CLOSURE '{closure}' → clearing buffered signal (msg_id={mid})", "INFO")
                if outcome_tracker:
                    buf_tps  = parse_tp_levels(buf.get("text", ""))
                    buf_sl   = parse_stop_loss(buf.get("text", ""))
                    vt_entry = buf.get("signal_market_price") or 0
                    if buf_tps and vt_entry:
                        vt_id = -abs(mid)
                        outcome_tracker.register_virtual_trade(vt_id, buf["side"], vt_entry, buf_sl or 0, buf_tps,
                                                               cancel_reason=f"channel_closure:{closure}")
                        log(f"VIRTUAL TRADE registered: id={vt_id} side={buf['side']} entry=${vt_entry:.2f}", "INFO")
            _buffered_signals.clear()
            return True

        # SL/TP update for existing positions
        sl_parsed    = parse_stop_loss(text)
        all_tps      = parse_tp_levels(text)
        tp3          = select_tp3(all_tps) if all_tps else None
        tp1_fallback = all_tps.get(1) if all_tps else None

        if sl_parsed or tp3:
            positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
            bot_positions = [p for p in positions if p.magic == MAGIC] if positions else []

            if bot_positions:
                for pos in bot_positions:
                    ticket = int(pos.ticket)
                    await update_position_sl_tp(ticket, sl_parsed, all_tps, source)
            elif _split_orders:
                # No filled positions but pending split orders exist — apply SL/TP to them
                for mid, info in _split_orders.items():
                    log(f"SL/TP update → applying to split order (msg_id={mid}){source}", "INFO")
                    await apply_real_levels_to_split(mid, sl_parsed, all_tps, source)
                    # Update signal_records with the TP levels
                    if signal_records:
                        signal_records.update_tp_sl(mid, all_tps or {}, sl_parsed or 0)
            else:
                log(f"SL/TP update received but no open positions or split orders{source}", "WARN")
            return True

        return False

    # ══════════════════════════════════════════════════════════════════════
    # Message handlers
    # ══════════════════════════════════════════════════════════════════════

    @client.on(events.NewMessage())
    async def on_new_message(event):
        global messages_received, messages_ignored, last_message_time, last_telegram_activity
        try:
            if event.message.id in _processed_msg_ids:
                return
            _processed_msg_ids.add(event.message.id)
            if len(_processed_msg_ids) > _MAX_PROCESSED_IDS:
                s = sorted(_processed_msg_ids)
                _processed_msg_ids.difference_update(s[:len(s)//2])

            messages_received += 1
            if event.chat_id not in ALLOWED_CHAT_IDS:
                messages_ignored += 1
                return

            last_telegram_activity = time.time()
            last_message_time      = time.time()

            text         = extract_message_text(event.message).strip()
            msg_id       = event.message.id
            channel_name = "[MAIN]"

            log(_LOG_SEP, "INFO")
            log(f"{channel_name} NEW MESSAGE | id={msg_id}", "INFO")
            log(f"   Preview: {text[:150]}", "INFO")
            log(_LOG_SEP, "INFO")

            handled = await process_message(text, msg_id, channel_name)
            if not handled:
                messages_ignored += 1

        except Exception as e:
            log(f"CRITICAL in on_new_message: {e}", "ERROR")
            import traceback; log(traceback.format_exc(), "ERROR")

    @client.on(events.MessageEdited())
    async def on_edit(event):
        global messages_received, messages_ignored, last_telegram_activity
        try:
            last_telegram_activity = time.time()
            messages_received += 1

            if event.chat_id not in ALLOWED_CHAT_IDS:
                messages_ignored += 1
                return

            text         = extract_message_text(event.message).strip()
            msg_id       = event.message.id
            channel_name = "[MAIN]"

            log(_LOG_SEP, "INFO")
            log(f"{channel_name} MESSAGE EDITED | id={msg_id}", "INFO")
            log(f"   Preview: {text[:200]}", "INFO")
            log(_LOG_SEP, "INFO")

            # ── 1. Buffered signal waiting for zone ──
            if msg_id in _buffered_signals:
                buf  = _buffered_signals[msg_id]
                side = buf["side"]
                zone = parse_entry_zone(text)
                if zone:
                    zone_low, zone_high = zone
                    sl_parsed = parse_stop_loss(text)
                    all_tps   = parse_tp_levels(text)
                    tp3       = select_tp3(all_tps)

                    balance = await run_mt5(get_account_balance)
                    state = await run_mt5(lambda: execute_split_entry_trade(
                        side, msg_id, zone_low, zone_high, balance,
                        buf.get("signal_market_price")))

                    if state:
                        _split_orders[msg_id] = state
                        state["sl"]      = sl_parsed
                        state["all_tps"] = all_tps
                        state["tp3"]     = tp3
                        if all_tps and 1 in all_tps:
                            state["tp1"] = all_tps[1]

                        strat = state.get("strategy")
                        if signal_records:
                            wait_time = time.time() - buf["buffered_at"]
                            signal_records.set_zone(msg_id, zone_low, zone_high, all_tps, sl_parsed or state["failsafe_sl"])
                            signal_records.record_entry_event(msg_id, "zone_from_edit", side=side, zone_low=zone_low, zone_high=zone_high, wait_time=wait_time)
                        record_entry_stat("zone_from_edit", side=side, zone_low=zone_low, zone_high=zone_high)

                        signal_market_price = buf.get("signal_market_price")
                        if strat in ("full_market", "split_market_and_pending"):
                            m_ticket = state.get("pos_ticket") or state.get("market_pos_ticket")
                            m_price  = state.get("market_entry_price") or entry_prices.get(m_ticket) or signal_market_price or zone_low
                            if outcome_tracker and m_ticket:
                                outcome_tracker.register_trade(m_ticket, side, m_price, sl_parsed or state["failsafe_sl"], all_tps or {})
                            if signal_records and m_ticket:
                                signal_records.record_bot_entry(msg_id, entered=True, entry_type="market", entry_price=m_price, ticket=m_ticket)
                                signal_records.record_entry_event(msg_id, "market_entry_in_zone", side=side, entry_price=m_price, zone_low=zone_low, zone_high=zone_high)
                                _ticket_to_msg_id[m_ticket] = msg_id
                            record_entry_stat("market_entry_in_zone", side=side, entry_price=m_price, zone_low=zone_low, zone_high=zone_high)
                            if strat == "split_market_and_pending":
                                if signal_records:
                                    signal_records.record_entry_event(msg_id, "limit_placed", side=side, zone_low=zone_low, zone_high=zone_high)
                                record_entry_stat("limit_placed", side=side, zone_low=zone_low, zone_high=zone_high)
                        else:
                            if signal_records:
                                signal_records.record_entry_event(msg_id, "limit_placed", side=side, zone_low=zone_low, zone_high=zone_high)
                            record_entry_stat("limit_placed", side=side, zone_low=zone_low, zone_high=zone_high)

                        if sl_parsed or tp3:
                            await apply_real_levels_to_split(msg_id, sl_parsed, all_tps, " (EDIT-ZONE)")

                    del _buffered_signals[msg_id]
                    return
                else:
                    # No zone yet — update SL/TP cache in buffer
                    sl = parse_stop_loss(text)
                    if sl: buf["sl"] = sl
                    tp_lvls = parse_tp_levels(text)
                    if tp_lvls: buf["text"] = text
                    log(f"Edit for buffered signal — still no zone (SL={'yes' if sl else 'no'})", "INFO")
                    return

            # ── 2. Existing split order ──
            if msg_id in _split_orders:
                sl_parsed = parse_stop_loss(text)
                all_tps   = parse_tp_levels(text)
                if sl_parsed or all_tps:
                    log(f"SPLIT ORDER EDIT (msg_id={msg_id}): applying SL/TP3", "INFO")
                    await apply_real_levels_to_split(msg_id, sl_parsed, all_tps, " (EDIT-SPLIT)")
                return

            # ── 3. Position map (legacy fallback) ──
            sl_parsed    = parse_stop_loss(text)
            all_tps      = parse_tp_levels(text)
            if not sl_parsed and not all_tps:
                return

            ticket = None
            if msg_id in position_map:
                ticket = position_map[msg_id]
            else:
                positions = await run_mt5(lambda: mt5.positions_get(symbol=SYMBOL))
                if positions:
                    bot_pos = [p for p in positions if p.magic == MAGIC]
                    if bot_pos:
                        ticket = int(bot_pos[-1].ticket)

            if ticket:
                await update_position_sl_tp(ticket, sl_parsed, all_tps, " (EDIT)")

        except Exception as e:
            log(f"CRITICAL in on_edit: {e}", "ERROR")
            import traceback; log(traceback.format_exc(), "ERROR")

    # ══════════════════════════════════════════════════════════════════════
    # Split entry monitor (replaces limit_order_monitor)
    # ══════════════════════════════════════════════════════════════════════
    async def split_entry_monitor():
        """
        Every 1 second:
        - Check buffered signals for zone-wait timeout
        - Check all _split_orders for:
            * Order fills → position tracking
            * BE trigger logic (both filled → price back to worst entry)
            * Only-worst filled → TP1 BE logic
            * Timeout / price invalidation
        """
        log("Split entry monitor started (1s interval)", "INFO")
        while True:
            try:
                await asyncio.sleep(1)
                now = time.time()

                # ── Zone-wait timeout ──
                expired = [mid for mid, b in _buffered_signals.items()
                           if now - b["buffered_at"] >= ZONE_WAIT_TIMEOUT]
                for mid in expired:
                    buf = _buffered_signals.pop(mid)
                    side_buf = buf["side"]
                    log(f"ZONE WAIT TIMEOUT: msg_id={mid} — no zone received, cancelling", "WARN")
                    record_entry_stat("zone_wait_timeout", side=side_buf)
                    if signal_records:
                        signal_records.record_bot_entry(mid, entered=False, entry_type="zone_wait_timeout")
                        signal_records.finalize(mid)
                    if outcome_tracker:
                        buf_tps = parse_tp_levels(buf.get("text", ""))
                        buf_sl  = parse_stop_loss(buf.get("text", ""))
                        vt_entry = buf.get("signal_market_price") or 0
                        if buf_tps and vt_entry:
                            vt_id = -abs(mid)
                            outcome_tracker.register_virtual_trade(vt_id, side_buf, vt_entry, buf_sl or 0, buf_tps,
                                                                   cancel_reason="zone_wait_timeout")
                            log(f"VIRTUAL TRADE registered: id={vt_id} side={side_buf} entry=${vt_entry:.2f}", "INFO")

                # ── Split order monitoring ──
                completed = []
                for mid, info in list(_split_orders.items()):
                    side            = info["side"]
                    strategy        = info["strategy"]
                    elapsed         = now - info["placed_at"]
                    zone_low        = info["zone_low"]
                    zone_high       = info["zone_high"]
                    worst_entry     = info["worst_entry_price"]
                    deep_entry      = info.get("deep_entry_price", worst_entry)
                    failsafe_sl     = info["failsafe_sl"]
                    failsafe_tp     = info["failsafe_tp"]

                    current_price_raw = await run_mt5(lambda: get_market_price(side))
                    if current_price_raw:
                        info["price_min_seen"] = min(info.get("price_min_seen", float('inf')),  current_price_raw)
                        info["price_max_seen"] = max(info.get("price_max_seen", float('-inf')), current_price_raw)
                    cp = current_price_raw

                    # ────────────────────────────────────────
                    # STRATEGY: split_both_pending
                    # Two pending limit orders for worst and deep entries
                    # ────────────────────────────────────────
                    if strategy == "split_both_pending":
                        worst_ot = info.get("worst_order_ticket")
                        deep_ot  = info.get("deep_order_ticket")

                        # Check fills for each pending order
                        for key_ot, key_pos in [("worst_order_ticket", "worst_pos_ticket"),
                                                 ("deep_order_ticket",  "deep_pos_ticket")]:
                            ot  = info.get(key_ot)
                            pos = info.get(key_pos)
                            if ot and not pos:
                                filled = await run_mt5(lambda _ot=ot: find_position_from_order(_ot))
                                if filled:
                                    fill_px = await run_mt5(lambda _ot=ot: get_fill_price_from_order(_ot))
                                    info[key_pos] = filled
                                    entry_prices[filled] = fill_px or (worst_entry if "worst" in key_ot else deep_entry)
                                    position_map[mid] = filled
                                    tp_sl_updated[filled] = False
                                    label = "WORST" if "worst" in key_ot else "DEEP"
                                    log(_LOG_SEP, "INFO")
                                    log(f"SPLIT FILL ({label}): order {ot} → position {filled} @ ${(fill_px if fill_px else 0):.2f}", "INFO")
                                    log(_LOG_SEP, "INFO")
                                    record_entry_stat("limit_fill", side=side,
                                                      limit_price=worst_entry if "worst" in key_ot else deep_entry,
                                                      fill_price=fill_px, zone_low=zone_low, zone_high=zone_high)
                                    if signal_records:
                                        lp_used = worst_entry if "worst" in key_ot else deep_entry
                                        signal_records.record_bot_entry(mid, entered=True, entry_type="limit",
                                                                        entry_price=fill_px or lp_used, ticket=filled)
                                        signal_records.record_entry_event(mid, "limit_fill", side=side,
                                                                          limit_price=lp_used,
                                                                          fill_price=fill_px or lp_used,
                                                                          zone_low=zone_low, zone_high=zone_high)
                                        signal_records.update_tp_sl(mid, info.get("all_tps") or {}, info.get("sl") or failsafe_sl or 0)
                                        _ticket_to_msg_id[filled] = mid
                                    if outcome_tracker:
                                        outcome_tracker.register_trade(filled, side, fill_px or worst_entry,
                                                                        info.get("sl") or failsafe_sl,
                                                                        info.get("all_tps", {}))
                                    # Apply real SL+TP3 if available
                                    if info.get("sl") or info.get("all_tps"):
                                        await update_position_sl_tp(filled, info.get("sl"), info.get("all_tps") or {}, " (SPLIT-FILL)")

                        worst_pos = info.get("worst_pos_ticket")
                        deep_pos  = info.get("deep_pos_ticket")
                        both_filled = worst_pos and deep_pos

                        # ── Both filled: watch for price returning to worst entry → BE ──
                        if both_filled:
                            price_back_to_worst = (side == "BUY"  and cp and cp >= worst_entry) or \
                                                  (side == "SELL" and cp and cp <= worst_entry)

                            if price_back_to_worst:
                                # CLOSE the worst position at market (it's at breakeven)
                                if not info["worst_be_done"]:
                                    log(f"BE TRIGGER (both filled): price ${cp:.2f} returned to worst entry ${worst_entry:.2f}", "INFO")
                                    log(f"  Closing worst position {worst_pos} at market (breakeven)", "INFO")
                                    ok = await run_mt5(lambda _t=worst_pos, _s=side: close_position(_t, _s))
                                    if ok:
                                        info["worst_be_done"] = True
                                    else:
                                        log(f"  Failed to close worst position {worst_pos} — retrying next tick", "WARN")

                                # Set SL on deep position to deep_entry + buffer (true breakeven for deep)
                                if info["worst_be_done"] and not info["deep_be_done"]:
                                    deep_actual_entry = entry_prices.get(deep_pos, deep_entry)
                                    be_sl = (deep_actual_entry + SPLIT_ENTRY_BE_BUFFER) if side == "BUY" \
                                            else (deep_actual_entry - SPLIT_ENTRY_BE_BUFFER)
                                    ok = await run_mt5(lambda: set_stop_loss(deep_pos, be_sl))
                                    if ok:
                                        info["deep_be_done"] = True
                                        log(f"  Deep position {deep_pos} SL → ${be_sl:.2f} (BE + ${SPLIT_ENTRY_BE_BUFFER} buffer)", "INFO")
                                    else:
                                        log(f"  Failed to set SL on deep position {deep_pos}", "WARN")

                        # ── Only worst filled, deep still pending ──
                        elif worst_pos and not deep_pos:
                            # Check if deep order expired or was removed
                            if deep_ot:
                                deep_still_live = await run_mt5(lambda _ot=deep_ot: mt5.orders_get(ticket=_ot))
                                if not deep_still_live:
                                    info["deep_order_ticket"] = None
                                    log(f"Deep order {deep_ot} gone (not filled) — only worst position active", "INFO")

                            # No BE for single-entry case — let it ride with its SL/TP

                        # ── Timeout ──
                        if elapsed >= LIMIT_ORDER_TIMEOUT:
                            log(f"SPLIT ORDER TIMEOUT (split_both_pending): msg_id={mid} after {elapsed:.0f}s", "WARN")
                            for key_ot in ("worst_order_ticket", "deep_order_ticket"):
                                ot = info.get(key_ot)
                                if ot:
                                    await run_mt5(lambda _ot=ot: cancel_limit_order(_ot))
                            if not worst_pos and not deep_pos:
                                _pmin = info.get("price_min_seen", float('inf'))
                                _pmax = info.get("price_max_seen", float('-inf'))
                                _best  = (_pmin <= zone_low)  if side == "BUY" else (_pmax >= zone_high)
                                _worst = (_pmin <= zone_high) if side == "BUY" else (_pmax >= zone_low)
                                record_entry_stat("limit_timeout", side=side, limit_price=worst_entry, elapsed=elapsed,
                                                  best_reached=_best, worst_reached=_worst, zone_low=zone_low, zone_high=zone_high)
                                if signal_records:
                                    signal_records.record_bot_entry(mid, entered=False, entry_type="limit_timeout")
                                    signal_records.record_entry_event(mid, "limit_timeout", side=side,
                                                                      limit_price=worst_entry, elapsed=elapsed,
                                                                      best_reached=_best, worst_reached=_worst)
                                    signal_records.finalize(mid)
                                if outcome_tracker and info.get("all_tps"):
                                    vt_id    = -abs(mid)
                                    vt_sl    = info.get("sl") or failsafe_sl or 0
                                    vt_entry = info.get("signal_market_price") or worst_entry
                                    outcome_tracker.register_virtual_trade(vt_id, side, vt_entry, vt_sl, info["all_tps"],
                                                                           cancel_reason=f"limit_timeout:{elapsed:.0f}s")
                                    log(f"VIRTUAL TRADE registered: id={vt_id} side={side} entry=${vt_entry:.2f}", "INFO")
                                completed.append(mid)
                            continue

                        # ── Price invalidation (only if neither filled yet) ──
                        if not worst_pos and not deep_pos and cp:
                            sl_val = info.get("sl") or failsafe_sl
                            tp_val = info.get("tp3") or info.get("tp1")
                            invalidated = False
                            reason = ""
                            if sl_val:
                                price_reached_zone = (
                                    (side == "BUY"  and info.get("price_max_seen", float('-inf')) >= zone_low) or
                                    (side == "SELL" and info.get("price_min_seen", float('inf'))  <= zone_high)
                                )
                                if price_reached_zone:
                                    if side == "BUY" and cp <= sl_val:
                                        invalidated, reason = True, f"Price ${cp:.2f} <= SL ${sl_val:.2f}"
                                    elif side == "SELL" and cp >= sl_val:
                                        invalidated, reason = True, f"Price ${cp:.2f} >= SL ${sl_val:.2f}"
                            if not invalidated and tp_val:
                                if side == "BUY" and cp >= tp_val:
                                    invalidated, reason = True, f"Price ${cp:.2f} >= TP ${tp_val:.2f}"
                                elif side == "SELL" and cp <= tp_val:
                                    invalidated, reason = True, f"Price ${cp:.2f} <= TP ${tp_val:.2f}"
                            if invalidated:
                                log(f"SPLIT ORDER INVALIDATED: {reason}", "WARN")
                                # Try to cancel pending orders; track if any were actually filled
                                any_filled = False
                                for key_ot, key_pos in [("worst_order_ticket", "worst_pos_ticket"),
                                                        ("deep_order_ticket",  "deep_pos_ticket")]:
                                    ot = info.get(key_ot)
                                    if ot:
                                        ok = await run_mt5(lambda _ot=ot: cancel_limit_order(_ot))
                                        if not ok:
                                            # Cancel failed — check if order was actually filled
                                            filled = await run_mt5(lambda _ot=ot: find_position_from_order(_ot))
                                            if filled:
                                                fill_px = await run_mt5(lambda _ot=ot: get_fill_price_from_order(_ot))
                                                info[key_pos] = filled
                                                entry_prices[filled] = fill_px or (worst_entry if "worst" in key_ot else deep_entry)
                                                position_map[mid] = filled
                                                tp_sl_updated[filled] = False
                                                label = "WORST" if "worst" in key_ot else "DEEP"
                                                log(f"INVALIDATION RECOVERY: order {ot} was filled → position {filled} @ ${(fill_px if fill_px else 0):.2f}", "INFO")
                                                any_filled = True
                                                if outcome_tracker:
                                                    outcome_tracker.register_trade(filled, side, fill_px or (worst_entry if "worst" in key_ot else deep_entry),
                                                                                    info.get("sl") or failsafe_sl,
                                                                                    info.get("all_tps", {}))
                                                if signal_records:
                                                    lp_used = worst_entry if "worst" in key_ot else deep_entry
                                                    signal_records.record_bot_entry(mid, entered=True, entry_type="limit",
                                                                                    entry_price=fill_px or lp_used, ticket=filled)
                                                    _ticket_to_msg_id[filled] = mid
                                if any_filled:
                                    log(f"INVALIDATION RECOVERED: position(s) found — keeping split order active for BE monitoring", "INFO")
                                    continue
                                record_entry_stat("limit_invalidated", side=side, reason=reason,
                                                  zone_low=zone_low, zone_high=zone_high)
                                if signal_records:
                                    _pmin2 = info.get("price_min_seen", float('inf'))
                                    _pmax2 = info.get("price_max_seen", float('-inf'))
                                    _best2  = (_pmin2 <= zone_low)  if side == "BUY" else (_pmax2 >= zone_high)
                                    _worst2 = (_pmin2 <= zone_high) if side == "BUY" else (_pmax2 >= zone_low)
                                    signal_records.record_bot_entry(mid, entered=False, entry_type="limit_timeout")
                                    signal_records.record_entry_event(mid, "limit_timeout", side=side,
                                                                      limit_price=worst_entry, elapsed=elapsed,
                                                                      best_reached=_best2, worst_reached=_worst2)
                                    signal_records.finalize(mid)
                                if outcome_tracker and info.get("all_tps"):
                                    vt_id    = -abs(mid)
                                    vt_sl    = info.get("sl") or failsafe_sl or 0
                                    vt_entry = info.get("signal_market_price") or worst_entry
                                    outcome_tracker.register_virtual_trade(vt_id, side, vt_entry, vt_sl, info["all_tps"],
                                                                           cancel_reason=f"price_invalidated:{reason}")
                                    log(f"VIRTUAL TRADE registered: id={vt_id} side={side} entry=${vt_entry:.2f}", "INFO")
                                completed.append(mid)
                                continue

                    # ────────────────────────────────────────
                    # STRATEGY: split_market_and_pending
                    # Market position (worst) + pending limit (deep)
                    # ────────────────────────────────────────
                    elif strategy == "split_market_and_pending":
                        market_pos = info.get("market_pos_ticket")
                        deep_ot    = info.get("deep_order_ticket")
                        deep_pos   = info.get("deep_pos_ticket")

                        # Check deep order fill
                        if deep_ot and not deep_pos:
                            filled = await run_mt5(lambda _ot=deep_ot: find_position_from_order(_ot))
                            if filled:
                                fill_px = await run_mt5(lambda _ot=deep_ot: get_fill_price_from_order(_ot))
                                info["deep_pos_ticket"] = filled
                                entry_prices[filled] = fill_px or deep_entry
                                tp_sl_updated[filled] = False
                                log(_LOG_SEP, "INFO")
                                log(f"SPLIT FILL (DEEP): order {deep_ot} → position {filled} @ ${(fill_px if fill_px else 0):.2f}", "INFO")
                                log(_LOG_SEP, "INFO")
                                if outcome_tracker:
                                    outcome_tracker.register_trade(filled, side, fill_px or deep_entry,
                                                                    info.get("sl") or failsafe_sl,
                                                                    info.get("all_tps", {}))
                                if signal_records:
                                    signal_records.record_bot_entry(mid, entered=True, entry_type="limit",
                                                                    entry_price=fill_px or deep_entry, ticket=filled)
                                    signal_records.record_entry_event(mid, "limit_fill", side=side,
                                                                      limit_price=deep_entry,
                                                                      fill_price=fill_px or deep_entry,
                                                                      zone_low=zone_low, zone_high=zone_high)
                                    signal_records.update_tp_sl(mid, info.get("all_tps") or {}, info.get("sl") or failsafe_sl or 0)
                                    _ticket_to_msg_id[filled] = mid
                                if info.get("sl") or info.get("all_tps"):
                                    await update_position_sl_tp(filled, info.get("sl"), info.get("all_tps") or {}, " (SPLIT-FILL)")
                                deep_pos = filled
                                position_map[mid] = filled  # track deep pos as the active position

                        # Market position is always the "worst" for BE purposes
                        market_actual_entry = info.get("market_entry_price", worst_entry)

                        if market_pos and deep_pos:
                            # Both active — watch for price return to ZONE EDGE (worst_entry),
                            # NOT the market entry price. Market entries inside the zone would
                            # trigger BE too soon if we used market_actual_entry.
                            price_back_to_zone_edge = (side == "BUY"  and cp and cp >= worst_entry) or \
                                                      (side == "SELL" and cp and cp <= worst_entry)
                            if price_back_to_zone_edge:
                                # CLOSE the market (worst) position
                                if not info["worst_be_done"]:
                                    market_still_open = await run_mt5(lambda _t=market_pos: mt5.positions_get(ticket=_t))
                                    if not market_still_open:
                                        info["worst_be_done"] = True
                                        log(f"  Market pos {market_pos} already closed — marking worst_be_done=True", "INFO")
                                    else:
                                        log(f"BE TRIGGER (market+deep): price ${cp:.2f} returned to zone edge ${worst_entry:.2f}", "INFO")
                                        log(f"  Closing market (worst) position {market_pos} (entered at ${market_actual_entry:.2f})", "INFO")
                                        ok = await run_mt5(lambda _t=market_pos, _s=side: close_position(_t, _s))
                                        if ok:
                                            info["worst_be_done"] = True
                                        else:
                                            log(f"  Failed to close market position {market_pos} — retrying next tick", "WARN")
                                # Set SL on deep position to slightly above deep entry
                                if info["worst_be_done"] and not info["deep_be_done"]:
                                    deep_actual = entry_prices.get(deep_pos, deep_entry)
                                    be_sl = (deep_actual + SPLIT_ENTRY_BE_BUFFER) if side == "BUY" \
                                            else (deep_actual - SPLIT_ENTRY_BE_BUFFER)
                                    ok = await run_mt5(lambda: set_stop_loss(deep_pos, be_sl))
                                    if ok:
                                        info["deep_be_done"] = True
                                        log(f"  Deep pos {deep_pos} SL → ${be_sl:.2f} (BE + ${SPLIT_ENTRY_BE_BUFFER} buffer)", "INFO")
                                    else:
                                        log(f"  Failed to set SL on deep position {deep_pos}", "WARN")

                        elif market_pos and not deep_pos:
                            # Only market position — at TP2, move to BE
                            if not info["only_worst_tp1_be_done"]:
                                all_tps = info.get("all_tps") or {}
                                tp2 = all_tps.get(2)
                                if tp2 and cp:
                                    tp2_hit = (side == "BUY" and cp >= tp2) or (side == "SELL" and cp <= tp2)
                                    if tp2_hit:
                                        ok = await run_mt5(lambda: set_stop_loss(market_pos, market_actual_entry))
                                        if ok:
                                            info["only_worst_tp1_be_done"] = True
                                            log(f"TP2 BE: market pos {market_pos} SL → ${market_actual_entry:.2f}", "INFO")

                        # Timeout on deep order
                        if deep_ot and not deep_pos and elapsed >= LIMIT_ORDER_TIMEOUT:
                            log(f"DEEP ORDER TIMEOUT: order {deep_ot} after {elapsed:.0f}s", "WARN")
                            await run_mt5(lambda _ot=deep_ot: cancel_limit_order(_ot))
                            info["deep_order_ticket"] = None
                            record_entry_stat("limit_timeout", side=side, limit_price=deep_entry, elapsed=elapsed)
                            # Market position stays open — this becomes "only worst filled"

                    # ────────────────────────────────────────
                    # STRATEGY: whole_lot
                    # Single pending stop order at best entry
                    # ────────────────────────────────────────
                    elif strategy == "whole_lot":
                        ot       = info.get("order_ticket")
                        pos_tick = info.get("pos_ticket")

                        if ot and not pos_tick:
                            filled = await run_mt5(lambda _ot=ot: find_position_from_order(_ot))
                            if not filled:
                                # Check if order still alive
                                still_live = await run_mt5(lambda _ot=ot: mt5.orders_get(ticket=_ot))
                                if not still_live and ot:
                                    # Try harder via history
                                    now_dt = datetime.now()
                                    all_deals = await run_mt5(lambda: mt5.history_deals_get(
                                        now_dt - timedelta(days=7), now_dt + timedelta(minutes=1)))
                                    if all_deals:
                                        for d in all_deals:
                                            if d.order == ot and d.entry == 0:
                                                filled = d.position_id
                                                break

                            if filled:
                                fill_px = await run_mt5(lambda _ot=ot: get_fill_price_from_order(_ot))
                                info["pos_ticket"] = filled
                                entry_prices[filled] = fill_px or worst_entry
                                position_map[mid]    = filled
                                tp_sl_updated[filled] = False
                                log(_LOG_SEP, "INFO")
                                log(f"WHOLE LOT FILLED: order {ot} → position {filled} @ ${(fill_px if fill_px else 0):.2f}", "INFO")
                                log(_LOG_SEP, "INFO")
                                record_entry_stat("limit_fill", side=side, limit_price=worst_entry,
                                                  fill_price=fill_px, zone_low=zone_low, zone_high=zone_high)
                                if outcome_tracker:
                                    outcome_tracker.register_trade(filled, side, fill_px or worst_entry,
                                                                    info.get("sl") or failsafe_sl,
                                                                    info.get("all_tps", {}))
                                if signal_records:
                                    signal_records.record_bot_entry(mid, entered=True, entry_type="limit",
                                                                    entry_price=fill_px or worst_entry, ticket=filled)
                                    signal_records.record_entry_event(mid, "limit_fill", side=side,
                                                                      limit_price=worst_entry,
                                                                      fill_price=fill_px or worst_entry,
                                                                      zone_low=zone_low, zone_high=zone_high)
                                    signal_records.update_tp_sl(mid, info.get("all_tps") or {}, info.get("sl") or failsafe_sl or 0)
                                    _ticket_to_msg_id[filled] = mid
                                if info.get("sl") or info.get("all_tps"):
                                    await update_position_sl_tp(filled, info.get("sl"), info.get("all_tps") or {}, " (WHOLE-FILL)")
                            elif elapsed >= LIMIT_ORDER_TIMEOUT:
                                log(f"WHOLE LOT TIMEOUT: order {ot} after {elapsed:.0f}s", "WARN")
                                await run_mt5(lambda _ot=ot: cancel_limit_order(_ot))
                                _pmin = info.get("price_min_seen", float('inf'))
                                _pmax = info.get("price_max_seen", float('-inf'))
                                _best  = (_pmin <= zone_low)  if side == "BUY" else (_pmax >= zone_high)
                                _worst = (_pmin <= zone_high) if side == "BUY" else (_pmax >= zone_low)
                                record_entry_stat("limit_timeout", side=side, limit_price=worst_entry, elapsed=elapsed,
                                                  best_reached=_best, worst_reached=_worst, zone_low=zone_low, zone_high=zone_high)
                                if signal_records:
                                    signal_records.record_bot_entry(mid, entered=False, entry_type="limit_timeout")
                                    signal_records.record_entry_event(mid, "limit_timeout", side=side,
                                                                      limit_price=worst_entry, elapsed=elapsed,
                                                                      best_reached=_best, worst_reached=_worst)
                                    signal_records.finalize(mid)
                                if outcome_tracker and info.get("all_tps"):
                                    vt_id    = -abs(mid)
                                    vt_sl    = info.get("sl") or failsafe_sl or 0
                                    vt_entry = info.get("signal_market_price") or worst_entry
                                    outcome_tracker.register_virtual_trade(vt_id, side, vt_entry, vt_sl, info["all_tps"],
                                                                           cancel_reason=f"limit_timeout:{elapsed:.0f}s")
                                    log(f"VIRTUAL TRADE registered: id={vt_id} side={side} entry=${vt_entry:.2f}", "INFO")
                                completed.append(mid)
                                continue

                    # ────────────────────────────────────────
                    # STRATEGY: full_market
                    # No pending orders — single full-lot market position
                    # No order management needed in monitor (just track position lifecycle)
                    # ────────────────────────────────────────
                    elif strategy == "full_market":
                        pass  # position tracked via position_map / trade_outcome_monitor

                for mid in completed:
                    _split_orders.pop(mid, None)

            except Exception as e:
                log(f"CRITICAL: Split entry monitor error: {e}", "ERROR")
                import traceback; log(traceback.format_exc(), "ERROR")
                await asyncio.sleep(5)

    # ── Heartbeat ──
    async def heartbeat_monitor():
        global heartbeat_counter
        while True:
            try:
                await asyncio.sleep(900)
                heartbeat_counter += 1
                m = get_bot_metrics()
                log(
                    f"HEARTBEAT #{heartbeat_counter} | Up {m['uptime_hours']:.1f}h | "
                    f"Msgs {m['messages_received']} | Trades {m['trades_executed']} | "
                    f"MT5={'OK' if m['mt5_connected'] else 'DOWN'} "
                    f"TG={'OK' if m['telegram_connected'] else 'DOWN'} | "
                    f"Pos {m['active_positions']} | Buf {m['buffered_signals']} Split {m['pending_split_orders']}",
                    "INFO",
                )
            except Exception as e:
                log(f"Heartbeat error: {e}", "ERROR")
                await asyncio.sleep(10)

    _prev_mt5_healthy: bool = True
    _prev_tg_healthy:  bool = True

    async def connection_health_monitor():
        nonlocal _prev_mt5_healthy, _prev_tg_healthy
        while True:
            try:
                await asyncio.sleep(300)
                mt5_healthy = await run_mt5(check_mt5_health)
                if not mt5_healthy:
                    if _prev_mt5_healthy:
                        log("MT5 connection went DOWN - attempting recovery", "WARN")
                    recovered = await run_mt5(recover_mt5_connection)
                    _prev_mt5_healthy = recovered
                else:
                    if not _prev_mt5_healthy:
                        log("MT5 connection restored", "INFO")
                    _prev_mt5_healthy = True

                if last_telegram_activity > 0:
                    since = time.time() - last_telegram_activity
                    if since > 300 and _telegram_client is not None:
                        try:
                            if not _telegram_client.is_connected():
                                await reconnect_telegram(_telegram_client, "health_monitor")
                            else:
                                await asyncio.wait_for(_telegram_client.get_me(), timeout=10)
                        except Exception:
                            await reconnect_telegram(_telegram_client, "health_monitor_ping")
            except Exception as e:
                log(f"Health monitor error: {e}", "ERROR")
                await asyncio.sleep(10)

    async def monitor_stale_failsafe():
        while True:
            try:
                await asyncio.sleep(120)
                for ticket in list(tp_sl_updated.keys()):
                    pos = await run_mt5(lambda t=ticket: mt5.positions_get(ticket=t))
                    if not pos:
                        tp_sl_updated.pop(ticket, None)
                        entry_prices.pop(ticket, None)
                        for mid_key, tid in list(position_map.items()):
                            if tid == ticket:
                                position_map.pop(mid_key, None)
                                break
                    elif not tp_sl_updated.get(ticket, True):
                        log(f"WARNING: Ticket {ticket} still has FAILSAFE SL/TP", "WARN")
            except Exception as e:
                log(f"Stale failsafe monitor error: {e}", "ERROR")
                await asyncio.sleep(30)

    async def mt5_cache_refresher():
        while True:
            try:
                await asyncio.sleep(5)
                await run_mt5(refresh_mt5_cache)
            except Exception:
                await asyncio.sleep(5)

    _last_status_trades: int = 0
    _last_status_msgs:   int = 0

    async def cleanup_and_report():
        nonlocal _last_status_trades, _last_status_msgs
        while True:
            try:
                await asyncio.sleep(300)
                if signal_queue:
                    signal_queue.remove_old_signals(days=7)
                if session_manager:
                    session_manager.cleanup_old_backups(keep_count=5)
                if trades_executed != _last_status_trades or messages_received != _last_status_msgs:
                    print_bot_status()
                    _last_status_trades = trades_executed
                    _last_status_msgs   = messages_received
            except Exception as e:
                log(f"Cleanup error: {e}", "ERROR")
                await asyncio.sleep(10)

    async def trade_outcome_monitor():
        log("Trade outcome monitor started (0.5s interval)", "INFO")
        while True:
            try:
                await asyncio.sleep(0.5)
                if not outcome_tracker:
                    continue

                active_tickets = outcome_tracker.get_active_tickets()

                untracked_signal_states: Dict[int, Dict] = {}
                if signal_records:
                    tracked_ids = set(_ticket_to_msg_id.values())
                    untracked_signal_states = {
                        mid: state
                        for mid, state in signal_records.get_active_states().items()
                        if mid not in tracked_ids
                    }

                if not active_tickets and not untracked_signal_states:
                    continue

                tick = await run_mt5(lambda: mt5.symbol_info_tick(SYMBOL))
                if tick is None:
                    continue
                bid = float(tick.bid)
                ask = float(tick.ask)

                if bid < 100 or ask < 100 or abs(ask - bid) > 5:
                    continue

                for mid, state in untracked_signal_states.items():
                    price = bid if state["side"] == "BUY" else ask
                    signal_records.update_price(mid, price)

                for ticket in active_tickets:
                    info = outcome_tracker.get_trade_info(ticket)
                    if info is None:
                        continue

                    current_price = bid if info["side"] == "BUY" else ask
                    entry = info.get("entry_price", 0)
                    if entry > 0 and abs(current_price - entry) > 50:
                        continue

                    if signal_records:
                        msg_id_for_ticket = _ticket_to_msg_id.get(ticket)
                        if msg_id_for_ticket:
                            signal_records.update_price(msg_id_for_ticket, current_price)

                    newly_hit = outcome_tracker.check_levels(ticket, current_price)
                    for level in newly_hit:
                        tag = " [VIRTUAL]" if info.get("virtual") else (" [SHADOW]" if info.get("position_closed") else "")
                        log(f"OUTCOME TRACKER: Ticket {ticket} hit {level} at ${current_price:.2f}{tag}", "INFO")
                        if signal_records:
                            m = _ticket_to_msg_id.get(ticket)
                            if m:
                                li = level_to_int(level)
                                if li is not None:
                                    signal_records.record_level_hit(m, li)

                    if outcome_tracker.is_shadow_tracking(ticket):
                        if outcome_tracker.should_finalize(ticket):
                            # Re-fetch info so finalization sees levels updated by check_levels() above
                            info = outcome_tracker.get_trade_info(ticket) or info
                            seq  = info.get("sequence_so_far", [])
                            seq_str = " → ".join(seq) if seq else "NONE"
                            profit = info.get("position_profit", 0.0) or 0.0
                            levels_hit = set(info.get("levels_hit", []))
                            tp_nums = [int(k) for k in info["tp_levels"].keys()]
                            highest_tp = f"TP{max(tp_nums)}" if tp_nums else None
                            reason = f"shadow:{highest_tp}_hit" if (highest_tp and highest_tp in levels_hit) \
                                     else ("shadow:SL_hit" if "SL" in levels_hit else "shadow:timeout")
                            virt_tag = "VIRTUAL" if info.get("virtual") else "SHADOW"
                            log(f"OUTCOME TRACKER: Ticket {ticket} {virt_tag} tracking FINALIZED | "
                                f"P&L=${profit:.2f} | Sequence: {seq_str} | Reason: {reason}", "INFO")
                            outcome_tracker.close_trade(ticket, profit, reason)
                            if signal_records:
                                _mid = _ticket_to_msg_id.pop(ticket, None)
                                if _mid:
                                    signal_records.finalize(_mid)
                        continue

                    pos = await run_mt5(lambda t=ticket: mt5.positions_get(ticket=t))
                    if not pos:
                        final_profit = 0.0
                        close_price  = 0.0
                        close_reason = "unknown"
                        now_dt = datetime.now()
                        deals  = await run_mt5(lambda: mt5.history_deals_get(
                            now_dt - timedelta(hours=24), now_dt + timedelta(minutes=1)))
                        if deals:
                            for d in deals:
                                if d.position_id == ticket and d.entry == 1:
                                    final_profit = float(d.profit)
                                    close_price  = float(d.price)
                                    close_reason = d.comment or "broker"
                                    break
                        seq = info.get("sequence_so_far", [])
                        seq_str = " → ".join(seq) if seq else "NONE"
                        log(f"OUTCOME TRACKER: Ticket {ticket} position CLOSED | P&L=${final_profit:.2f} | "
                            f"Close price=${close_price:.2f} | Sequence so far: {seq_str} | Reason: {close_reason}", "INFO")
                        log(f"OUTCOME TRACKER: Starting SHADOW TRACKING for ticket {ticket} "
                            f"(will monitor until highest TP or SL is hit, max 24h)", "INFO")
                        outcome_tracker.mark_position_closed(ticket, final_profit, close_reason, close_price)
                        tp_sl_updated.pop(ticket, None)
                        entry_prices.pop(ticket, None)
                        for mid_key, tid in list(position_map.items()):
                            if tid == ticket:
                                position_map.pop(mid_key, None)
                                break

            except Exception as e:
                log(f"Trade outcome monitor error: {e}", "ERROR")
                import traceback; log(traceback.format_exc(), "ERROR")
                await asyncio.sleep(5)

    async def message_poller():
        global messages_received, messages_ignored, last_message_time, last_telegram_activity
        log("Message poller started", "INFO")
        last_seen_ids: Dict[int, int] = {}
        for chat_id in ALLOWED_CHAT_IDS:
            try:
                msgs = await client.get_messages(chat_id, limit=1)
                if msgs: last_seen_ids[chat_id] = msgs[0].id
            except Exception: pass

        while True:
            try:
                await asyncio.sleep(POLLING_INTERVAL)
                for chat_id in ALLOWED_CHAT_IDS:
                    try:
                        msgs = await client.get_messages(chat_id, limit=5)
                        if not msgs: continue
                        min_id = last_seen_ids.get(chat_id, 0)
                        for msg in reversed(msgs):
                            if msg.id <= min_id or msg.id in _processed_msg_ids:
                                continue
                            _processed_msg_ids.add(msg.id)
                            if len(_processed_msg_ids) > _MAX_PROCESSED_IDS:
                                s = sorted(_processed_msg_ids)
                                _processed_msg_ids.difference_update(s[:len(s)//2])
                            last_seen_ids[chat_id] = max(last_seen_ids.get(chat_id, 0), msg.id)
                            text = extract_message_text(msg).strip()
                            if not text: continue
                            last_telegram_activity = time.time()
                            last_message_time      = time.time()
                            messages_received += 1
                            log(_LOG_SEP, "INFO")
                            log(f"[MAIN] NEW MESSAGE (POLLED) | id={msg.id}", "INFO")
                            log(f"   Preview: {text[:150]}", "INFO")
                            log(_LOG_SEP, "INFO")
                            handled = await process_message(text, msg.id, "[MAIN]", source=" (POLLED)")
                            if not handled:
                                messages_ignored += 1
                        if msgs:
                            last_seen_ids[chat_id] = max(last_seen_ids.get(chat_id, 0), msgs[0].id)
                    except Exception as ce:
                        log(f"Poller error for {chat_id}: {ce}", "WARN")
            except Exception as e:
                log(f"Message poller exception: {e}", "ERROR")
                import traceback; log(traceback.format_exc(), "ERROR")
                await asyncio.sleep(10)

    # ── Start tasks ──
    log("Starting background tasks...", "INFO")
    asyncio.create_task(heartbeat_monitor())
    asyncio.create_task(connection_health_monitor())
    asyncio.create_task(monitor_stale_failsafe())
    asyncio.create_task(cleanup_and_report())
    asyncio.create_task(mt5_cache_refresher())
    asyncio.create_task(split_entry_monitor())
    asyncio.create_task(trade_outcome_monitor())
    log("All background tasks created", "INFO")

    # ── Connect Telegram ──
    await client.start()
    telegram_connected     = True
    last_telegram_activity = time.time()
    log("Telegram connected!", "INFO")

    try:
        dialogs = await client.get_dialogs()
        log(f"Loaded {len(dialogs)} dialogs", "INFO")
    except Exception as e:
        log(f"get_dialogs failed: {e}", "WARN")

    for chat_id in ALLOWED_CHAT_IDS:
        try:
            entity = await client.get_entity(chat_id)
            title  = getattr(entity, "title", None) or f"Chat {chat_id}"
            log(f"   Can access: {title} ({chat_id})", "INFO")
        except Exception as e:
            log(f"   Cannot access chat {chat_id}: {e}", "ERROR")

    asyncio.create_task(message_poller())
    print_bot_status()
    await replay_pending_signals(client)

    log(_LOG_SEP, "INFO")
    log("BOT READY — LISTENING FOR SIGNALS (SPLIT ENTRY / TP3)", "INFO")
    log(_LOG_SEP, "INFO")

    consecutive_failures = 0
    try:
        while True:
            if not client.is_connected():
                log("Client not connected — reconnecting...", "WARN")
                reconnected = await reconnect_telegram(client, "pre_flight_check")
                if reconnected:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        restart_bot()
                    await asyncio.sleep(min(30 * (2 ** min(consecutive_failures - 1, 4)), 300))
                    continue
            try:
                await client.run_until_disconnected()
                telegram_connected = False
                telegram_connection_losses += 1
                log("Telegram disconnected — reconnecting...", "WARN")
                reconnected = await reconnect_telegram(client, "run_until_disconnected")
                if reconnected:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        restart_bot()
                continue
            except KeyboardInterrupt:
                log("Keyboard interrupt — shutting down", "INFO")
                break
            except TypeNotFoundError as e:
                telegram_connected = False
                telegram_connection_losses += 1
                log(f"Telethon TypeNotFoundError: {e}", "ERROR")
                await reconnect_telegram(client, "TypeNotFoundError")
            except Exception as e:
                telegram_connected = False
                telegram_connection_losses += 1
                log(f"Main loop exception: {e}", "ERROR")
                import traceback; log(traceback.format_exc(), "ERROR")
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    restart_bot()
                await asyncio.sleep(min(30 * (2 ** min(consecutive_failures - 1, 4)), 300))
    finally:
        log("Bot shutting down...", "INFO")
        try: mt5.shutdown()
        except Exception: pass


if __name__ == "__main__":
    asyncio.run(main())
