import re
import time
import math
import argparse
import sys
import os
from typing import Optional, Tuple, Dict

from telethon import TelegramClient, events
import MetaTrader5 as mt5
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.style import Style

# Fix Windows encoding for emoji support
if sys.platform == "win32":
    os.system("chcp 65001 > nul")

console = Console(force_terminal=True, legacy_windows=False)

# ===================== TEST MODE =====================
TEST_MODE = False  # Will be set by command-line argument
TEST_CHANNEL_ID = None  # Set this to your test channel ID (find via find_channel.py)


# ===================== TELEGRAM CONFIG =====================
api_id = 34597981
api_hash = "2cd59609b6cacb56da261e43fdb897ea"
CHANNEL = -1003349563414  # EGN GOLD (Live Trading)
TEST_CHANNEL = -1003896390912  # TODO: Set to your test channel ID (use find_channel.py to find it)


# ===================== MT5 CONFIG =====================
SYMBOL = "XAUUSD"
MAGIC = 777
DEVIATION = 20

# Risk sizing (NO PIPS, ONLY PRICE DISTANCE)
RISK_PCT = 0.1                 # risk % of BALANCE per trade - CRITICAL: 6% max risk per trade
ASSUMED_SL_PRICE_DIST = 8.0     # assume worst-case SL is this many PRICE units away (e.g. $8.0)
MAX_LOT = 1.0                   # hard cap to prevent surprises

# Safety kill-switches (highly recommended)
USE_EQUITY_FOR_RISK = True       # safer sizing when floating loss exists
HARD_MAX_LOSS_MONEY = 1000      # ABSOLUTE max loss per trade in account currency; set None to disable
HARD_MAX_LOSS_PCT = 0.1         # ABSOLUTE max loss % per trade - CRITICAL: same as RISK_PCT; set None to disable
HARD_MIN_SL_DIST = 1.0           # do not allow SL closer than this (price units) from entry
SANITY_MAX_SL_DIST = 50.0        # if parsed SL is farther than this from market, do NOT apply (symbol mismatch)
SKIP_IF_MINLOT_EXCEEDS_CAP = True  # if minimum lot already exceeds cap, skip trade

# How long to keep retrying SL/TP modification when broker rejects stops
SLTP_RETRY_SECONDS = 45
SLTP_RETRY_DELAY = 0.6

# Treat these retcodes as retryable (market moving / too close / invalid stops)
RETRYABLE_RETCODES = {mt5.TRADE_RETCODE_INVALID_STOPS, 10029}

# Extra logging
DEBUG = True
DEBUG_DUMP_EVERY_EVENT = True
DEBUG_SHOW_MARKET_SNAPSHOT = True
DEBUG_SHOW_SYMBOL_SPECS = True
DEBUG_SHOW_RISK_MATH = True
DEBUG_SHOW_POSITION_STATE = True
DEBUG_WARN_ON_MULTI_POSITIONS = True


# ===================== INTERNAL STATE =====================
client = TelegramClient("zinra_session", api_id, api_hash)

# Map Telegram message id -> MT5 position ticket
msgid_to_ticket: Dict[int, int] = {}

# Remember last applied (tp, sl) per ticket to avoid re-modifying on repeated edits
ticket_last_targets: Dict[int, Tuple[float, float]] = {}

# Track duplicates / spam
last_seen_event_text: Dict[int, str] = {}


# ===================== UTILITIES / DEBUG =====================
def now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def dbg(msg: str, style: str = "cyan"):
    """Print debug message with rich formatting"""
    if DEBUG:
        timestamp = f"[{now_ts()}]"
        console.print(f"{timestamp} {msg}", style=style)


def banner(title: str, style: str = "bold yellow on dark_blue"):
    """Display a beautiful banner"""
    panel = Panel(
        title,
        style=style,
        expand=False,
        padding=(1, 2),
    )
    console.print()
    console.print(panel)


def fmt(x, nd=2):
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return str(x)


def safe_last_error() -> str:
    try:
        return str(mt5.last_error())
    except Exception:
        return "(mt5.last_error unavailable)"


def dump_market(symbol: str):
    if not DEBUG_SHOW_MARKET_SNAPSHOT:
        return
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        dbg(f"🔹 MARKET SNAPSHOT: tick=None for {symbol} | last_error={safe_last_error()}", style="yellow")
        return
    dbg(f"🔹 MARKET SNAPSHOT {symbol}: bid={fmt(tick.bid)} ask={fmt(tick.ask)} | last={getattr(tick,'last',None)} time={getattr(tick,'time',None)}", style="green")


def dump_symbol_specs(symbol: str):
    if not DEBUG_SHOW_SYMBOL_SPECS:
        return
    si = mt5.symbol_info(symbol)
    if si is None:
        dbg(f"⚙️ SYMBOL SPECS: symbol_info(None) for {symbol} | last_error={safe_last_error()}", style="yellow")
        return
    
    spec_text = (
        f"⚙️ SYMBOL SPECS {symbol}: "
        f"digits={si.digits} | point={si.point} | "
        f"vol_min={si.volume_min} | vol_max={si.volume_max} | vol_step={si.volume_step} | "
        f"trade_stops_level={getattr(si,'trade_stops_level',None)} | "
        f"trade_contract_size={getattr(si,'trade_contract_size',None)} | "
        f"visible={si.visible}"
    )
    dbg(spec_text, style="cyan")


def dump_account():
    acc = mt5.account_info()
    if acc is None:
        dbg(f"💰 ACCOUNT: account_info=None | last_error={safe_last_error()}", style="red")
        return None
    
    table = Table(title="💰 ACCOUNT INFO", style="bold cyan")
    table.add_column("Field", style="magenta")
    table.add_column("Value", style="green")
    
    table.add_row("Balance", f"${fmt(acc.balance, 2)}")
    table.add_row("Equity", f"${fmt(acc.equity, 2)}")
    table.add_row("Margin", f"${fmt(acc.margin, 2)}")
    table.add_row("Free Margin", f"${fmt(acc.margin_free, 2)}")
    table.add_row("Profit/Loss", f"${fmt(acc.profit, 2)}")
    table.add_row("Currency", str(acc.currency))
    
    console.print(table)
    return acc


def dump_positions(symbol: str):
    if not DEBUG_SHOW_POSITION_STATE:
        return
    poss = mt5.positions_get(symbol=symbol) or []
    
    if not poss:
        dbg(f"📊 POSITIONS {symbol}: None active", style="yellow")
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
        pnl_color = "green" if p.profit > 0 else "red"
        table.add_row(
            str(p.ticket),
            "BUY" if p.type == 0 else "SELL",
            fmt(p.volume),
            fmt(p.price_open),
            fmt(p.sl) if p.sl > 0 else "—",
            fmt(p.tp) if p.tp > 0 else "—",
            f"${fmt(p.profit, 2)}",
            style=pnl_color
        )
    
    if len(poss) > 10:
        table.add_row("", "", "", "", "", "", f"... +{len(poss)-10} more")
    
    console.print(table)


def is_close(a: float, b: float, tol: float = 0.02) -> bool:
    return abs(float(a) - float(b)) <= tol


# ===================== MT5 HELPERS =====================
def mt5_connect():
    if not mt5.initialize():
        dbg(f"❌ MT5 initialize failed: {safe_last_error()}", style="bold red")
        raise RuntimeError(f"MT5 initialize failed: {safe_last_error()}")

    info = mt5.account_info()
    if info is None:
        dbg("❌ MT5 account_info() is None (not logged in?)", style="bold red")
        raise RuntimeError("MT5 account_info() is None (not logged in?)")

    term = mt5.terminal_info()
    if term is None:
        dbg("❌ MT5 terminal_info() is None", style="bold red")
        raise RuntimeError("MT5 terminal_info() is None")

    if not getattr(term, "trade_allowed", False):
        msg = "⚠️ Trading is NOT allowed in MT5 terminal (trade_allowed=False)"
        dbg(msg, style="bold yellow")
        console.print(
            "\n[bold yellow]═══════════════════════════════════════════[/bold yellow]"
            "\n[bold yellow]⚠️  AutoTrading Disabled[/bold yellow]"
            "\n[bold yellow]═══════════════════════════════════════════[/bold yellow]\n"
            "[cyan]The bot will:[/cyan]\n"
            "  ✓ Monitor Telegram signals\n"
            "  ✓ Display what trades WOULD be executed\n"
            "  ✗ NOT actually execute trades\n\n"
            "[yellow]To enable actual trading, in MetaTrader 5:[/yellow]\n"
            "  1. Tools → Options → Expert Advisors\n"
            "  2. Enable [bold]Allow live trading[/bold]\n"
            "  3. Enable [bold]Allow DLL imports[/bold]\n"
            "  4. Restart the bot\n"
            "[cyan]For now, bot will run in SIGNAL DISPLAY mode[/cyan]\n"
            "[bold yellow]═══════════════════════════════════════════[/bold yellow]\n",
            style="bright_white"
        )

    # Check if in test mode and warn about demo account
    if TEST_MODE:
        account_type = "Demo" if info.login < 100000000 else "Live"  # Simple heuristic
        dbg(f"🧪 TEST MODE ACTIVE", style="bold yellow on blue")
        
        if "demo" not in str(info.server).lower() and account_type == "Live":
            banner("⚠️ TEST MODE WARNING", style="bold yellow on blue")
            console.print(
                "\n[bold yellow]⚠️  TEST MODE DETECTED[/bold yellow]\n"
                "[cyan]Your MetaTrader 5 account appears to be a [bold]LIVE[/bold] account.[/cyan]\n"
                "[cyan]Please switch to a [bold]DEMO[/bold] account for testing![/cyan]\n"
                "[yellow]To switch accounts in MT5:[/yellow]\n"
                "  1. File → Open an Account\n"
                "  2. Select or create a DEMO account\n"
                "  3. Restart this bot\n"
                "[bold red]Proceeding in 5 seconds... [/bold red]\n",
                style="bold"
            )
            time.sleep(5)
        elif "demo" in str(info.server).lower() or account_type == "Demo":
            banner("✅ DEMO ACCOUNT CONFIRMED", style="bold white on green")
            dbg(f"Safe to test on demo account", style="green")

    banner("✅ MT5 CONNECTED", style="bold white on green")
    dbg(f"Account: login={info.login} | server={getattr(info,'server',None)} | currency={info.currency}", style="green")
    dump_account()
    dump_symbol_specs(SYMBOL)
    dump_market(SYMBOL)


def ensure_symbol(symbol: str, retries: int = 8, delay: float = 0.5):
    """
    MT5 Python can temporarily return symbol_info(None) even when symbol is visible in UI.
    We retry + force-select to avoid missing signals.
    """
    last_si = None
    for i in range(retries):
        si = mt5.symbol_info(symbol)
        last_si = si
        if si is not None:
            if not si.visible:
                mt5.symbol_select(symbol, True)
                si = mt5.symbol_info(symbol)
                if si is None:
                    dbg(f"⚠️ ensure_symbol: select({symbol}) done but symbol_info still None (attempt {i+1}/{retries})", style="yellow")
                else:
                    return si
            return si

        mt5.symbol_select(symbol, True)
        dbg(f"⏳ ensure_symbol: symbol_info(None) for {symbol}; forced select (attempt {i+1}/{retries})", style="yellow")
        time.sleep(delay)

    dbg(f"❌ Symbol not available after retries: {symbol}", style="bold red")
    raise RuntimeError(f"Symbol not available after retries: {symbol} | last_si={last_si} | last_error={safe_last_error()}")


def retcode_is_ok(retcode: int) -> bool:
    return retcode in {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED, mt5.TRADE_RETCODE_NO_CHANGES}


def market_order(symbol: str, lot: float, side: str, tg_msg_id: int):
    ensure_symbol(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick data for {symbol} (market closed?) | last_error={safe_last_error()}")

    side = side.upper()
    if side == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
    elif side == "SELL":
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        raise ValueError("side must be BUY or SELL")

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lot),
        "type": order_type,
        "price": float(price),
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": f"tg:{tg_msg_id}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    dbg(f"📤 ORDER SEND (ENTRY): side={side} | lot={fmt(lot)} | price={fmt(price)}", style="bold cyan")
    r = mt5.order_send(request)
    rc = getattr(r, "retcode", -1) if r is not None else -1
    status = "✅" if retcode_is_ok(rc) else "❌"
    dbg(f"{status} ORDER RESULT (ENTRY): retcode={rc} | ticket={getattr(r, 'order', '?')}", style="green" if retcode_is_ok(rc) else "red")
    return r


def set_sltp(symbol: str, ticket: int, sl: float, tp: float):
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": ticket,
        "sl": float(sl),
        "tp": float(tp),
        "magic": MAGIC,
        "comment": "set TP1+SL",
    }
    dbg(f"📤 ORDER SEND (SLTP): ticket={ticket} | SL={fmt(sl)} | TP={fmt(tp)}", style="bold cyan")
    r = mt5.order_send(request)
    rc = getattr(r, "retcode", -1) if r is not None else -1
    status = "✅" if retcode_is_ok(rc) else "❌"
    dbg(f"{status} ORDER RESULT (SLTP): retcode={rc}", style="green" if retcode_is_ok(rc) else "red")
    return r


def find_position_ticket(symbol: str, tg_id: int, retries: int = 45, delay: float = 0.2) -> Optional[int]:
    target_comment = f"tg:{tg_id}"
    for i in range(retries):
        positions = mt5.positions_get(symbol=symbol) or []
        for p in positions:
            if p.magic == MAGIC and (p.comment or "") == target_comment:
                dbg(f"✅ Found ticket={p.ticket} for tg_id={tg_id} (attempt {i+1}/{retries})", style="green")
                return p.ticket
        time.sleep(delay)
    dbg(f"⏳ Position NOT found for tg_id={tg_id} after {retries} retries (may still be pending)", style="yellow")
    return None


def get_min_stop_distance(symbol: str) -> float:
    si = ensure_symbol(symbol)
    points = getattr(si, "trade_stops_level", 0) or 0
    min_dist = points * si.point
    min_dist = max(min_dist, 2 * si.point)
    dbg(f"min_stop_distance: trade_stops_level(points)={points} point={si.point} => min_dist={min_dist}")
    return float(min_dist)


def clamp_sltp_to_valid(symbol: str, side: str, sl: float, tp: float) -> Tuple[float, float]:
    """
    Clamps SL/TP ONLY to satisfy broker minimum stop distance.
    WARNING: if parsed SL is way off market (wrong symbol), we should refuse before clamping.
    """
    min_dist = get_min_stop_distance(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        dbg("⚠️ clamp_sltp_to_valid: tick=None, returning raw sl/tp", style="yellow")
        return sl, tp

    side = side.upper()
    bid, ask = tick.bid, tick.ask

    raw_sl, raw_tp = sl, tp

    if side == "BUY":
        sl_max = bid - min_dist
        tp_min = ask + min_dist
        sl = min(sl, sl_max)
        tp = max(tp, tp_min)
    else:
        sl_min = ask + min_dist
        tp_max = bid - min_dist
        sl = max(sl, sl_min)
        tp = min(tp, tp_max)

    if DEBUG_SHOW_RISK_MATH and (raw_sl != sl or raw_tp != tp):
        dbg(
            f"🔧 SLTP Adjusted: SL {fmt(raw_sl)}→{fmt(sl)} | TP {fmt(raw_tp)}→{fmt(tp)}", 
            style="yellow"
        )

    return float(sl), float(tp)


def get_position_side(ticket: int) -> Optional[str]:
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        return None
    p = pos[0]
    if p.type == mt5.POSITION_TYPE_BUY:
        return "BUY"
    if p.type == mt5.POSITION_TYPE_SELL:
        return "SELL"
    return None


# ===================== LOT SIZING (PRICE DISTANCE) =====================
def round_down_to_step(x: float, step: float) -> float:
    return math.floor(x / step) * step


def calc_max_lot_for_risk_price_dist(
    symbol: str,
    side: str,
    entry_price: float,
    sl_price_dist: float,
    capital: float,     # balance/equity used for risk
    risk_pct: float,
    also_limit_by_margin: bool = True,
) -> float:
    si = ensure_symbol(symbol)
    risk_money = float(capital) * float(risk_pct)

    side = side.upper()
    if side == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        sl_price = float(entry_price) - float(sl_price_dist)
    elif side == "SELL":
        order_type = mt5.ORDER_TYPE_SELL
        sl_price = float(entry_price) + float(sl_price_dist)
    else:
        raise ValueError("side must be BUY or SELL")

    # Loss for 1.00 lot if SL hits (profit will be negative for losing move)
    loss_for_1lot = mt5.order_calc_profit(order_type, symbol, 1.0, float(entry_price), float(sl_price))
    if loss_for_1lot is None:
        raise RuntimeError(f"order_calc_profit failed: {safe_last_error()}")

    loss_for_1lot = abs(loss_for_1lot)
    if loss_for_1lot <= 0:
        raise RuntimeError("Computed 0 loss for 1 lot; check symbol specs / prices.")

    raw_lot = risk_money / loss_for_1lot

    # Broker constraints
    vmin = float(getattr(si, "volume_min", 0.01) or 0.01)
    vmax = float(getattr(si, "volume_max", 100.0) or 100.0)
    vstep = float(getattr(si, "volume_step", 0.01) or 0.01)

    lot = max(vmin, min(vmax, raw_lot))
    lot = round_down_to_step(lot, vstep)

    if also_limit_by_margin and lot > 0:
        acc = mt5.account_info()
        if acc is not None:
            free_margin = float(acc.margin_free)
            margin_for_1lot = mt5.order_calc_margin(order_type, symbol, 1.0, float(entry_price))
            if margin_for_1lot is not None and margin_for_1lot > 0:
                max_by_margin = free_margin / float(margin_for_1lot)
                max_by_margin = round_down_to_step(max_by_margin, vstep)
                lot = min(lot, max_by_margin)
                lot = round_down_to_step(lot, vstep)
                if DEBUG_SHOW_RISK_MATH:
                    dbg(f"💳 Margin constraint: free=${fmt(free_margin, 2)} | max_lot={fmt(max_by_margin, 3)}", style="cyan")

    lot = min(lot, float(MAX_LOT))
    lot = round_down_to_step(lot, vstep)

    if lot < vmin:
        return 0.0
    return float(lot)


# ===================== PARSING HELPERS =====================
def parse_entry_side(text: str) -> Optional[str]:
    t = (text or "").upper()
    if "BUY NOW" in t:
        return "BUY"
    if "SELL NOW" in t:
        return "SELL"
    return None


def parse_tp1_sl(text: str) -> Tuple[Optional[float], Optional[float]]:
    t = text or ""
    tp1 = None
    sl = None

    # TP1 can be "TP1" or "TP 1" or "Tp1"
    m = re.search(r"\bTP\s*1\b[^0-9]*([0-9]+(?:\.[0-9]+)?)", t, re.IGNORECASE)
    if m:
        tp1 = float(m.group(1))

    # SL can appear with emojis; search for "SL" letters anywhere, not strict \b boundaries.
    m = re.search(r"SL[^0-9]*([0-9]+(?:\.[0-9]+)?)", t, re.IGNORECASE)
    if m:
        sl = float(m.group(1))

    return tp1, sl


def is_noise_message(text: str) -> bool:
    t = (text or "").upper()
    noise_phrases = [
        "TP1 HIT", "TP2 HIT", "TP3 HIT", "TP4 HIT", "TP5 HIT",
        "STAY ACTIVE", "POTENTIAL", "BE READY", "CLOSE SOME PROFITS",
        "MISSED OUR ZONE", "PIPS"
    ]
    return any(p in t for p in noise_phrases) and ("BUY NOW" not in t and "SELL NOW" not in t)


# ===================== SAFETY CHECKS =====================
def calc_pl_if_sl(symbol: str, side: str, lot: float, entry_price: float, sl_price: float) -> Optional[float]:
    side = side.upper()
    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    pl = mt5.order_calc_profit(order_type, symbol, float(lot), float(entry_price), float(sl_price))
    return pl


def should_block_trade_by_hard_caps(acc, risk_loss_money: float) -> Tuple[bool, str]:
    """
    risk_loss_money is negative for loss, we compare abs.
    """
    if acc is None:
        return False, ""

    cap_money = abs(float(risk_loss_money))
    base = float(acc.equity) if USE_EQUITY_FOR_RISK else float(acc.balance)

    if HARD_MAX_LOSS_MONEY is not None and cap_money > float(HARD_MAX_LOSS_MONEY):
        msg = f"🚫 Hard €/$ cap: risk ${fmt(cap_money, 2)} > limit ${HARD_MAX_LOSS_MONEY}"
        return True, msg

    if HARD_MAX_LOSS_PCT is not None and base > 0:
        pct = cap_money / base
        if pct > float(HARD_MAX_LOSS_PCT):
            msg = f"🚫 Hard % cap: risk {fmt(pct*100, 1)}% > limit {fmt(HARD_MAX_LOSS_PCT*100, 1)}%"
            return True, msg

    return False, ""


# ===================== TELEGRAM HANDLERS =====================
@client.on(events.NewMessage())
async def on_new(event):
    # Debug: Log all incoming messages with their chat IDs
    if DEBUG_DUMP_EVERY_EVENT:
        banner(f"📨 EVENT RECEIVED: chat_id={event.chat_id}, CHANNEL={CHANNEL}", style="bold magenta on dark_magenta")
    
    # Filter by current CHANNEL (may change in test mode)
    if event.chat_id != CHANNEL:
        if DEBUG_DUMP_EVERY_EVENT:
            dbg(f"⏭️ Ignoring message from chat_id={event.chat_id} (expecting {CHANNEL})", style="dim yellow")
        return
    
    text_raw = (event.raw_text or "").strip()
    if not text_raw:
        return

    if DEBUG_DUMP_EVERY_EVENT:
        banner(f"📨 NEW TELEGRAM MESSAGE (ID: {event.message.id})", style="bold magenta on dark_magenta")
        dbg(text_raw, style="white")

    side = parse_entry_side(text_raw)
    if not side:
        dbg("❌ No BUY NOW / SELL NOW found. Ignoring.", style="yellow")
        return

    tg_id = event.message.id
    side_u = side.upper()

    banner(f"🎯 NEW SIGNAL RECEIVED: {side_u}", style="bold yellow on dark_blue")
    dbg(f"Message ID: {tg_id} | Side: {side_u}", style="bright_cyan")

    try:
        ensure_symbol(SYMBOL)
        dump_symbol_specs(SYMBOL)
        dump_market(SYMBOL)

        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            raise RuntimeError(f"No tick data for {SYMBOL} (market closed?) | last_error={safe_last_error()}")

        entry_price = float(tick.ask if side_u == "BUY" else tick.bid)
        dbg(f"📈 Entry price: ${fmt(entry_price, 5)}", style="green")

        acc = mt5.account_info()
        if acc is None:
            raise RuntimeError("MT5 account_info() is None (not logged in?)")
        dump_account()

        capital = float(acc.equity if USE_EQUITY_FOR_RISK else acc.balance)
        if capital <= 0:
            raise RuntimeError(f"Capital is non-positive ({capital}). Refusing to trade.")

        # Enforce minimum assumed SL distance sanity
        assumed_dist = max(float(ASSUMED_SL_PRICE_DIST), float(HARD_MIN_SL_DIST))

        lot = calc_max_lot_for_risk_price_dist(
            symbol=SYMBOL,
            side=side_u,
            entry_price=entry_price,
            sl_price_dist=assumed_dist,
            capital=capital,
            risk_pct=RISK_PCT,
            also_limit_by_margin=True,
        )

        if lot <= 0:
            dbg("📉 Lot size = 0 (margin/risk constraints). Skipping trade.", style="yellow")
            return

        # Risk math sanity print
        assumed_sl_price = entry_price - assumed_dist if side_u == "BUY" else entry_price + assumed_dist
        pl_if_sl = calc_pl_if_sl(SYMBOL, side_u, lot, entry_price, assumed_sl_price)
        if pl_if_sl is None:
            raise RuntimeError(f"order_calc_profit failed for risk preview | last_error={safe_last_error()}")

        max_risk = capital * RISK_PCT
        
        if DEBUG_SHOW_RISK_MATH:
            risk_table = Table(title="💰 RISK CALCULATION", style="bold cyan")
            risk_table.add_column("Parameter", style="magenta")
            risk_table.add_column("Value", style="green")
            risk_table.add_row("Entry Price", f"${fmt(entry_price, 5)}")
            risk_table.add_row("Assumed SL Distance", f"${fmt(assumed_dist, 4)}")
            risk_table.add_row("Assumed SL Price", f"${fmt(assumed_sl_price, 5)}")
            risk_table.add_row("Lot Size", fmt(lot, 3))
            risk_table.add_row("Max Risk", f"${fmt(max_risk, 2)}")
            risk_table.add_row("Risk if SL Hit", f"${fmt(abs(pl_if_sl), 2)}")
            console.print(risk_table)

        # If min lot already exceeds cap, optionally skip
        if SKIP_IF_MINLOT_EXCEEDS_CAP and abs(pl_if_sl) > (capital * RISK_PCT * 1.05):
            dbg(f"🚫 Min lot risk exceeds cap. Skipping trade.", style="yellow")
            return

        # Hard kill-switches (absolute)
        block, reason = should_block_trade_by_hard_caps(acc, pl_if_sl)
        if block:
            dbg(f"🚫 TRADE BLOCKED: {reason}", style="bold red")
            return

        # Warn if multiple positions open (could stack risk)
        if DEBUG_WARN_ON_MULTI_POSITIONS:
            poss = mt5.positions_get(symbol=SYMBOL) or []
            if len(poss) >= 1:
                dbg(f"⚠️ WARNING: {len(poss)} position(s) already open on {SYMBOL}. Risk stacking!", style="bold yellow")

        r = market_order(SYMBOL, lot, side_u, tg_id)
        rc = getattr(r, "retcode", -1) if r is not None else -1
        if r is None or not retcode_is_ok(rc):
            dbg(f"❌ ENTRY FAILED: retcode={rc} | Error: {safe_last_error()}", style="bold red")
            return

        ticket = find_position_ticket(SYMBOL, tg_id)
        if ticket:
            msgid_to_ticket[tg_id] = ticket
            dbg(f"✅ ENTRY EXECUTED: Ticket {ticket} | Lot {fmt(lot, 3)}", style="bold green")
            dump_positions(SYMBOL)
        else:
            dbg("⏳ ENTRY OK but ticket not found yet. Waiting for confirmation...", style="yellow")

    except Exception as e:
        dbg(f"❌ ENTRY FAILED: {str(e)}", style="bold red")


@client.on(events.MessageEdited())
async def on_edit(event):
    # Filter by current CHANNEL (may change in test mode)
    if event.chat_id != CHANNEL:
        return
    
    text = (event.raw_text or "").strip()
    if not text:
        return

    if DEBUG_DUMP_EVERY_EVENT:
        banner(f"📝 TELEGRAM EDIT MESSAGE (ID: {event.message.id})", style="bold cyan on dark_cyan")
        dbg(text, style="white")

    if is_noise_message(text):
        dbg("❌ Noise message detected. Ignoring.", style="yellow")
        return

    tg_id = event.message.id

    # avoid re-processing identical edit payloads
    prev = last_seen_event_text.get(tg_id)
    if prev == text:
        dbg("⏭️ Duplicate edit payload. Skipping.", style="yellow")
        return
    last_seen_event_text[tg_id] = text

    banner(f"🎯 EDIT SIGNAL RECEIVED", style="bold yellow on dark_blue")
    dbg(f"Message ID: {tg_id}", style="bright_cyan")

    ticket = msgid_to_ticket.get(tg_id)
    if not ticket:
        ticket = find_position_ticket(SYMBOL, tg_id)
        if ticket:
            msgid_to_ticket[tg_id] = ticket
            dbg(f"🔍 Late-found ticket {ticket} for msg_id {tg_id}", style="yellow")
        else:
            dbg("⏳ No ticket for this msg_id yet. Waiting for position confirmation.", style="yellow")
            return

    tp1, sl = parse_tp1_sl(text)
    
    if tp1 is not None and sl is not None:
        dbg(f"✅ Parsed: TP1=${fmt(tp1, 5)} | SL=${fmt(sl, 5)}", style="green")
    else:
        dbg(f"⏳ Incomplete: TP1={tp1} | SL={sl}. Waiting for next edit.", style="yellow")
        if tp1 is None or sl is None:
            return

    ensure_symbol(SYMBOL)
    dump_symbol_specs(SYMBOL)
    dump_market(SYMBOL)
    dump_positions(SYMBOL)

    side = get_position_side(ticket)
    if not side:
        dbg("❌ Position not found (may be closed already).", style="yellow")
        return

    # Sanity check SL against market to avoid symbol mismatch disasters
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is not None:
        cur = float(tick.bid if side == "SELL" else tick.ask)
        dist = abs(float(sl) - cur)
        dbg(f"📏 Sanity check: current=${fmt(cur, 5)} | SL=${fmt(sl, 5)} | distance=${fmt(dist, 4)}", style="cyan")
        if dist > SANITY_MAX_SL_DIST:
            dbg(f"🚫 SL too far from market (${fmt(dist, 2)} > limit ${fmt(SANITY_MAX_SL_DIST, 2)}). Possible symbol mismatch!", style="bold red")
            return

    # Avoid re-modifying if we already applied these targets (or close enough)
    last = ticket_last_targets.get(ticket)
    if last is not None:
        last_tp, last_sl = last
        if is_close(last_tp, float(tp1), tol=0.05) and is_close(last_sl, float(sl), tol=0.05):
            dbg("⏭️ Targets already applied recently. Skipping duplicate modification.", style="yellow")
            return

    deadline = time.time() + SLTP_RETRY_SECONDS
    last_ret = None
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        try:
            # adjust to broker min-distance
            adj_sl, adj_tp = clamp_sltp_to_valid(SYMBOL, side, float(sl), float(tp1))

            # EXTRA: check we are not setting something absurdly close to market after clamp
            tick2 = mt5.symbol_info_tick(SYMBOL)
            if tick2 is not None:
                cur2 = float(tick2.bid if side == "SELL" else tick2.ask)
                min_safe = get_min_stop_distance(SYMBOL) * 0.9
                if abs(adj_sl - cur2) < min_safe:
                    dbg(f"⚠️ Adjusted SL very close to market! Instant stop-out risk.", style="bold yellow")

            r = set_sltp(SYMBOL, ticket, sl=adj_sl, tp=adj_tp)
            last_ret = r

            rc = getattr(r, "retcode", -1)

            if retcode_is_ok(rc):
                banner(f"✅ POSITION UPDATED", style="bold green on dark_green")
                dbg(f"Ticket {ticket} | SL=${fmt(adj_sl, 5)} | TP=${fmt(adj_tp, 5)}", style="bold green")
                ticket_last_targets[ticket] = (float(tp1), float(sl))
                dump_positions(SYMBOL)
                return

            if rc in RETRYABLE_RETCODES:
                dbg(f"⏳ Retryable error (code {rc}). Retry {attempt}/{int(SLTP_RETRY_SECONDS/SLTP_RETRY_DELAY)}", style="yellow")
                time.sleep(SLTP_RETRY_DELAY)
                continue

            dbg(f"❌ SL/TP FAILED (non-retry): retcode={rc}", style="bold red")
            dump_positions(SYMBOL)
            return

        except Exception as e:
            dbg(f"⚠️ Exception: {str(e)}. Retrying...", style="yellow")
            time.sleep(SLTP_RETRY_DELAY)

    dbg(f"❌ TIMEOUT: Gave up after {SLTP_RETRY_SECONDS}s", style="bold red")
    dump_positions(SYMBOL)


# ===================== RUN LOOP WITH AUTO-RECONNECT =====================
def run_forever():
    global TEST_MODE, CHANNEL
    
    # Display test mode status
    if TEST_MODE:
        if TEST_CHANNEL is None:
            dbg(
                "❌ TEST_CHANNEL_ID not configured!\n"
                "Please run: python find_channel.py\n"
                "Then update TEST_CHANNEL in script.py with your test channel ID",
                style="bold red"
            )
            raise RuntimeError("TEST_CHANNEL_ID not configured. Please set it in script.py")
        
        banner("🧪 TEST MODE ACTIVATED", style="bold yellow on blue")
        dbg(f"Using test channel: {TEST_CHANNEL}", style="yellow")
        dbg(f"Original live channel: {CHANNEL}", style="cyan")
        CHANNEL = TEST_CHANNEL
        dbg(f"Channel switched to test channel for this session", style="green")
    else:
        banner("🚀 LIVE MODE", style="bold white on green")
        dbg(f"Trading on live channel: {CHANNEL}", style="green")
    
    mt5_connect()
    ensure_symbol(SYMBOL)
    
    banner("🤖 TRADING BOT ACTIVE", style="bold white on green")
    dbg(f"Listening to Telegram channel for signals...", style="bright_white")
    
    while True:
        try:
            client.start()
            client.run_until_disconnected()
        except Exception as e:
            dbg(f"⚠️ Telegram connection lost: {str(e)}", style="bold yellow")
            dbg("🔄 Reconnecting in 5 seconds...", style="yellow")
            time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Copy Trading Gold Bot - Automated Trading with Telegram Signals",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python script.py                    # Run in LIVE mode
  python script.py --test-mode        # Run in TEST mode
  python script.py -t                 # Short form of test mode
        """
    )
    
    parser.add_argument(
        '--test-mode', '-t',
        action='store_true',
        help='Enable TEST MODE: Uses test Telegram channel and requires demo account'
    )
    
    args = parser.parse_args()
    
    TEST_MODE = args.test_mode
    
    if TEST_MODE:
        console.print(
            "\n[bold yellow]═══════════════════════════════════════════[/bold yellow]"
            "\n[bold yellow]🧪 TEST MODE ENABLED[/bold yellow]"
            "\n[bold yellow]═══════════════════════════════════════════[/bold yellow]\n"
            "[cyan]This bot will:[/cyan]\n"
            "  • Connect to the [bold]TEST[/bold] Telegram channel\n"
            "  • Require a [bold]DEMO[/bold] MetaTrader 5 account\n"
            "  • NOT execute trades on your live account\n"
            "[yellow]Setup required:[/yellow]\n"
            "  1. Run: [bold]python find_channel.py[/bold]\n"
            "  2. Find 'testing the bot do not touch' channel ID\n"
            "  3. Update TEST_CHANNEL in script.py\n"
            "  4. Switch MetaTrader 5 to a DEMO account\n"
            "[green]Then run: python script.py --test-mode[/green]\n"
            "\n[bold yellow]═══════════════════════════════════════════[/bold yellow]\n",
            style="bright_white"
        )
        
        # Check if TEST_CHANNEL is configured
        if TEST_CHANNEL is None:
            console.print(
                "[bold red]ERROR: TEST_CHANNEL not configured![/bold red]\n"
                "[cyan]Steps to fix:[/cyan]\n"
                "  1. Run: [bold]python find_channel.py[/bold]\n"
                "  2. Find your test channel ID\n"
                "  3. Edit script.py\n"
                "  4. Update: [bold]TEST_CHANNEL = YOUR_CHANNEL_ID[/bold]\n"
                "  5. Run: python script.py --test-mode\n",
                style="bright_white"
            )
            import sys
            sys.exit(1)
    
    run_forever()
