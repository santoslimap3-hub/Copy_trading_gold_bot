#!/usr/bin/env python3
"""
Random Bot - Automated trading with internally generated signals
Generates random BUY/SELL signals and trades them directly without Telegram
"""

import time
import math
import random
import argparse
import sys
import os
import traceback
from datetime import datetime
from typing import Optional, Tuple, Dict

import MetaTrader5 as mt5
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

# Fix Windows encoding for emoji support
if sys.platform == "win32":
    os.system("chcp 65001 > nul")

console = Console(force_terminal=True, legacy_windows=False)

# Enhanced logging
def log_section(title: str, style: str = "bold cyan"):
    """Print a section separator"""
    console.print()
    console.print(f"{'='*60}", style=style)
    console.print(f"  {title}", style=style)
    console.print(f"{'='*60}", style=style)

# ===================== CONFIG =====================
SYMBOL = "XAUUSD"
MAGIC = 888  # Different from script.py to avoid conflicts
DEVIATION = 20

# Risk sizing
RISK_PCT = 0.05
ASSUMED_SL_PRICE_DIST = 8.0
MAX_LOT = 1.0

# Safety limits
USE_EQUITY_FOR_RISK = True
HARD_MAX_LOSS_MONEY = 1000
HARD_MAX_LOSS_PCT = 0.1
HARD_MIN_SL_DIST = 1.0
SANITY_MAX_SL_DIST = 50.0
SKIP_IF_MINLOT_EXCEEDS_CAP = True

# Breakeven strategy
TARGET_TP_LEVEL = 3
BREAKEVEN_ACTIVATION_TP = 1

# Signal generation parameters
SIGNAL_RANGE = 2.50
SIGNAL_TP_STEP = 2.00
SIGNAL_TP_COUNT = 5
SIGNAL_SL_DIST = 3.00

# Monitoring
POSITION_CHECK_INTERVAL = 0.5  # seconds
BREAKEVEN_CHECK_INTERVAL = 0.5  # seconds

DEBUG = True

# ===================== STATE =====================
ticket_tp_levels: Dict[int, Dict[int, float]] = {}
ticket_entry_prices: Dict[int, float] = {}
ticket_breakeven_activated: Dict[int, bool] = {}


# ===================== UTILITIES =====================
def dbg(msg: str, style: str = "cyan"):
    if DEBUG:
        console.print(f"[{now_ts()}] {msg}", style=style)


def dbg_success(msg: str):
    dbg(msg, style="bold green")


def dbg_error(msg: str):
    dbg(msg, style="bold red")


def dbg_warning(msg: str):
    dbg(msg, style="bold yellow")


def dbg_info(msg: str):
    dbg(msg, style="cyan")


def dbg_step(step: int, msg: str):
    """Log a numbered step"""
    dbg(f"[STEP {step}] {msg}", style="bold blue")


def banner(title: str, style: str = "bold yellow on dark_blue"):
    panel = Panel(title, style=style, expand=False, padding=(1, 2))
    console.print()
    console.print(panel)


def fmt(x, nd=2):
    try:
        return f"{float(x):.{int(nd)}f}"
    except Exception:
        return str(x)


def now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def dump_account():
    acc = mt5.account_info()
    if acc is None:
        dbg_error("Failed to get account info")
        return None
    
    table = Table(title="💰 ACCOUNT INFO", style="bold cyan")
    table.add_column("Field", style="magenta")
    table.add_column("Value", style="green")
    
    table.add_row("Login", str(acc.login))
    table.add_row("Server", str(getattr(acc, 'server', 'N/A')))
    table.add_row("Balance", f"${fmt(acc.balance, 2)}")
    table.add_row("Equity", f"${fmt(acc.equity, 2)}")
    table.add_row("Margin", f"${fmt(acc.margin, 2)}")
    table.add_row("Free Margin", f"${fmt(acc.margin_free, 2)}")
    table.add_row("Profit/Loss", f"${fmt(acc.profit, 2)}")
    table.add_row("Currency", str(acc.currency))
    
    console.print(table)
    return acc


def dump_market_snapshot(symbol: str):
    """Dump detailed market information"""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        dbg_error(f"Failed to get tick for {symbol}")
        return None
    
    dbg_info(f"🔹 MARKET SNAPSHOT {symbol}:")
    dbg_info(f"  • Bid: ${fmt(tick.bid)}")
    dbg_info(f"  • Ask: ${fmt(tick.ask)}")
    dbg_info(f"  • Spread: {fmt(tick.ask - tick.bid)} points")
    dbg_info(f"  • Mid: ${fmt((tick.bid + tick.ask) / 2.0)}")
    dbg_info(f"  • Last: ${fmt(tick.last)}")
    dbg_info(f"  • Time: {datetime.fromtimestamp(tick.time)}")
    
    return tick


def dump_symbol_specs(symbol: str):
    """Dump detailed symbol specifications"""
    si = mt5.symbol_info(symbol)
    if si is None:
        dbg_error(f"Failed to get specs for {symbol}")
        return None
    
    dbg_info(f"⚙️ SYMBOL SPECS {symbol}:")
    dbg_info(f"  • Digits: {si.digits}")
    dbg_info(f"  • Point: {si.point}")
    dbg_info(f"  • Volume Min: {si.volume_min}")
    dbg_info(f"  • Volume Max: {si.volume_max}")
    dbg_info(f"  • Volume Step: {si.volume_step}")
    dbg_info(f"  • Trade Stops Level: {getattr(si, 'trade_stops_level', 'N/A')}")
    dbg_info(f"  • Contract Size: {getattr(si, 'trade_contract_size', 'N/A')}")
    dbg_info(f"  • Visible: {si.visible}")
    
    return si


def dump_positions(symbol: str):
    """Dump all positions for symbol"""
    poss = mt5.positions_get(symbol=symbol) or []
    
    if not poss:
        dbg_info(f"No positions for {symbol}")
        return
    
    table = Table(title=f"📊 POSITIONS ({symbol})", style="bold cyan")
    table.add_column("Ticket", style="magenta")
    table.add_column("Type", style="yellow")
    table.add_column("Volume", style="cyan")
    table.add_column("Entry", style="cyan")
    table.add_column("SL", style="red")
    table.add_column("TP", style="green")
    table.add_column("P&L", style="white")
    
    for p in poss[:10]:
        side = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
        table.add_row(
            str(p.ticket),
            side,
            fmt(p.volume),
            fmt(p.price_open),
            fmt(p.sl) if p.sl > 0 else "—",
            fmt(p.tp) if p.tp > 0 else "—",
            fmt(p.profit),
        )
    
    console.print(table)


# ===================== MT5 HELPERS =====================
def mt5_connect():
    if not mt5.initialize():
        dbg(f"❌ MT5 initialize failed: {mt5.last_error()}", style="bold red")
        raise SystemExit(1)

    info = mt5.account_info()
    if info is None:
        dbg("❌ Failed to get account info", style="bold red")
        raise SystemExit(1)

    term = mt5.terminal_info()
    if term is None:
        dbg("❌ Failed to get terminal info", style="bold red")
        raise SystemExit(1)

    if not getattr(term, "trade_allowed", False):
        dbg("⚠️ Trade operations are blocked in terminal settings!", style="bold red")
        dbg("  • Open MetaTrader 5", style="yellow")
        dbg("  • Tools → Options → Expert Advisors", style="yellow")
        dbg("  • Check 'Allow automated trading'", style="yellow")
        raise SystemExit(1)

    banner("✅ MT5 CONNECTED", style="bold white on green")
    dbg(f"Account: login={info.login} | server={getattr(info, 'server', None)} | currency={info.currency}", style="green")
    dump_account()
    ensure_symbol(SYMBOL)


def ensure_symbol(symbol: str, retries: int = 8, delay: float = 0.5):
    for i in range(retries):
        if mt5.symbol_select(symbol, True):
            si = mt5.symbol_info(symbol)
            if si is not None:
                dbg(f"✓ Symbol {symbol} available", style="green")
                return si
        time.sleep(delay)
    
    dbg(f"❌ Symbol not available after retries: {symbol}", style="bold red")
    raise RuntimeError(f"Symbol not available: {symbol}")


def retcode_is_ok(retcode: int) -> bool:
    return retcode in {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED, mt5.TRADE_RETCODE_NO_CHANGES}


def market_order(symbol: str, lot: float, side: str) -> Optional[int]:
    """Open a market order and return ticket or None"""
    dbg_step(1, f"Opening {side} order for {fmt(lot)} lots")
    
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        dbg_error("Failed to get market tick")
        return None
    
    dbg_info(f"Current market: Bid=${fmt(tick.bid)} Ask=${fmt(tick.ask)}")

    side = side.upper()
    if side == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
        dbg_info(f"BUY order will use ASK price: ${fmt(price)}")
    elif side == "SELL":
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
        dbg_info(f"SELL order will use BID price: ${fmt(price)}")
    else:
        dbg_error(f"Invalid side: {side}")
        return None

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lot),
        "type": order_type,
        "price": float(price),
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": "random_signal",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    dbg_info(f"Order request: {request}")
    dbg_info(f"📤 Sending {side} order...")
    dbg_info(f"  • Symbol: {symbol}")
    dbg_info(f"  • Volume: {fmt(lot)} lots")
    dbg_info(f"  • Price: ${fmt(price)}")
    dbg_info(f"  • Deviation: {DEVIATION}")
    dbg_info(f"  • Magic: {MAGIC}")
    
    r = mt5.order_send(request)
    
    if r is None:
        dbg_error(f"order_send() returned None!")
        dbg_error(f"Last MT5 error: {mt5.last_error()}")
        return None
    
    rc = getattr(r, "retcode", -1)
    ticket = getattr(r, "order", None)
    
    dbg_info(f"Order response received:")
    dbg_info(f"  • Retcode: {rc}")
    dbg_info(f"  • Ticket: {ticket}")
    dbg_info(f"  • Deal: {getattr(r, 'deal', 'N/A')}")
    dbg_info(f"  • Comment: {getattr(r, 'comment', 'N/A')}")
    
    if retcode_is_ok(rc) and ticket:
        dbg_success(f"✅ ORDER OPENED: Ticket={ticket} | Type={side} | Lot={fmt(lot)} | Price=${fmt(price)}")
        return ticket
    else:
        dbg_error(f"❌ ORDER FAILED: retcode={rc}")
        return None


def set_sltp(symbol: str, ticket: int, sl: float, tp: float) -> bool:
    """Update SL/TP for a position"""
    dbg_step(2, f"Setting SL/TP for ticket {ticket}")
    dbg_info(f"  • SL: ${fmt(sl)}")
    dbg_info(f"  • TP: ${fmt(tp)}")
    
    # Validate SL and TP
    pos = mt5.positions_get(ticket=ticket)
    if pos:
        p = pos[0]
        entry_price = p.price_open
        side = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
        
        dbg_info(f"Position details:")
        dbg_info(f"  • Entry price: ${fmt(entry_price)}")
        dbg_info(f"  • Side: {side}")
        
        if side == "BUY":
            if sl >= entry_price:
                dbg_warning(f"⚠️ SL (${fmt(sl)}) >= Entry (${fmt(entry_price)}) for BUY - invalid!")
            if tp <= entry_price:
                dbg_warning(f"⚠️ TP (${fmt(tp)}) <= Entry (${fmt(entry_price)}) for BUY - invalid!")
        else:  # SELL
            if sl <= entry_price:
                dbg_warning(f"⚠️ SL (${fmt(sl)}) <= Entry (${fmt(entry_price)}) for SELL - invalid!")
            if tp >= entry_price:
                dbg_warning(f"⚠️ TP (${fmt(tp)}) >= Entry (${fmt(entry_price)}) for SELL - invalid!")
    
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": ticket,
        "sl": float(sl),
        "tp": float(tp),
        "magic": MAGIC,
        "comment": "set_sltp",
    }
    
    dbg_info(f"Sending SLTP request: {request}")
    r = mt5.order_send(request)
    rc = getattr(r, "retcode", -1) if r is not None else -1
    
    dbg_info(f"SLTP response: retcode={rc}")
    
    if retcode_is_ok(rc):
        dbg_success(f"✅ SL/TP UPDATED: Ticket={ticket} | SL=${fmt(sl)} | TP=${fmt(tp)}")
        return True
    else:
        dbg_error(f"❌ SLTP UPDATE FAILED: retcode={rc}")
        if r:
            dbg_error(f"  Comment: {getattr(r, 'comment', 'N/A')}")
        return False


def get_min_stop_distance(symbol: str) -> float:
    si = mt5.symbol_info(symbol)
    if si is None:
        return 0.02
    points = getattr(si, "trade_stops_level", 0) or 0
    min_dist = points * si.point
    min_dist = max(min_dist, 2 * si.point)
    return float(min_dist)


def round_down_to_step(x: float, step: float) -> float:
    return math.floor(x / step) * step


def calc_max_lot_for_risk(
    symbol: str,
    side: str,
    entry_price: float,
    sl_price_dist: float,
    capital: float,
    risk_pct: float,
) -> float:
    dbg_step(3, "Calculating lot size")
    dbg_info(f"  • Capital: ${fmt(capital, 2)}")
    dbg_info(f"  • Risk %: {fmt(risk_pct*100)}%")
    dbg_info(f"  • Entry price: ${fmt(entry_price)}")
    dbg_info(f"  • SL distance: ${fmt(sl_price_dist)}")
    
    si = mt5.symbol_info(symbol)
    if si is None:
        dbg_error(f"Failed to get symbol info for {symbol}")
        return 0.01
    
    risk_money = float(capital) * float(risk_pct)
    dbg_info(f"  • Risk money: ${fmt(risk_money, 2)}")
    
    side = side.upper()
    if side == "BUY":
        sl_price = entry_price - sl_price_dist
        dbg_info(f"  • BUY: SL = Entry - Distance = ${fmt(entry_price)} - ${fmt(sl_price_dist)} = ${fmt(sl_price)}")
    else:
        sl_price = entry_price + sl_price_dist
        dbg_info(f"  • SELL: SL = Entry + Distance = ${fmt(entry_price)} + ${fmt(sl_price_dist)} = ${fmt(sl_price)}")
    
    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    loss_for_1lot = mt5.order_calc_profit(order_type, symbol, 1.0, float(entry_price), float(sl_price))
    
    dbg_info(f"  • Loss for 1.0 lot if SL hits: ${fmt(loss_for_1lot, 2)}")
    
    if loss_for_1lot is None or loss_for_1lot >= 0:
        dbg_warning(f"⚠️ Loss calculation invalid, using minimum lot")
        return 0.01
    
    loss_for_1lot = abs(loss_for_1lot)
    raw_lot = risk_money / loss_for_1lot
    dbg_info(f"  • Raw lot (before constraints): {fmt(raw_lot)}")
    
    vmin = float(getattr(si, "volume_min", 0.01) or 0.01)
    vmax = float(getattr(si, "volume_max", 100.0) or 100.0)
    vstep = float(getattr(si, "volume_step", 0.01) or 0.01)
    
    dbg_info(f"  • Volume constraints: min={vmin} max={vmax} step={vstep}")
    
    lot = max(vmin, min(vmax, raw_lot))
    dbg_info(f"  • After min/max clamp: {fmt(lot)}")
    
    lot = round_down_to_step(lot, vstep)
    dbg_info(f"  • After step rounding: {fmt(lot)}")
    
    lot = min(lot, float(MAX_LOT))
    dbg_info(f"  • After MAX_LOT cap: {fmt(lot)}")
    
    if lot < vmin:
        dbg_warning(f"⚠️ Final lot {fmt(lot)} < minimum {vmin}, using minimum")
        lot = vmin
    
    dbg_success(f"✅ FINAL LOT SIZE: {fmt(lot)}")
    return float(lot)


# ===================== SIGNAL GENERATION =====================
def build_signal(side: str, mid: float, range_size: float, tp_step: float, tp_count: int, sl_dist: float) -> str:
    """Generate a signal in the same format as generate_test_signal.py"""
    side = side.upper()

    entry_high = mid + range_size
    entry_low = mid - range_size

    if side == "BUY":
        sl = entry_low - sl_dist
        tp_start = entry_high + tp_step
        tp_sign = 1
    else:
        sl = entry_high + sl_dist
        tp_start = entry_low - tp_step
        tp_sign = -1

    tps = [tp_start + tp_sign * tp_step * i for i in range(tp_count)]

    lines = [
        "EGN GOLD",
        f"XAU USD {side} NOW",
        "",
        f"{fmt(entry_high)} - {fmt(entry_low)}",
        "",
    ]

    for idx, tp in enumerate(tps, start=1):
        lines.append(f"TP{idx} {fmt(tp)}")

    lines.extend([
        "",
        f"SL {fmt(sl)}",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ])

    return "\n".join(lines)


def generate_random_signal() -> str:
    """Generate a random signal based on current market price"""
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        raise RuntimeError("Failed to get market tick")
    
    mid = (tick.bid + tick.ask) / 2.0
    side = random.choice(["BUY", "SELL"])
    
    signal = build_signal(
        side=side,
        mid=mid,
        range_size=SIGNAL_RANGE,
        tp_step=SIGNAL_TP_STEP,
        tp_count=SIGNAL_TP_COUNT,
        sl_dist=SIGNAL_SL_DIST,
    )
    
    return signal


# ===================== PARSING =====================
def parse_side(text: str) -> Optional[str]:
    t = (text or "").upper()
    if "BUY NOW" in t:
        return "BUY"
    if "SELL NOW" in t:
        return "SELL"
    return None


def parse_all_tp_levels(text: str) -> Dict[int, float]:
    """Parse all TP levels from signal text"""
    import re
    t = text or ""
    tp_levels = {}
    
    dbg_info("Parsing TP levels...")
    matches = re.finditer(r"\bTP\s*(\d+)\b[^0-9]*([0-9]+(?:\.[0-9]+)?)", t, re.IGNORECASE)
    for match in matches:
        level = int(match.group(1))
        price = float(match.group(2))
        tp_levels[level] = price
        dbg_info(f"  • TP{level}: ${fmt(price)}")
    
    if not tp_levels:
        dbg_error("No TP levels found in signal!")
    else:
        dbg_success(f"✅ Found {len(tp_levels)} TP levels")
    
    return tp_levels


def parse_tp1_sl(text: str) -> Tuple[Optional[float], Optional[float]]:
    """Parse TP1 and SL from signal text"""
    import re
    t = text or ""
    tp1 = None
    sl = None

    dbg_info("Parsing TP1 and SL...")
    
    m = re.search(r"\bTP\s*1\b[^0-9]*([0-9]+(?:\.[0-9]+)?)", t, re.IGNORECASE)
    if m:
        tp1 = float(m.group(1))
        dbg_info(f"  • TP1: ${fmt(tp1)}")
    else:
        dbg_error("TP1 not found!")

    m = re.search(r"SL[^0-9]*([0-9]+(?:\.[0-9]+)?)", t, re.IGNORECASE)
    if m:
        sl = float(m.group(1))
        dbg_info(f"  • SL: ${fmt(sl)}")
    else:
        dbg_error("SL not found!")

    return tp1, sl


def get_target_tp(tp_levels: Dict[int, float]) -> Optional[float]:
    """Get TP3 or highest available TP"""
    if not tp_levels:
        return None
    
    if TARGET_TP_LEVEL in tp_levels:
        return tp_levels[TARGET_TP_LEVEL]
    
    max_level = max(tp_levels.keys())
    return tp_levels[max_level]


def price_passes_tp1(side: str, current_price: float, entry_price: float, tp1_price: float) -> bool:
    """Check if price has passed TP1"""
    side = side.upper()
    if side == "BUY":
        return current_price >= tp1_price
    elif side == "SELL":
        return current_price <= tp1_price
    return False


# ===================== BREAKEVEN MONITORING =====================
def activate_breakeven_if_needed(ticket: int, side: str):
    """Move SL to breakeven when TP1 is passed"""
    if ticket_breakeven_activated.get(ticket, False):
        return
    
    if ticket not in ticket_tp_levels or not ticket_tp_levels[ticket]:
        dbg_info(f"No TP levels stored for ticket {ticket}")
        return
    
    tp1_price = ticket_tp_levels[ticket].get(1)
    entry_price = ticket_entry_prices.get(ticket)
    
    if tp1_price is None or entry_price is None:
        dbg_warning(f"TP1 or entry price missing: TP1={tp1_price} Entry={entry_price}")
        return
    
    # Get current price
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        dbg_error("Failed to get market tick for breakeven check")
        return
    
    current_price = tick.ask if side == "BUY" else tick.bid
    
    # Check if TP1 passed
    if not price_passes_tp1(side, current_price, entry_price, tp1_price):
        return
    
    dbg_success(f"🎯 TP1 PASSED! Ticket {ticket} | Current: ${fmt(current_price)} | TP1: ${fmt(tp1_price)}")
    dbg_info(f"Now activating breakeven SL at entry price ${fmt(entry_price)}")
    
    # Get position details
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        dbg_error(f"Position {ticket} not found during breakeven activation")
        return
    
    p = pos[0]
    current_tp = float(p.tp) if p.tp > 0 else None
    current_sl = float(p.sl) if p.sl > 0 else None
    
    dbg_info(f"Current position state:")
    dbg_info(f"  • Current SL: ${fmt(current_sl)}")
    dbg_info(f"  • Current TP: ${fmt(current_tp)}")
    
    if current_tp is None:
        dbg_error(f"No TP set for ticket {ticket}")
        return
    
    # Move SL to breakeven
    breakeven_sl = entry_price
    dbg_info(f"Attempting to move SL from ${fmt(current_sl)} to breakeven at ${fmt(breakeven_sl)}")
    
    if set_sltp(SYMBOL, ticket, breakeven_sl, current_tp):
        ticket_breakeven_activated[ticket] = True
        dbg_success(f"✅ BREAKEVEN SL ACTIVATED for ticket {ticket}")
    else:
        dbg_error(f"❌ Failed to activate breakeven SL for ticket {ticket}")


# ===================== TRADE MONITORING =====================
def wait_for_position_close(ticket: int) -> Dict:
    """Wait for a position to close and return trade stats"""
    side = None
    entry_price = None
    max_profit = 0
    max_loss = 0
    check_count = 0
    
    dbg_step(4, f"Monitoring position {ticket} until close...")
    
    while True:
        check_count += 1
        pos = mt5.positions_get(ticket=ticket)
        
        if not pos:
            # Position closed
            dbg_success(f"✅ Position {ticket} CLOSED (check #{check_count})")
            
            # Get final details from order history
            deals = mt5.history_deals_get(ticket=ticket)
            if deals:
                close_deal = None
                open_deal = None
                
                dbg_info("Trade deals:")
                for deal in deals:
                    dbg_info(f"  • Deal: {deal.ticket} | Entry: {deal.entry} | Price: ${fmt(deal.price)} | Profit: ${fmt(deal.profit)}")
                    if deal.entry == mt5.DEAL_ENTRY_IN:
                        open_deal = deal
                    elif deal.entry == mt5.DEAL_ENTRY_OUT:
                        close_deal = deal
                
                if close_deal and open_deal:
                    profit = close_deal.profit
                    dbg_success(f"📊 TRADE CLOSED: Profit/Loss = ${fmt(profit, 2)}")
                    
                    return {
                        "ticket": ticket,
                        "side": side,
                        "entry_price": open_deal.price,
                        "close_price": close_deal.price,
                        "profit": profit,
                        "closed_at": datetime.fromtimestamp(close_deal.time),
                    }
            
            return {
                "ticket": ticket,
                "side": side,
                "entry_price": entry_price,
                "close_price": None,
                "profit": 0,
                "closed_at": datetime.now(),
            }
        
        # Position still open
        p = pos[0]
        
        if side is None:
            side = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
            entry_price = p.price_open
            dbg_info(f"Position opened:")
            dbg_info(f"  • Side: {side}")
            dbg_info(f"  • Entry: ${fmt(entry_price)}")
        
        # Track max profit/loss
        profit = p.profit
        if profit > max_profit:
            max_profit = profit
        if profit < max_loss:
            max_loss = profit
        
        # Log current position state every 10 checks
        if check_count % 10 == 0:
            dbg_info(f"Position {ticket} status (check #{check_count}):")
            dbg_info(f"  • Current P&L: ${fmt(profit, 2)}")
            dbg_info(f"  • Max Profit: ${fmt(max_profit, 2)}")
            dbg_info(f"  • Max Loss: ${fmt(max_loss, 2)}")
            dbg_info(f"  • SL: ${fmt(p.sl)} | TP: ${fmt(p.tp)}")
        
        # Try to activate breakeven
        activate_breakeven_if_needed(ticket, side)
        
        time.sleep(POSITION_CHECK_INTERVAL)


# ===================== MAIN BOT LOOP =====================
def run_bot():
    """Main bot loop - generate signals and trade them"""
    try:
        mt5_connect()
        
        log_section("🤖 RANDOM BOT INITIALIZED", "bold white on green")
        dbg_info(f"Symbol: {SYMBOL}")
        dbg_info(f"Magic: {MAGIC}")
        dbg_info(f"Risk: {RISK_PCT*100}% per trade")
        dbg_info(f"Max LOT: {MAX_LOT}")
        
        trade_count = 0
        total_profit = 0
        
        while True:
            trade_count += 1
            log_section(f"📊 TRADE #{trade_count}", "bold cyan")
            
            try:
                # Generate signal
                dbg_step(0, "GENERATING SIGNAL")
                dbg_info("🎲 Creating random BUY/SELL signal from current market...")
                signal_text = generate_random_signal()
                print("\n" + "="*60)
                print(signal_text)
                print("="*60 + "\n")
                
                # Parse signal
                dbg_step(1, "PARSING SIGNAL")
                side = parse_side(signal_text)
                if not side:
                    dbg_error("Failed to parse side (BUY/SELL)")
                    time.sleep(2)
                    continue
                dbg_success(f"✅ Side: {side}")
                
                tp_levels = parse_all_tp_levels(signal_text)
                if not tp_levels:
                    dbg_error("No TP levels found")
                    time.sleep(2)
                    continue
                
                tp1, sl = parse_tp1_sl(signal_text)
                if not tp1 or not sl:
                    dbg_error(f"Failed to parse TP1 or SL: TP1={tp1}, SL={sl}")
                    time.sleep(2)
                    continue
                dbg_success(f"✅ TP1: ${fmt(tp1)}, SL: ${fmt(sl)}")
                
                target_tp = get_target_tp(tp_levels)
                if not target_tp:
                    dbg_error("No target TP found")
                    time.sleep(2)
                    continue
                dbg_success(f"✅ Target TP{TARGET_TP_LEVEL}: ${fmt(target_tp)}")
                
                # Get current market state
                dbg_step(2, "CHECKING MARKET")
                dump_market_snapshot(SYMBOL)
                
                tick = mt5.symbol_info_tick(SYMBOL)
                if tick is None:
                    dbg_error("Failed to get market tick")
                    time.sleep(2)
                    continue
                
                entry_price = tick.ask if side == "BUY" else tick.bid
                dbg_info(f"Entry price ({side}): ${fmt(entry_price)}")
                
                # Get account state
                dbg_step(3, "CHECKING ACCOUNT")
                acc = dump_account()
                if acc is None:
                    dbg_error("Failed to get account info")
                    time.sleep(2)
                    continue
                
                capital = acc.equity if USE_EQUITY_FOR_RISK else acc.balance
                dbg_info(f"Capital for risk calculation: ${fmt(capital, 2)}")
                
                # Calculate lot size
                dbg_step(4, "CALCULATING LOT SIZE")
                lot = calc_max_lot_for_risk(SYMBOL, side, entry_price, ASSUMED_SL_PRICE_DIST, capital, RISK_PCT)
                
                if lot <= 0:
                    dbg_error(f"Invalid lot size calculated: {lot}")
                    time.sleep(2)
                    continue
                
                # Open position
                dbg_step(5, "OPENING POSITION")
                ticket = market_order(SYMBOL, lot, side)
                if not ticket:
                    dbg_error("Failed to open position")
                    time.sleep(2)
                    continue
                
                # Store strategy state
                ticket_tp_levels[ticket] = tp_levels
                ticket_entry_prices[ticket] = entry_price
                ticket_breakeven_activated[ticket] = False
                dbg_success(f"✅ Position tracking initialized for ticket {ticket}")
                
                # Set TP and SL
                dbg_step(6, "SETTING SL/TP")
                time.sleep(0.5)  # Brief delay to ensure position exists
                if set_sltp(SYMBOL, ticket, sl, target_tp):
                    dbg_success(f"✅ SL/TP configured")
                else:
                    dbg_error(f"⚠️ Failed to set SL/TP (position may close with defaults)")
                
                # Display current state
                dbg_step(7, "POSITION STATE")
                time.sleep(0.5)
                dump_positions(SYMBOL)
                
                # Wait for position to close
                dbg_step(8, "MONITORING POSITION")
                result = wait_for_position_close(ticket)
                total_profit += result["profit"]
                
                # Log trade summary
                log_section("📊 TRADE SUMMARY", "bold green")
                dbg_info(f"Ticket: {result['ticket']}")
                dbg_info(f"Side: {result['side']}")
                dbg_info(f"Entry: ${fmt(result['entry_price'])}")
                dbg_info(f"Close: ${fmt(result['close_price'])} (if available)")
                dbg_info(f"Profit/Loss: ${fmt(result['profit'], 2)}")
                dbg_info(f"Closed: {result['closed_at']}")
                dbg_success(f"✅ Trade #{trade_count} completed")
                
                # Show cumulative
                dbg_success(f"💰 CUMULATIVE PROFIT: ${fmt(total_profit, 2)}")
                dbg_info(f"⏳ Waiting 5 seconds before next signal...")
                time.sleep(5)
            
            except Exception as e:
                dbg_error(f"❌ Error during trade #{trade_count}: {str(e)}")
                dbg_error(f"Traceback: {traceback.format_exc()}")
                time.sleep(5)
                continue
    
    except KeyboardInterrupt:
        log_section("🛑 BOT STOPPED BY USER", "bold red")
        dbg_warning("Keyboard interrupt received")
    
    except Exception as e:
        dbg_error(f"Fatal error in bot loop: {str(e)}")
        dbg_error(f"Traceback: {traceback.format_exc()}")
    
    finally:
        log_section("📊 FINAL SUMMARY", "bold cyan")
        dbg_info(f"Total Trades: {trade_count}")
        dbg_info(f"Total Profit: ${fmt(total_profit, 2)}")
        if trade_count > 0:
            avg_profit = total_profit / trade_count
            dbg_info(f"Average Profit per Trade: ${fmt(avg_profit, 2)}")
        dbg_info("Shutting down MT5...")
        mt5.shutdown()
        dbg_success("✅ Bot stopped cleanly")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Random Bot - Automated trading with generated signals")
    args = parser.parse_args()
    
    run_bot()
