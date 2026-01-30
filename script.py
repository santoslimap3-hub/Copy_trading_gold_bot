import re
import time
import math
from typing import Optional, Tuple, Dict

from telethon import TelegramClient, events
import MetaTrader5 as mt5


# ===================== TELEGRAM CONFIG =====================
api_id = 34597981
api_hash = "2cd59609b6cacb56da261e43fdb897ea"
CHANNEL = -1003349563414  # EGN GOLD


# ===================== MT5 CONFIG =====================
SYMBOL = "XAUUSD"
MAGIC = 777
DEVIATION = 20

# Risk sizing (NO PIPS, ONLY PRICE DISTANCE)
RISK_PCT = 0.10                  # risk % of BALANCE per trade
ASSUMED_SL_PRICE_DIST = 8.0     # assume worst-case SL is this many PRICE units away (e.g. $10)
MAX_LOT = 1                  # hard cap to prevent surprises

# How long to keep retrying SL/TP modification when "Invalid stops" happens
SLTP_RETRY_SECONDS = 30
SLTP_RETRY_DELAY = 0.7


# ===================== INTERNAL STATE =====================
client = TelegramClient("zinra_session", api_id, api_hash)

# Map Telegram message id -> MT5 position ticket
msgid_to_ticket: Dict[int, int] = {}


# ===================== MT5 HELPERS =====================
def mt5_connect():
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    info = mt5.account_info()
    if info is None:
        raise RuntimeError("MT5 account_info() is None (not logged in?)")

    term = mt5.terminal_info()
    if term is None:
        raise RuntimeError("MT5 terminal_info() is None")

    if not getattr(term, "trade_allowed", False):
        raise RuntimeError(
            "Trading is NOT allowed in MT5 terminal (trade_allowed=False).\n"
            "Fix: turn ON Algo Trading / AutoTrading in MT5."
        )

    print(f"✅ MT5 connected. Account: {info.login} Balance: {info.balance}")
    print(f"✅ Terminal trade_allowed={term.trade_allowed}")


def ensure_symbol(symbol: str, retries: int = 5, delay: float = 0.5):
    for i in range(retries):
        si = mt5.symbol_info(symbol)
        if si is not None:
            if not si.visible:
                mt5.symbol_select(symbol, True)
            return si

        # try to force-select it
        mt5.symbol_select(symbol, True)
        time.sleep(delay)

    raise RuntimeError(f"Symbol not available after retries: {symbol}")



def retcode_is_ok(retcode: int) -> bool:
    return retcode in {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED, mt5.TRADE_RETCODE_NO_CHANGES}


def market_order(symbol: str, lot: float, side: str, tg_msg_id: int):
    ensure_symbol(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError("No tick data (market closed?)")

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
    return mt5.order_send(request)


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
    return mt5.order_send(request)


def find_position_ticket(symbol: str, tg_id: int, retries: int = 30, delay: float = 0.2) -> Optional[int]:
    """Retry because positions may appear slightly after order_send."""
    target_comment = f"tg:{tg_id}"
    for _ in range(retries):
        positions = mt5.positions_get(symbol=symbol) or []
        for p in positions:
            if p.magic == MAGIC and (p.comment or "") == target_comment:
                return p.ticket
        time.sleep(delay)
    return None


def get_min_stop_distance(symbol: str) -> float:
    """
    Returns minimum stop distance in PRICE units based on broker stops level.
    Some brokers return 0 -> still apply a tiny safety buffer.
    """
    si = ensure_symbol(symbol)
    points = getattr(si, "trade_stops_level", 0) or 0  # in points
    min_dist = points * si.point
    min_dist = max(min_dist, 2 * si.point)  # safety buffer even if broker says 0
    return min_dist


def clamp_sltp_to_valid(symbol: str, side: str, sl: float, tp: float) -> Tuple[float, float]:
    """
    Ensures SL/TP satisfy broker minimum distance from current price.
    If needed, pushes SL/TP slightly further away so MT5 accepts it.
    """
    min_dist = get_min_stop_distance(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return sl, tp

    side = side.upper()
    bid, ask = tick.bid, tick.ask

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

    return sl, tp


def get_position_side(ticket: int) -> Optional[str]:
    """Returns BUY/SELL for a position ticket, or None if not found."""
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
    balance: float,
    risk_pct: float,
    also_limit_by_margin: bool = True,
) -> float:
    si = ensure_symbol(symbol)
    risk_money = balance * risk_pct

    side = side.upper()
    if side == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        sl_price = entry_price - float(sl_price_dist)
    elif side == "SELL":
        order_type = mt5.ORDER_TYPE_SELL
        sl_price = entry_price + float(sl_price_dist)
    else:
        raise ValueError("side must be BUY or SELL")

    # Loss for 1.00 lot if SL hits
    loss_for_1lot = mt5.order_calc_profit(order_type, symbol, 1.0, entry_price, sl_price)
    if loss_for_1lot is None:
        raise RuntimeError(f"order_calc_profit failed: {mt5.last_error()}")

    loss_for_1lot = abs(loss_for_1lot)
    if loss_for_1lot <= 0:
        raise RuntimeError("Computed 0 loss for 1 lot; check symbol specs / prices.")

    raw_lot = risk_money / loss_for_1lot

    # Broker constraints
    vmin = getattr(si, "volume_min", 0.01) or 0.01
    vmax = getattr(si, "volume_max", 100.0) or 100.0
    vstep = getattr(si, "volume_step", 0.01) or 0.01

    lot = max(vmin, min(vmax, raw_lot))
    lot = round_down_to_step(lot, vstep)

    # Optional: limit by free margin
    if also_limit_by_margin and lot > 0:
        acc = mt5.account_info()
        if acc is not None:
            free_margin = acc.margin_free
            margin_for_1lot = mt5.order_calc_margin(order_type, symbol, 1.0, entry_price)
            if margin_for_1lot is not None and margin_for_1lot > 0:
                max_by_margin = free_margin / margin_for_1lot
                max_by_margin = round_down_to_step(max_by_margin, vstep)
                lot = min(lot, max_by_margin)
                lot = round_down_to_step(lot, vstep)

    # Hard cap
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

    m = re.search(r"\bTP\s*1\b[^0-9]*([0-9]+(?:\.[0-9]+)?)", t, re.IGNORECASE)
    if m:
        tp1 = float(m.group(1))

    m = re.search(r"\bSL\b[^0-9]*([0-9]+(?:\.[0-9]+)?)", t, re.IGNORECASE)
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


# ===================== TELEGRAM HANDLERS =====================
@client.on(events.NewMessage(chats=CHANNEL))
async def on_new(event):
    text_raw = (event.raw_text or "").strip()
    if not text_raw:
        return

    side = parse_entry_side(text_raw)
    if not side:
        return

    tg_id = event.message.id
    print(f"\n🆕 NEW SIGNAL (msg_id={tg_id}) side={side}")
    print(text_raw)

    try:
        ensure_symbol(SYMBOL)
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            raise RuntimeError("No tick data (market closed?)")

        entry_price = tick.ask if side.upper() == "BUY" else tick.bid

        acc = mt5.account_info()
        if acc is None:
            raise RuntimeError("MT5 account_info() is None (not logged in?)")

        lot = calc_max_lot_for_risk_price_dist(
            symbol=SYMBOL,
            side=side,
            entry_price=entry_price,
            sl_price_dist=ASSUMED_SL_PRICE_DIST,
            balance=acc.balance,  # switch to acc.equity if you prefer
            risk_pct=RISK_PCT,
            also_limit_by_margin=True,
        )

        if lot <= 0:
            print("⚠️ Lot computed as 0. Skipping trade (risk/margin constraints).")
            return

        # sanity check print
        order_type = mt5.ORDER_TYPE_BUY if side.upper() == "BUY" else mt5.ORDER_TYPE_SELL
        assumed_sl_price = entry_price - ASSUMED_SL_PRICE_DIST if side.upper() == "BUY" else entry_price + ASSUMED_SL_PRICE_DIST
        loss_if_assumed_sl = mt5.order_calc_profit(order_type, SYMBOL, lot, entry_price, assumed_sl_price)
        print(f"📏 lot={lot} | assumed SL ({ASSUMED_SL_PRICE_DIST}) loss≈ {loss_if_assumed_sl:.2f} | cap≈ {-acc.balance*RISK_PCT:.2f}")

        r = market_order(SYMBOL, lot, side, tg_id)
        print("MT5 entry response:", r)

        if r is None or not retcode_is_ok(getattr(r, "retcode", -1)):
            print("⚠️ Entry order not completed:", getattr(r, "retcode", None), mt5.last_error())
            return

        ticket = find_position_ticket(SYMBOL, tg_id)
        if ticket:
            msgid_to_ticket[tg_id] = ticket
            print(f"✅ Stored position ticket {ticket} for msg_id {tg_id}")
        else:
            print("⚠️ Entry executed but ticket not found yet. Will try again on edit.")

    except Exception as e:
        print("❌ Failed to place entry:", e)


@client.on(events.MessageEdited(chats=CHANNEL))
async def on_edit(event):
    text = (event.raw_text or "").strip()
    if not text:
        return

    if is_noise_message(text):
        return

    tg_id = event.message.id
    print(f"\n✏️ EDITED SIGNAL (msg_id={tg_id})")
    print(text)

    ticket = msgid_to_ticket.get(tg_id)
    if not ticket:
        ticket = find_position_ticket(SYMBOL, tg_id)
        if ticket:
            msgid_to_ticket[tg_id] = ticket
            print(f"✅ Late-found position ticket {ticket} for msg_id {tg_id}")
        else:
            print("ℹ️ No ticket for this msg_id yet. Ignoring.")
            return

    tp1, sl = parse_tp1_sl(text)
    if tp1 is None or sl is None:
        print("ℹ️ Edit did not contain TP1 and SL yet. Waiting for next edit.")
        return

    side = get_position_side(ticket)
    if not side:
        print("ℹ️ Position not found anymore (maybe already closed).")
        return

    deadline = time.time() + SLTP_RETRY_SECONDS
    last_ret = None

    while time.time() < deadline:
        try:
            adj_sl, adj_tp = clamp_sltp_to_valid(SYMBOL, side, sl, tp1)
            r = set_sltp(SYMBOL, ticket, sl=adj_sl, tp=adj_tp)
            last_ret = r
            print("MT5 modify response:", r)

            rc = getattr(r, "retcode", -1)
            if retcode_is_ok(rc):
                if rc == mt5.TRADE_RETCODE_NO_CHANGES:
                    print(f"✅ SL/TP already set for position {ticket}.")
                else:
                    print(f"✅ Updated position {ticket}: SL={adj_sl}, TP1={adj_tp}")
                return

            if rc == mt5.TRADE_RETCODE_INVALID_STOPS:
                print("⏳ Invalid stops. Retrying...")
                time.sleep(SLTP_RETRY_DELAY)
                continue

            print("⚠️ SL/TP modify failed (non-retry):", rc, mt5.last_error())
            return

        except Exception as e:
            print("❌ Exception while modifying SL/TP:", e)
            time.sleep(SLTP_RETRY_DELAY)

    print("❌ Gave up retrying SL/TP after timeout. Last response:", last_ret)


# ===================== RUN LOOP WITH AUTO-RECONNECT =====================
def run_forever():
    mt5_connect()

    while True:
        try:
            client.start()
            print("Listening to Telegram…")
            client.run_until_disconnected()
        except Exception as e:
            print(f"⚠️ Telegram disconnected or error: {e}")
            print("Reconnecting in 5 seconds...")
            time.sleep(5)


if __name__ == "__main__":
    run_forever()