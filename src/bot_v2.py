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
from typing import Optional, Dict, Tuple

# ===================== CONFIGURATION =====================
# Telegram
API_ID = 34597981
API_HASH = "2cd59609b6cacb56da261e43fdb897ea"
CHANNEL_ID = -1003349563414  # Main trading channel
TEST_CHANNEL_ID = -1003732211798  # Test channel for manual signals
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

# Track if breakeven has been activated: ticket -> bool
breakeven_activated: Dict[int, bool] = {}

# ===================== HELPERS =====================

def log(msg: str, level: str = "INFO"):
    """Print with timestamp and level"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


def ensure_mt5_connection():
    """Initialize MT5 if not connected"""
    log("📡 Attempting MT5 initialization...", "DEBUG")
    
    if not mt5.initialize():
        log(f"❌ MT5 initialization FAILED", "ERROR")
        log(f"   Last error: {mt5.last_error()}", "ERROR")
        sys.exit(1)
    
    log("✅ MT5 initialized successfully", "INFO")
    
    # Verify connection
    version = mt5.version()
    log(f"   MT5 Version: {version}", "DEBUG")
    
    account = mt5.account_info()
    if account:
        log(f"   Account: {account.login} | Server: {account.server} | Currency: {account.currency}", "DEBUG")
        log(f"   Balance: ${account.balance:.2f} | Equity: ${account.equity:.2f}", "DEBUG")
    else:
        log(f"⚠️ Could not retrieve account info", "WARN")


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
    log(f"📐 Calculating lot size...", "DEBUG")
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
    
    # Clamp to symbol limits
    lot = max(lot, vol_min)
    lot = min(lot, vol_max)
    lot = round(lot / vol_step) * vol_step  # Round to step size
    
    log(f"   ✅ Final lot size: {lot:.4f}", "DEBUG")
    return lot


def get_filling_mode() -> int:
    """Get the appropriate filling mode for the symbol"""
    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None:
        log(f"⚠️ Could not get symbol info, defaulting to IOC", "WARN")
        return mt5.ORDER_FILLING_IOC
    
    # Check what filling modes are supported
    filling = symbol_info.filling_mode
    
    # Try ORDER_FILLING_IOC first (most common for Forex/CFDs)
    if filling & 2 == 2:  # 2 = SYMBOL_FILLING_IOC
        log(f"   Using ORDER_FILLING_IOC", "DEBUG")
        return mt5.ORDER_FILLING_IOC
    # Try ORDER_FILLING_FOK
    elif filling & 1 == 1:  # 1 = SYMBOL_FILLING_FOK
        log(f"   Using ORDER_FILLING_FOK", "DEBUG")
        return mt5.ORDER_FILLING_FOK
    # Default to RETURN mode
    else:
        log(f"   Using ORDER_FILLING_RETURN", "DEBUG")
        return mt5.ORDER_FILLING_RETURN


def open_position(side: str, lot: float) -> Optional[int]:
    """
    Open market position. Returns ticket number or None.
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
        "comment": f"Signal {side}",
    }
    
    log(f"   Request: {request}", "DEBUG")
    
    result = mt5.order_send(request)
    
    log(f"   Response retcode: {result.retcode}", "DEBUG")
    log(f"   Response: {result}", "DEBUG")
    
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log(f"❌ Order FAILED - Retcode: {result.retcode}", "ERROR")
        log(f"   Error message: {result.comment}", "ERROR")
        log(f"   Last MT5 error: {mt5.last_error()}", "ERROR")
        return None
    
    ticket = result.order
    log(f"✅ Position opened successfully - Ticket: {ticket}", "INFO")
    return ticket


def close_position(ticket: int) -> bool:
    """Close an open position immediately at market price (TP3 HIT safety exit)"""
    log(f"🚨 Closing position {ticket} - TP3 HIT SAFETY EXIT", "INFO")
    
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
    
    price = float(tick.bid if side == "BUY" else tick.ask)
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": float(p.volume),
        "type": close_type,
        "position": ticket,
        "price": price,
        "magic": MAGIC,
        "comment": "TP3 HIT - SAFETY EXIT",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    log(f"   Closing {side} position: Ticket {ticket} | Volume {p.volume:.3f} | Price ${price:.5f}", "DEBUG")
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
    
    # Get current position to preserve TP
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
        "tp": current_tp,  # Preserve current TP
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

    # If breakeven is active, never allow SL to move back into loss.
    if breakeven_activated.get(ticket, False) and entry is not None:
        if side == "BUY" and sl_price < entry:
            return False, "breakeven protected (BUY)"
        if side == "SELL" and sl_price > entry:
            return False, "breakeven protected (SELL)"

    return True, "ok"


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


# ===================== TELEGRAM HANDLERS =====================

def init_telegram():
    """Create Telegram client"""
    return TelegramClient(SESSION_FILE, API_ID, API_HASH)


async def main():
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
                
                # Get current market price
                entry_price = get_market_price(side)
                if entry_price is None:
                    log(f"❌ Cannot get market price - aborting trade", "ERROR")
                    return
                
                # Calculate lot size
                balance = get_account_balance()
                if balance <= 0:
                    log(f"❌ Invalid balance: ${balance:.2f} - aborting trade", "ERROR")
                    return
                
                failsafe_sl = entry_price - FAILSAFE_SL_DISTANCE if side == "BUY" else entry_price + FAILSAFE_SL_DISTANCE
                log(f"🛡️ Failsafe SL distance: ${FAILSAFE_SL_DISTANCE:.5f}", "DEBUG")
                log(f"🛡️ Failsafe SL price: ${failsafe_sl:.5f}", "DEBUG")
                
                lot = calculate_lot_size(entry_price, failsafe_sl, balance)
                
                if lot <= 0:
                    log(f"❌ Invalid lot size: {lot} - aborting trade", "ERROR")
                    return
                
                log(f"📊 Trade parameters: Entry=${entry_price:.5f} | SL=${failsafe_sl:.5f} | Lot={lot:.4f}", "INFO")
                
                # Open position
                ticket = open_position(side, lot)
                
                if ticket:
                    log(f"✅ POSITION OPENED: Ticket={ticket} | Side={side} | Entry=${entry_price:.5f} | Lot={lot:.4f}", "INFO")
                    position_map[msg_id] = ticket
                    entry_prices[ticket] = entry_price
                    breakeven_activated[ticket] = False
                    log(f"   Stored in position_map: msg_id={msg_id} -> ticket={ticket}", "DEBUG")
                    
                    # Set failsafe SL
                    log(f"⏳ Waiting for MT5 to register position...", "DEBUG")
                    time.sleep(0.5)
                    set_stop_loss(ticket, failsafe_sl)
                else:
                    log(f"❌ POSITION OPEN FAILED - returning", "ERROR")
                
                return
            
            # Check for TP/SL update (if no signal found)
            log(f"🔍 Checking for TP/SL updates in message...", "DEBUG")
            sl = parse_stop_loss(text)
            tp_levels_parsed = parse_tp_levels(text)
            
            # Find which position to update (most recent open position)
            positions = mt5.positions_get(symbol=SYMBOL)
            if not positions:
                log(f"⚠️ No open positions to update - ignoring message", "WARN")
                return
            
            ticket = int(positions[-1].ticket)  # most recent
            log(f"🔄 Found {len(positions)} open position(s) | Will update ticket {ticket}", "DEBUG")
            
            # Update SL if found
            if sl:
                log(f"📊 Found SL in message: ${sl:.5f}", "DEBUG")
                can_update, reason = can_update_stop_loss(ticket, sl)
                if can_update:
                    set_stop_loss(ticket, sl)
                else:
                    log(f"⚠️ SL update skipped: {reason}", "WARN")
            else:
                log(f"⚠️ No SL found in message", "DEBUG")
            
            # Update TP levels if found
            if tp_levels_parsed:
                log(f"📊 Found {len(tp_levels_parsed)} TP level(s): {tp_levels_parsed}", "DEBUG")
                tp_levels[ticket] = tp_levels_parsed
                log(f"   Stored TP levels for ticket {ticket}", "DEBUG")
                
                # Set TP to TP3 if it exists
                if 3 in tp_levels_parsed:
                    tp3 = tp_levels_parsed[3]
                    log(f"🎯 TP3 found: ${tp3:.5f} - setting as target", "INFO")
                    set_take_profit(ticket, tp3)
                else:
                    log(f"⚠️ TP3 not in parsed levels - will wait for full message", "WARN")
            else:
                log(f"⚠️ No TP levels found in message", "DEBUG")
        
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
            log(f"🔍 Parsing edited message for SL...", "DEBUG")
            sl = parse_stop_loss(text)
            if sl:
                log(f"📊 Found SL in edit: ${sl:.5f}", "DEBUG")
                can_update, reason = can_update_stop_loss(ticket, sl)
                if can_update:
                    set_stop_loss(ticket, sl)
                else:
                    log(f"⚠️ SL update skipped: {reason}", "WARN")
            else:
                log(f"⚠️ No SL in edit message", "DEBUG")
            
            # Update TP levels if present
            log(f"🔍 Parsing edited message for TP levels...", "DEBUG")
            tp_levels_parsed = parse_tp_levels(text)
            if tp_levels_parsed:
                log(f"📊 Found {len(tp_levels_parsed)} TP level(s): {tp_levels_parsed}", "DEBUG")
                tp_levels[ticket] = tp_levels_parsed
                log(f"   Stored TP levels for ticket {ticket}", "DEBUG")
                
                if 3 in tp_levels_parsed:
                    tp3 = tp_levels_parsed[3]
                    log(f"🎯 TP3 found in edit: ${tp3:.5f} - updating", "INFO")
                    set_take_profit(ticket, tp3)
                else:
                    log(f"⚠️ TP3 not found in edit message", "WARN")
            else:
                log(f"⚠️ No TP levels in edit message", "DEBUG")
        
        except Exception as e:
            log(f"❌ EXCEPTION in on_edit: {str(e)}", "ERROR")
            import traceback
            log(f"   {traceback.format_exc()}", "ERROR")
    
    # Monitor positions for breakeven activation
    async def monitor_breakeven():
        """Check if TP1 has been hit and move SL to breakeven"""
        log("🔄 Breakeven monitor started", "INFO")
        check_count = 0
        
        while True:
            try:
                check_count += 1
                
                positions = mt5.positions_get(symbol=SYMBOL)
                
                if not positions:
                    # Only log every 10 checks to avoid spam
                    if check_count % 10 == 0:
                        log(f"⏳ Monitor check #{check_count}: No open positions", "DEBUG")
                    await asyncio.sleep(2)
                    continue
                
                log(f"🔍 Monitor check #{check_count}: Checking {len(positions)} position(s)", "DEBUG")
                
                for pos in positions:
                    ticket = int(pos.ticket)
                    
                    # Skip if not in our tracking
                    if ticket not in entry_prices:
                        log(f"   Ticket {ticket}: NOT in our tracking (entry_prices)", "DEBUG")
                        continue
                    
                    log(f"   Ticket {ticket}: Checking...", "DEBUG")
                    
                    # Skip if breakeven already activated
                    if breakeven_activated.get(ticket, False):
                        log(f"   Ticket {ticket}: Breakeven already activated", "DEBUG")
                        continue
                    
                    # Skip if no TP1 stored
                    if ticket not in tp_levels or 1 not in tp_levels[ticket]:
                        log(f"   Ticket {ticket}: No TP1 stored yet", "DEBUG")
                        continue
                    
                    tp1 = tp_levels[ticket][1]
                    entry = entry_prices[ticket]
                    side = get_position_side(ticket)
                    
                    if side is None:
                        log(f"   Ticket {ticket}: Position side is None (closed?)", "WARN")
                        continue
                    
                    # Get current price
                    tick = mt5.symbol_info_tick(SYMBOL)
                    if tick is None:
                        log(f"   Ticket {ticket}: Cannot get tick data", "WARN")
                        continue
                    
                    current = float(tick.bid if side == "SELL" else tick.ask)
                    
                    # Check if TP1 crossed
                    if price_crossed_tp1(ticket, tp1, current):
                        log(f"🎯 TP1 HIT! Ticket {ticket} | Current=${current:.5f} vs TP1=${tp1:.5f} ({side})", "INFO")
                        log(f"   >>> MOVING SL TO BREAKEVEN: ${entry:.5f}", "INFO")
                        set_stop_loss(ticket, entry)
                        breakeven_activated[ticket] = True
                    else:
                        log(f"   Ticket {ticket}: TP1 not yet reached | Current=${current:.5f} | TP1=${tp1:.5f}", "DEBUG")
                
                await asyncio.sleep(2)  # Check every 2 seconds
            
            except Exception as e:
                log(f"⚠️ Exception in monitor_breakeven: {str(e)}", "ERROR")
                import traceback
                log(f"   {traceback.format_exc()}", "ERROR")
                await asyncio.sleep(2)
    
    # Start monitoring in background
    log("🔄 Starting breakeven monitor background task...", "INFO")
    asyncio.create_task(monitor_breakeven())
    log("✅ Breakeven monitor background task created", "INFO")
    
    # Connect and run
    log("📡 Connecting to Telegram...", "INFO")
    await client.start()
    log("✅ Telegram connected!", "INFO")
    log("=" * 70, "INFO")
    log("🎯 BOT READY - LISTENING FOR SIGNALS", "INFO")
    log("=" * 70, "INFO")
    
    try:
        await client.run_until_disconnected()
    except KeyboardInterrupt:
        log("⏹️ Keyboard interrupt received - shutting down", "INFO")
    except Exception as e:
        log(f"❌ Unexpected error in main loop: {str(e)}", "ERROR")
        import traceback
        log(f"   {traceback.format_exc()}", "ERROR")
    finally:
        log("🛑 Bot shutting down...", "INFO")
        mt5.shutdown()
        log("✅ MT5 shutdown complete", "INFO")


if __name__ == "__main__":
    asyncio.run(main())
