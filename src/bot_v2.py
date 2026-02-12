#!/usr/bin/env python3
"""
Gold Trading Bot - Clean Implementation
Connects to Telegram channel for gold trading signals and executes trades automatically.
"""

import asyncio
import re
import sys
import os
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

# ===================== HELPERS =====================

def log(msg: str, level: str = "INFO"):
    """Print with timestamp and level"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


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
    account = mt5.account_info()
    if account:
        if account.trade_allowed:
            log("✅ AutoTrading ENABLED", "INFO")
        else:
            log("❌ AutoTrading DISABLED - trading forbidden!", "ERROR")
            log("   FIX: Enable in Terminal > Tools > Options > Expert Advisors", "ERROR")
    else:
        log("⚠️ Cannot check AutoTrading status", "WARN")


def ensure_mt5_connection():
    """Initialize MT5 if not connected"""
    if not mt5.initialize():
        log(f"❌ MT5 initialization FAILED: {mt5.last_error()}", "ERROR")
        sys.exit(1)
    
    account = mt5.account_info()
    if account:
        log(f"✅ MT5 Ready | Acct: {account.login} | Balance: ${account.balance:.2f}", "INFO")
    else:
        log(f"⚠️ Could not retrieve account info", "WARN")
    
    check_autotrading_status()


def get_account_balance() -> float:
    """Get current account balance"""
    acc = mt5.account_info()
    if acc is None:
        log("❌ account_info() failed", "ERROR")
        return 0.0
    return float(acc.balance)


def get_market_price(side: str) -> Optional[float]:
    """Get entry price (ask for BUY, bid for SELL)"""
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log(f"❌ Cannot get market data: {mt5.last_error()}", "ERROR")
        return None
    return float(tick.ask if side == "BUY" else tick.bid)


def calculate_lot_size(entry_price: float, sl_price: float, balance: float) -> float:
    """
    Calculate max lot size that risks less than RISK_PCT of balance.
    lot_size = (balance * risk_pct) / (price_distance * contract_size)
    """
    price_distance = abs(entry_price - sl_price)
    if price_distance <= 0:
        log(f"❌ Invalid price distance: {price_distance}", "ERROR")
        return 0.01
    
    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None:
        log(f"❌ Cannot get symbol info", "ERROR")
        return 0.01
    
    contract_size = float(symbol_info.trade_contract_size)
    vol_min = float(symbol_info.volume_min)
    vol_max = float(symbol_info.volume_max)
    vol_step = float(symbol_info.volume_step)
    
    max_risk_money = balance * RISK_PCT
    lot = max_risk_money / (price_distance * contract_size)
    
    # Clamp to symbol limits
    lot = max(lot, vol_min)
    lot = min(lot, vol_max)
    lot = round(lot / vol_step) * vol_step
    
    return lot


def get_filling_mode() -> int:
    """Get the appropriate filling mode for the symbol"""
    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None:
        return mt5.ORDER_FILLING_IOC
    
    filling = symbol_info.filling_mode
    if filling & 2 == 2:
        return mt5.ORDER_FILLING_IOC
    elif filling & 1 == 1:
        return mt5.ORDER_FILLING_FOK
    else:
        return mt5.ORDER_FILLING_RETURN


def open_position(side: str, lot: float) -> Optional[int]:
    """
    Open market position. Returns ticket number or None.
    Does NOT retry - signals are time-sensitive and must execute immediately or be abandoned.
    """
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
    
    result = mt5.order_send(request)
    
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        ticket = result.order
        log(f"✅ {side} OPENED (Ticket {ticket})", "INFO")
        return ticket
    
    if result.retcode == 10027:
        log(f"🚨 AutoTrading DISABLED - cannot trade!", "ERROR")
        return None
    
    log(f"❌ Order failed: {result.comment}", "ERROR")
    
    # Retry for certain errors
    retryable_codes = {10009, 10028, 10044, 9}
    
    if result.retcode in retryable_codes:
        for attempt in range(1, 3):
            time.sleep(2)
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                ticket = result.order
                log(f"✅ {side} OPENED (Ticket {ticket}) [retry]", "INFO")
                return ticket
    
    return None


def close_position(ticket: int) -> bool:
    """Close an open position immediately at market price (TP3 HIT safety exit)"""
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"❌ Position {ticket} not found", "WARN")
        return False
    
    p = pos[0]
    side = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
    close_type = mt5.ORDER_TYPE_SELL if side == "BUY" else mt5.ORDER_TYPE_BUY
    
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log(f"❌ No tick data", "ERROR")
        return False
    
    price = float(tick.bid if side == "BUY" else tick.ask)
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": float(p.volume),
        "type": close_type,
        "position": ticket,
        "price": price,
        "magic": MAGIC,
        "comment": "TP3 SAFETY EXIT",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    result = mt5.order_send(request)
    success = result.retcode == mt5.TRADE_RETCODE_DONE
    
    if success:
        log(f"✅ Ticket {ticket} CLOSED (TP3 Exit)", "INFO")
    else:
        log(f"❌ Close failed: {result.comment}", "ERROR")
    
    return success


def set_stop_loss(ticket: int, sl_price: float) -> bool:
    """Update position stop loss - preserves current TP"""
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"❌ Position {ticket} not found", "ERROR")
        return False
    
    current_tp = float(pos[0].tp) if pos[0].tp > 0 else 0.0
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": sl_price,
        "tp": current_tp,
    }
    
    result = mt5.order_send(request)
    success = result.retcode == mt5.TRADE_RETCODE_DONE
    
    if success:
        log(f"✅ Ticket {ticket} SL updated", "INFO")
    else:
        log(f"⚠️ SL update failed: {result.comment}", "WARN")
    
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

    # If breakeven is active, never allow SL to move back into loss.
    if breakeven_activated.get(ticket, False) and entry is not None:
        if side == "BUY" and sl_price < entry:
            return False, "breakeven protected (BUY)"
        if side == "SELL" and sl_price > entry:
            return False, "breakeven protected (SELL)"

    return True, "ok"


def calculate_failsafe_tp3(entry_price: float, side: str) -> float:
    """Calculate failsafe TP3 based on the pattern: worst entry ± 7 points.
    This provides a reasonable default TP while waiting for real values.
    """
    tp3 = entry_price + 7.0 if side == "BUY" else entry_price - 7.0
    return tp3


def is_valid_tp_price(ticket: int, tp_price: float) -> bool:
    """Check if TP price is valid for the position.
    For BUY: TP must be above entry. For SELL: TP must be below entry.
    Also checks for obviously wrong values (e.g., $543 when entry is $5037).
    """
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        return False
    
    entry = entry_prices.get(ticket, float(pos[0].price_open))
    side = "BUY" if pos[0].type == mt5.ORDER_TYPE_BUY else "SELL"
    
    # For BUY: TP must be above entry
    # For SELL: TP must be below entry
    if side == "BUY":
        is_valid = tp_price > entry
    else:
        is_valid = tp_price < entry
    
    # Additional sanity check: detect obviously wrong values
    # (e.g., $543 when entry is $5037)
    if is_valid and abs(tp_price - entry) > 0.01:  # At least some meaningful distance
        # For gold, if the price difference is wildly wrong (e.g., 4000+ points off),
        # it's probably a typo
        if side == "BUY" and (tp_price - entry) < -1000:  # BUY TP should be above entry
            is_valid = False
        elif side == "SELL" and (entry - tp_price) < -1000:  # SELL TP should be below entry
            is_valid = False
    
    return is_valid


def select_best_tp_with_fallback(ticket: int, tp_levels_dict: Dict[int, float]) -> Tuple[Optional[int], Optional[float]]:
    """Select the best valid TP from available levels, with fallback logic.
    Prefers TP3 > TP2 > TP1, but falls back to lower level if higher is invalid.
    Returns: (tp_level_number, tp_price) or (None, None) if no valid TP found.
    """
    # Try TP3, then TP2, then TP1
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
    
    # Get current position to preserve SL
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        log(f"❌ Position {ticket} not found", "ERROR")
        return False
    
    current_sl = float(pos[0].sl) if pos[0].sl > 0 else 0.0
    log(f"   Current SL: ${current_sl:.5f} (will preserve)", "DEBUG")
    
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": current_sl,  # Preserve current SL
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
        log(f"   [TP1 Check] Ticket {ticket}: Current=${current_price:.5f} vs TP1=${tp1:.5f} (BUY) → {'CROSSED' if crossed else 'not crossed'}", "DEBUG")
    else:  # SELL
        crossed = current_price <= tp1
        log(f"   [TP1 Check] Ticket {ticket}: Current=${current_price:.5f} vs TP1=${tp1:.5f} (SELL) → {'CROSSED' if crossed else 'not crossed'}", "DEBUG")
    
    return crossed


# ===================== PARSING =====================

def is_tp3_hit_message(text: str) -> bool:
    """Check if message is a TP3 HIT signal (safety exit trigger)"""
    t = (text or "").upper()
    # Match "TP3 HIT" or "TP 3 HIT" with flexible spacing
    return bool(re.search(r"\bTP\s*3\s+HIT\b", t))


def parse_signal(text: str) -> Optional[Tuple[str, str]]:
    """
    Parse "XAUUSD BUY NOW" or "XAUUSD SELL NOW"
    Returns (symbol, side) or None
    """
    text = text.upper().strip()
    log(f"🔍 Parsing signal from: {text[:80]}", "DEBUG")
    
    if "BUY NOW" in text:
        log(f"   ✅ Found BUY signal", "DEBUG")
        return (SYMBOL, "BUY")
    if "SELL NOW" in text:
        log(f"   ✅ Found SELL signal", "DEBUG")
        return (SYMBOL, "SELL")
    
    log(f"   ⚠️ No BUY/SELL signal found", "DEBUG")
    return None


def parse_tp_levels(text: str) -> Dict[int, float]:
    """
    Parse all TP levels from text.
    Looks for patterns like "TP1 4877" or "TP 1 4877"
    Returns {1: 4877, 2: 4875, 3: 4873, ...}
    """
    log(f"🔍 Parsing TP levels...", "DEBUG")
    levels = {}
    
    # Pattern: TP followed by digit(s), then a price
    matches = re.finditer(r"TP\s*(\d+)\s+([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    
    for match in matches:
        tp_num = int(match.group(1))
        tp_price = float(match.group(2))
        
        # Filter out unrealistic prices (< 100)
        if tp_price >= 100:
            levels[tp_num] = tp_price
            log(f"   Found TP{tp_num}: ${tp_price:.5f}", "DEBUG")
    
    if not levels:
        log(f"   ⚠️ No TP levels found", "DEBUG")
    
    return levels


def parse_stop_loss(text: str) -> Optional[float]:
    """
    Parse stop loss value from text.
    Looks for patterns like "SL 4888" or "SL 4873"
    """
    log(f"🔍 Parsing stop loss...", "DEBUG")
    
    match = re.search(r"SL\s+([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    
    if match:
        sl_price = float(match.group(1))
        if sl_price >= 100:  # realistic price
            log(f"   Found SL: ${sl_price:.5f}", "DEBUG")
            return sl_price
    
    log(f"   ⚠️ No valid SL found", "DEBUG")
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
    """Extract text from Telegram message"""
    if hasattr(message, "text") and message.text:
        return message.text
    return ""


def log_signal_attempt(side: str, result: str, detail: str = ""):
    """Log a signal attempt for debugging"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "timestamp": ts,
        "signal": side,
        "result": result,
        "detail": detail
    }
    signal_log.append(entry)
    log(f"📋 Signal log: {side} | {result} | {detail}", "DEBUG")


# ===================== TELEGRAM HANDLERS =====================

def init_telegram():
    """Create Telegram client"""
    return TelegramClient(SESSION_FILE, API_ID, API_HASH)


async def replay_pending_signals(client):
    """
    Replay signals from queue that were received but not yet executed.
    This recovers from crashes/disconnects without losing signals.
    """
    if not signal_queue:
        return
    
    pending = signal_queue.get_pending_signals()
    if not pending:
        return
    
    log(f"🔄 Replaying {len(pending)} pending signals", "INFO")
    for sig in pending:
        msg_id = sig.get('id')
        signal_type = sig.get('signal_type')
        log(f"  {signal_type} signal (ID {msg_id})", "INFO")


async def cleanup_and_report():
    """
    Periodic cleanup and reporting of system health.
    """
    while True:
        try:
            await asyncio.sleep(300)  # every 5 minutes
            
            # Cleanup old signals
            if signal_queue:
                signal_queue.remove_old_signals(days=7)
            
            # Cleanup old backups
            if session_manager:
                session_manager.cleanup_old_backups(keep_count=5)
            
            # Report reconnect stats if any
            if reconnect_monitor and reconnect_monitor.get_stats()['total_reconnects'] > 0:
                reconnect_monitor.print_summary()
        
        except Exception as e:
            log(f"⚠️ Exception in cleanup_and_report: {e}", "ERROR")


async def main():
    global signal_queue, session_manager, reconnect_monitor
    
    # Initialize persistence & monitoring modules
    signal_queue = SignalQueue("signal_queue.json")
    session_manager = SessionManager("trading_bot_session", "sessions")
    reconnect_monitor = ReconnectMonitor(alert_threshold=5, time_window_minutes=30)
    
    client = init_telegram()
    ensure_mt5_connection()
    
    log("=" * 70, "INFO")
    log("🚀 BOT INITIALIZATION COMPLETE", "INFO")
    log("=" * 70, "INFO")
    log(f"📱 Main channel ID: {CHANNEL_ID}", "INFO")
    log(f"📱 Test channel ID: {TEST_CHANNEL_ID}", "INFO")
    log(f"💎 Trading symbol: {SYMBOL}", "INFO")
    log(f"💰 Risk per trade: {RISK_PCT*100:.1f}%", "INFO")
    log(f"🛡️ Failsafe SL distance: ${FAILSAFE_SL_DISTANCE:.2f}", "INFO")
    log(f"📊 Magic number: {MAGIC}", "INFO")
    log("=" * 70, "INFO")
    
    @client.on(events.NewMessage(chats=[CHANNEL_ID, TEST_CHANNEL_ID]))
    async def on_new_message(event):
        """Handle new Telegram messages from main or test channel"""
        try:
            text = extract_message_text(event.message).strip()
            msg_id = event.message.id
            channel_name = "[TEST]" if event.chat_id == TEST_CHANNEL_ID else "[MAIN]"
            
            log(f"📨 {channel_name} New message (ID: {msg_id}): {text[:80]}", "INFO")
            
            # CHECK FOR TP3 HIT - SAFETY EXIT (highest priority)
            if is_tp3_hit_message(text):
                log("=" * 70, "INFO")
                log("🚨 TP3 HIT DETECTED - SAFETY EXIT!", "INFO")
                log("=" * 70, "INFO")
                
                # Find and close all open positions for this symbol
                positions = mt5.positions_get(symbol=SYMBOL)
                if positions:
                    closed_count = 0
                    for p in positions:
                        if p.magic == MAGIC:
                            log(f"🎯 Closing position: Ticket {p.ticket} | Volume {p.volume:.3f}", "INFO")
                            if close_position(p.ticket):
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
            
            # Check if this is a trade signal
            signal = parse_signal(text)
            
            if signal:
                symbol, side = signal
                log(f"🎯 SIGNAL DETECTED: {side} {symbol}", "INFO")
                log_signal_attempt(side, "RECEIVED", f"msg_id={msg_id}")
                
                # Get current market price
                entry_price = get_market_price(side)
                if entry_price is None:
                    log(f"❌ Cannot get market price - aborting trade", "ERROR")
                    log_signal_attempt(side, "FAILED", "Cannot get market price")
                    return
                
                # Calculate lot size
                balance = get_account_balance()
                if balance <= 0:
                    log(f"❌ Invalid balance: ${balance:.2f} - aborting trade", "ERROR")
                    log_signal_attempt(side, "FAILED", f"Invalid balance: ${balance:.2f}")
                    return
                
                failsafe_sl = entry_price - FAILSAFE_SL_DISTANCE if side == "BUY" else entry_price + FAILSAFE_SL_DISTANCE
                log(f"🛡️ Failsafe SL distance: ${FAILSAFE_SL_DISTANCE:.5f}", "DEBUG")
                log(f"🛡️ Failsafe SL price: ${failsafe_sl:.5f}", "DEBUG")
                
                lot = calculate_lot_size(entry_price, failsafe_sl, balance)
                
                if lot <= 0:
                    log(f"❌ Invalid lot size: {lot} - aborting trade", "ERROR")
                    log_signal_attempt(side, "FAILED", f"Invalid lot size: {lot}")
                    return
                
                log(f"📊 Trade parameters: Entry=${entry_price:.5f} | SL=${failsafe_sl:.5f} | Lot={lot:.4f}", "INFO")
                
                # Open position
                ticket = open_position(side, lot)
                
                if ticket:
                    log(f"✅ POSITION OPENED: Ticket={ticket} | Side={side} | Entry=${entry_price:.5f} | Lot={lot:.4f}", "INFO")
                    log_signal_attempt(side, "SUCCESS", f"Ticket={ticket} | Entry=${entry_price:.5f}")
                    position_map[msg_id] = ticket
                    entry_prices[ticket] = entry_price
                    breakeven_activated[ticket] = False
                    log(f"   Stored in position_map: msg_id={msg_id} -> ticket={ticket}", "DEBUG")
                    
                    # Persist to signal queue
                    signal_queue.add_signal(
                        signal_type="TRADE",
                        side=side,
                        entry_price=entry_price,
                        sl_price=failsafe_sl,
                        lot_size=lot,
                        tp_levels={},  # will be updated from edits
                        msg_id=msg_id,
                        msg_text=text[:100]
                    )
                    signal_queue.mark_executed(msg_id)
                    
                    # Set failsafe SL
                    log(f"⏳ Waiting for MT5 to register position...", "DEBUG")
                    time.sleep(0.5)
                    set_stop_loss(ticket, failsafe_sl)
                    
                    # Set failsafe TP3 (pattern: entry ± 7 points)
                    failsafe_tp3_price = calculate_failsafe_tp3(entry_price, side)
                    failsafe_tp3[ticket] = failsafe_tp3_price
                    log(f"🎯 Failsafe TP3 distance: 7 points", "INFO")
                    log(f"🎯 Failsafe TP3 price: ${failsafe_tp3_price:.5f}", "DEBUG")
                    set_take_profit(ticket, failsafe_tp3_price)
                else:
                    log(f"❌ POSITION OPEN FAILED - returning", "ERROR")
                    log_signal_attempt(side, "FAILED", "Position open failed (see above for details)")
                
                return
            
            # Check for TP/SL update (if no signal found)
            sl = parse_stop_loss(text)
            tp_levels_parsed = parse_tp_levels(text)
            
            # Find which position to update (most recent open position)
            positions = mt5.positions_get(symbol=SYMBOL)
            if not positions:
                log(f"⚠️ No open positions to update", "WARN")
                return
            
            ticket = int(positions[-1].ticket)
            
            # Update SL if found
            if sl:
                can_update, reason = can_update_stop_loss(ticket, sl)
                if can_update:
                    set_stop_loss(ticket, sl)
                else:
                    log(f"⚠️ SL skipped: {reason}", "WARN")
            
            # Update TP levels if found
            if tp_levels_parsed:
                tp_levels[ticket] = tp_levels_parsed
                
                # Select the best valid TP with fallback logic (TP3 > TP2 > TP1)
                tp_num, tp_price = select_best_tp_with_fallback(ticket, tp_levels_parsed)
                if tp_num and tp_price:
                    log(f"✅ TP{tp_num} set (${tp_price:.5f})", "INFO")
                    set_take_profit(ticket, tp_price)
                else:
                    log(f"⚠️ TP levels invalid - waiting for valid values", "WARN")
        
        except Exception as e:
            log(f"❌ EXCEPTION in on_new_message: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
    
    @client.on(events.MessageEdited(chats=[CHANNEL_ID, TEST_CHANNEL_ID]))
    async def on_edit(event):
        """Handle edited messages from main or test channel"""
        try:
            text = extract_message_text(event.message).strip()
            msg_id = event.message.id
            channel_name = "[TEST]" if event.chat_id == TEST_CHANNEL_ID else "[MAIN]"
            
            log(f"📝 {channel_name} Edited message (ID: {msg_id}): {text[:80]}", "INFO")
            
            # CHECK FOR TP3 HIT - SAFETY EXIT (highest priority)
            if is_tp3_hit_message(text):
                log("=" * 70, "INFO")
                log("🚨 TP3 HIT DETECTED (EDIT) - SAFETY EXIT!", "INFO")
                log("=" * 70, "INFO")
                
                # Find and close all open positions for this symbol
                positions = mt5.positions_get(symbol=SYMBOL)
                if positions:
                    closed_count = 0
                    for p in positions:
                        if p.magic == MAGIC:
                            log(f"🎯 Closing position: Ticket {p.ticket} | Volume {p.volume:.3f}", "INFO")
                            if close_position(p.ticket):
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
            
            # Find position for this message
            if msg_id not in position_map:
                log(f"⚠️ Message ID {msg_id} not in position_map - checking for recent positions", "WARN")
                log(f"   Current position_map keys: {list(position_map.keys())}", "DEBUG")
                
                # Fallback: use most recent position
                positions = mt5.positions_get(symbol=SYMBOL)
                if not positions:
                    log(f"⚠️ No open positions at all - ignoring edit", "WARN")
                    return
                
                ticket = int(positions[-1].ticket)
                log(f"   Using most recent position: ticket {ticket}", "DEBUG")
            else:
                ticket = position_map[msg_id]
                log(f"   Found mapped ticket: {ticket}", "DEBUG")
            
            # Verify position still exists
            pos = mt5.positions_get(ticket=ticket)
            if not pos:
                log(f"❌ Position {ticket} not found - may have been closed", "WARN")
                return
            
            # Update SL if present
            sl = parse_stop_loss(text)
            if sl:
                can_update, reason = can_update_stop_loss(ticket, sl)
                if can_update:
                    set_stop_loss(ticket, sl)
                else:
                    log(f"⚠️ SL skipped: {reason}", "WARN")
            
            # Update TP levels if present
            tp_levels_parsed = parse_tp_levels(text)
            if tp_levels_parsed:
                tp_levels[ticket] = tp_levels_parsed
                
                # Select the best valid TP with fallback logic (TP3 > TP2 > TP1)
                tp_num, tp_price = select_best_tp_with_fallback(ticket, tp_levels_parsed)
                if tp_num and tp_price:
                    log(f"✅ TP{tp_num} set (${tp_price:.5f})", "INFO")
                    set_take_profit(ticket, tp_price)
                else:
                    log(f"⚠️ TP levels invalid - waiting for valid values", "WARN")
        
        except Exception as e:
            log(f"❌ EXCEPTION in on_edit: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
    
    # Monitor positions for breakeven activation
    async def monitor_breakeven():
        """Check if TP1 has been hit and move SL to breakeven"""
        log("🔄 Breakeven monitor started", "INFO")
        
        while True:
            try:
                positions = mt5.positions_get(symbol=SYMBOL)
                
                if not positions:
                    await asyncio.sleep(2)
                    continue
                
                for pos in positions:
                    ticket = int(pos.ticket)
                    
                    # Skip if not in our tracking
                    if ticket not in entry_prices:
                        continue
                    
                    # Skip if breakeven already activated
                    if breakeven_activated.get(ticket, False):
                        continue
                    
                    # Skip if no TP1 stored
                    if ticket not in tp_levels or 1 not in tp_levels[ticket]:
                        continue
                    
                    tp1 = tp_levels[ticket][1]
                    entry = entry_prices[ticket]
                    side = get_position_side(ticket)
                    
                    if side is None:
                        continue
                    
                    # Get current price
                    tick = mt5.symbol_info_tick(SYMBOL)
                    if tick is None:
                        continue
                    
                    current = float(tick.bid if side == "SELL" else tick.ask)
                    
                    # Check if TP1 crossed
                    if price_crossed_tp1(ticket, tp1, current):
                        log(f"🎯 TP1 HIT! Moving SL to breakeven (${entry:.5f})", "INFO")
                        set_stop_loss(ticket, entry)
                        breakeven_activated[ticket] = True
                
                await asyncio.sleep(2)  # Check every 2 seconds
            
            except Exception as e:
                log(f"⚠️ Exception in monitor_breakeven: {str(e)}", "ERROR")
    
    # Signal log printer for debugging - minimal logging
    async def print_signal_log():
        """Print signal log occasionally for debugging"""
        while True:
            try:
                await asyncio.sleep(60)  # Check every 60 seconds
                # Only log if there were recent failed attempts
                if signal_log and any(e['result'] == 'FAILED' for e in signal_log[-5:]):
                    recent = [f"{e['signal']}/{e['result']}" for e in signal_log[-3:]]
                    log(f"📋 Recent attempts: {recent}", "INFO")
            except Exception as e:
                log(f"⚠️ Exception in print_signal_log: {str(e)}", "ERROR")
    
    # Start monitoring in background
    asyncio.create_task(monitor_breakeven())
    asyncio.create_task(print_signal_log())
    asyncio.create_task(cleanup_and_report())
    log("✅ Background tasks started", "INFO")
    
    # Connect and run
    log("📡 Connecting to Telegram...", "INFO")
    await client.start()
    log("✅ Telegram connected!", "INFO")
    log("=" * 70, "INFO")
    log("🎯 BOT READY - LISTENING FOR SIGNALS", "INFO")
    log("=" * 70, "INFO")
    
    # Replay pending signals from previous session if any
    await replay_pending_signals(client)
    
    # Run the Telegram client and recover from unknown TLObject errors by reconnecting.
    try:
        while True:
            try:
                await client.run_until_disconnected()
                break
            except KeyboardInterrupt:
                log("⏹️ Keyboard interrupt received - shutting down", "INFO")
                break
            except TypeNotFoundError as e:
                # This happens when Telethon encounters an unknown/unsupported TL constructor
                log("⚠️ Telethon TypeNotFoundError - unknown TLObject received. Attempting reconnect...", "ERROR")
                log(f"   {str(e)}", "DEBUG")
                import traceback
                log(f"   {traceback.format_exc()}", "DEBUG")
                
                # Record reconnection event
                reconnect_monitor.record_reconnect("TypeNotFoundError", str(e)[:50])
                
                # Check if we should rotate session
                error_count = reconnect_monitor.error_counters.get("TypeNotFoundError", 0)
                if session_manager.should_rotate("tlnotfound", error_count):
                    log("🔄 Rotating session file due to repeated TLObject errors", "INFO")
                    session_manager.rotate_session()
                    # Update client to use new session file
                    client = TelegramClient(session_manager.get_current_session_file(), API_ID, API_HASH)

                # Try to safely reconnect the client (don't shutdown MT5)
                try:
                    await client.disconnect()
                except Exception as ex:
                    log(f"⚠️ Error while disconnecting client: {ex}", "WARN")

                await asyncio.sleep(5)

                try:
                    await client.start()
                    log("✅ Telegram client restarted after TypeNotFoundError", "INFO")
                except Exception as ex2:
                    log(f"❌ Failed to restart Telegram client: {ex2}", "ERROR")
                    await asyncio.sleep(5)
                # loop will retry run_until_disconnected
                continue
            except Exception as e:
                log(f"❌ Unexpected error in main loop: {str(e)}", "ERROR")
                import traceback
                log(f"   {traceback.format_exc()}", "ERROR")
                break
    finally:
        log("🛑 Bot shutting down...", "INFO")
        mt5.shutdown()
        log("✅ MT5 shutdown complete", "INFO")


if __name__ == "__main__":
    asyncio.run(main())
