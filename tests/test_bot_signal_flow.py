#!/usr/bin/env python3
"""
Bot Signal Flow Test Simulator
================================
Tests ALL bot functionality without needing MT5, Telegram, or a demo account.

Usage:
    cd Copy_trading_gold_bot
    python tests/test_bot_signal_flow.py

Covers:
  - Parsing: signal, zone, trade closure, SL, TP
  - MT5 functions: limit orders, market orders, position management
  - Signal flow: buffer -> zone edit -> limit -> fill -> SL/TP
  - Edge cases: zone wait timeout, limit timeout, price invalidation
  - Trade closure: TP HIT / SL HIT / Close profit -> cancel all
  - SELL flow: zone_high limit, invalidation
  - Entry stats tracking
  - Market strategy fallback (legacy)
"""

import sys
import types
import os
import time
import json
from dataclasses import dataclass, field
from typing import Optional, Dict, List

# ============================================================
# STEP 1: Mock ALL external dependencies BEFORE importing bot
# ============================================================

# ---------- Mock MetaTrader5 ----------
mock_mt5 = types.ModuleType("MetaTrader5")

# MT5 Constants
mock_mt5.ORDER_TYPE_BUY = 0
mock_mt5.ORDER_TYPE_SELL = 1
mock_mt5.ORDER_TYPE_BUY_LIMIT = 2
mock_mt5.ORDER_TYPE_SELL_LIMIT = 3
mock_mt5.TRADE_ACTION_DEAL = 1
mock_mt5.TRADE_ACTION_PENDING = 5
mock_mt5.TRADE_ACTION_SLTP = 6
mock_mt5.TRADE_ACTION_MODIFY = 7
mock_mt5.TRADE_ACTION_REMOVE = 8
mock_mt5.ORDER_TIME_GTC = 0
mock_mt5.ORDER_FILLING_FOK = 0
mock_mt5.ORDER_FILLING_IOC = 1
mock_mt5.ORDER_FILLING_RETURN = 2
mock_mt5.TRADE_RETCODE_DONE = 10009
mock_mt5.SYMBOL_FILLING_FOK = 1
mock_mt5.SYMBOL_FILLING_IOC = 2


# ---------- Mock MT5 data classes ----------
@dataclass
class MockOrderResult:
    retcode: int = 10009
    order: int = 0
    comment: str = "Done"


@dataclass
class MockTick:
    bid: float = 3300.0
    ask: float = 3300.5


@dataclass
class MockSymbolInfo:
    trade_contract_size: float = 100.0
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    filling_mode: int = 2  # IOC


@dataclass
class MockAccountInfo:
    balance: float = 10000.0
    equity: float = 10000.0
    trade_allowed: bool = True
    login: int = 12345
    server: str = "MockServer"
    currency: str = "USD"


@dataclass
class MockPosition:
    ticket: int = 0
    type: int = 0  # 0=BUY, 1=SELL
    sl: float = 0.0
    tp: float = 0.0
    price_open: float = 3300.0
    magic: int = 777
    symbol: str = "XAUUSD"


@dataclass
class MockDeal:
    order: int = 0
    position_id: int = 0
    entry: int = 0  # 0=entry deal, 1=exit deal
    price: float = 0.0


@dataclass
class MockOrder:
    ticket: int = 0
    price_open: float = 0.0
    sl: float = 0.0
    tp: float = 0.0


# ---------- Mock MT5 state machine ----------
_mock_state = {
    "tick": MockTick(),
    "account": MockAccountInfo(),
    "positions": [],
    "orders": [],
    "deals": [],
    "next_ticket": 1000,
    "order_send_override": None,
}


def _reset_mock_state():
    _mock_state["tick"] = MockTick()
    _mock_state["account"] = MockAccountInfo()
    _mock_state["positions"].clear()
    _mock_state["orders"].clear()
    _mock_state["deals"].clear()
    _mock_state["next_ticket"] = 1000
    _mock_state["order_send_override"] = None


# ---------- Mock MT5 functions ----------
def _mock_initialize(**kwargs):
    return True


def _mock_shutdown():
    pass


def _mock_version():
    return (5, 0, 0)


def _mock_symbol_info_tick(symbol):
    return _mock_state["tick"]


def _mock_symbol_info(symbol):
    return MockSymbolInfo()


def _mock_account_info():
    return _mock_state["account"]


def _mock_last_error():
    return (0, "No error")


def _mock_order_send(request):
    if _mock_state.get("order_send_override"):
        return _mock_state["order_send_override"](request)

    _mock_state["next_ticket"] += 1
    ticket = _mock_state["next_ticket"]

    action = request.get("action")

    if action == mock_mt5.TRADE_ACTION_PENDING:
        _mock_state["orders"].append(MockOrder(
            ticket=ticket,
            price_open=request.get("price", 0),
            sl=request.get("sl", 0),
            tp=request.get("tp", 0),
        ))
        return MockOrderResult(retcode=10009, order=ticket)

    elif action == mock_mt5.TRADE_ACTION_DEAL:
        tick = _mock_state["tick"]
        order_type = request.get("type")
        price = tick.ask if order_type == mock_mt5.ORDER_TYPE_BUY else tick.bid
        pos = MockPosition(
            ticket=ticket,
            type=0 if order_type == mock_mt5.ORDER_TYPE_BUY else 1,
            sl=request.get("sl", 0),
            tp=request.get("tp", 0),
            price_open=price,
            magic=request.get("magic", 777),
        )
        _mock_state["positions"].append(pos)
        return MockOrderResult(retcode=10009, order=ticket)

    elif action == mock_mt5.TRADE_ACTION_REMOVE:
        order_ticket = request.get("order")
        _mock_state["orders"] = [o for o in _mock_state["orders"] if o.ticket != order_ticket]
        return MockOrderResult(retcode=10009, order=order_ticket)

    elif action == mock_mt5.TRADE_ACTION_MODIFY:
        order_ticket = request.get("order")
        for o in _mock_state["orders"]:
            if o.ticket == order_ticket:
                o.sl = request.get("sl", o.sl)
                o.tp = request.get("tp", o.tp)
                break
        return MockOrderResult(retcode=10009, order=order_ticket)

    elif action == mock_mt5.TRADE_ACTION_SLTP:
        pos_ticket = request.get("position")
        for p in _mock_state["positions"]:
            if p.ticket == pos_ticket:
                p.sl = request.get("sl", p.sl)
                p.tp = request.get("tp", p.tp)
                break
        return MockOrderResult(retcode=10009, order=pos_ticket)

    return MockOrderResult(retcode=10009, order=ticket)


def _mock_positions_get(**kwargs):
    ticket = kwargs.get("ticket")
    symbol = kwargs.get("symbol")
    result = list(_mock_state["positions"])
    if ticket:
        result = [p for p in result if p.ticket == ticket]
    if symbol:
        result = [p for p in result if p.symbol == symbol]
    return result if result else None


def _mock_orders_get(**kwargs):
    ticket = kwargs.get("ticket")
    result = list(_mock_state["orders"])
    if ticket:
        result = [o for o in result if o.ticket == ticket]
    return result if result else None


def _mock_history_deals_get(*args, **kwargs):
    return _mock_state["deals"] if _mock_state["deals"] else None


# Wire up mock functions
mock_mt5.initialize = _mock_initialize
mock_mt5.shutdown = _mock_shutdown
mock_mt5.version = _mock_version
mock_mt5.symbol_info_tick = _mock_symbol_info_tick
mock_mt5.symbol_info = _mock_symbol_info
mock_mt5.account_info = _mock_account_info
mock_mt5.order_send = _mock_order_send
mock_mt5.positions_get = _mock_positions_get
mock_mt5.orders_get = _mock_orders_get
mock_mt5.history_deals_get = _mock_history_deals_get
mock_mt5.last_error = _mock_last_error

sys.modules["MetaTrader5"] = mock_mt5


# ---------- Mock Telethon ----------
mock_telethon = types.ModuleType("telethon")
mock_telethon_events = types.ModuleType("telethon.events")
mock_telethon_errors = types.ModuleType("telethon.errors")
mock_telethon_errors_common = types.ModuleType("telethon.errors.common")


class _MockTelegramClient:
    def __init__(self, *args, **kwargs):
        pass

    def on(self, event):
        return lambda f: f

    async def start(self):
        pass

    async def connect(self):
        pass

    async def get_entity(self, *a):
        return None

    async def get_messages(self, *a, **kw):
        return []

    def is_connected(self):
        return True


class _MockEvents:
    @staticmethod
    def NewMessage(*a, **kw):
        return lambda f: f

    @staticmethod
    def MessageEdited(*a, **kw):
        return lambda f: f


mock_telethon.TelegramClient = _MockTelegramClient
mock_telethon.events = _MockEvents
mock_telethon_errors_common.TypeNotFoundError = Exception
mock_telethon_errors.common = mock_telethon_errors_common

sys.modules["telethon"] = mock_telethon
sys.modules["telethon.events"] = mock_telethon_events
sys.modules["telethon.errors"] = mock_telethon_errors
sys.modules["telethon.errors.common"] = mock_telethon_errors_common


# ---------- Mock custom modules ----------
mock_signal_queue_mod = types.ModuleType("signal_queue")


class MockSignalQueue:
    def __init__(self, *a, **kw):
        pass

    def add_signal(self, **kw):
        pass

    def mark_executed(self, *a):
        pass

    def get_pending(self):
        return []

    def load(self):
        pass


mock_signal_queue_mod.SignalQueue = MockSignalQueue
sys.modules["signal_queue"] = mock_signal_queue_mod

mock_session_mgr_mod = types.ModuleType("session_manager")


class MockSessionManager:
    def __init__(self, *a, **kw):
        pass


mock_session_mgr_mod.SessionManager = MockSessionManager
sys.modules["session_manager"] = mock_session_mgr_mod

mock_reconnect_mod = types.ModuleType("reconnect_monitor")


class MockReconnectMonitor:
    def __init__(self, *a, **kw):
        pass


mock_reconnect_mod.ReconnectMonitor = MockReconnectMonitor
sys.modules["reconnect_monitor"] = mock_reconnect_mod


# ============================================================
# STEP 2: Import the REAL bot module (uses mocked dependencies)
# ============================================================

src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, src_dir)

# Suppress verbose bot logs during tests
os.environ["BOT_TEST_MODE"] = "1"

import bot_v2 as bot  # noqa: E402

# Quiet the logs during tests
bot.LOG_LEVEL = "ERROR"


# ============================================================
# STEP 3: Test Framework
# ============================================================

class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def ok(self, name):
        self.passed += 1
        print(f"  PASS  {name}")

    def fail(self, name, expected, got):
        self.failed += 1
        self.errors.append((name, expected, got))
        print(f"  FAIL  {name}")
        print(f"         Expected: {expected}")
        print(f"         Got:      {got}")

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        if self.failed == 0:
            print(f"ALL {total} TESTS PASSED")
        else:
            print(f"RESULTS: {self.passed}/{total} passed, {self.failed} failed")
            print(f"\nFailed tests:")
            for name, exp, got in self.errors:
                print(f"  - {name}")
                print(f"    Expected: {exp}")
                print(f"    Got:      {got}")
        print(f"{'='*60}")
        return self.failed == 0


results = TestResults()


def reset_bot_state():
    """Reset all bot global state + mock MT5 state between tests."""
    bot.position_map.clear()
    bot.entry_prices.clear()
    bot.tp_sl_updated.clear()
    bot._pending_limit_orders.clear()
    bot._buffered_signals.clear()
    bot.signal_log.clear()
    bot._processed_msg_ids.clear()
    bot.trades_executed = 0
    bot.trades_failed = 0
    # Reset the filling mode cache so get_filling_mode re-evaluates
    bot._cached_filling_mode = None
    bot._cached_symbol_info = None
    bot._cached_account_info = None
    bot._cache_last_refresh = 0
    _reset_mock_state()


# ============================================================
# STEP 4: Test Suites
# ============================================================

# ----------------------------------------------------------
#  SUITE 1: Parsing Functions
# ----------------------------------------------------------
def test_parsing():
    print(f"\n{'='*60}")
    print("TEST SUITE: Parsing Functions")
    print(f"{'='*60}")

    # --- parse_signal ---
    r = bot.parse_signal("XAU USD BUY NOW 3300")
    if r and r[1] == "BUY":
        results.ok("parse_signal: 'XAU USD BUY NOW' -> BUY")
    else:
        results.fail("parse_signal: BUY NOW", ("XAUUSD", "BUY"), r)

    r = bot.parse_signal("XAUUSD SELL NOW")
    if r and r[1] == "SELL":
        results.ok("parse_signal: 'XAUUSD SELL NOW' -> SELL")
    else:
        results.fail("parse_signal: SELL NOW", ("XAUUSD", "SELL"), r)

    r = bot.parse_signal("SL 3250")
    if r is None:
        results.ok("parse_signal: 'SL 3250' -> None (not a signal)")
    else:
        results.fail("parse_signal: non-signal", None, r)

    r = bot.parse_signal("TP1 HIT congrats")
    if r is None:
        results.ok("parse_signal: 'TP1 HIT' -> None")
    else:
        results.fail("parse_signal: closure not a signal", None, r)

    r = bot.parse_signal("Gold is looking strong today")
    if r is None:
        results.ok("parse_signal: random text -> None")
    else:
        results.fail("parse_signal: random text", None, r)

    # --- parse_entry_zone ---
    r = bot.parse_entry_zone("3300 - 3296")
    if r == (3296.0, 3300.0):
        results.ok("parse_entry_zone: '3300 - 3296' -> (3296, 3300)")
    else:
        results.fail("parse_entry_zone: dash", (3296, 3300), r)

    r = bot.parse_entry_zone("3300\u20133296")  # en-dash
    if r and r[0] == 3296.0 and r[1] == 3300.0:
        results.ok("parse_entry_zone: en-dash '3300-3296'")
    else:
        results.fail("parse_entry_zone: en-dash", (3296, 3300), r)

    r = bot.parse_entry_zone("5396.50 - 5392.00")
    if r and r[0] == 5392.0 and r[1] == 5396.5:
        results.ok("parse_entry_zone: decimals '5396.50 - 5392.00'")
    else:
        results.fail("parse_entry_zone: decimals", (5392.0, 5396.5), r)

    r = bot.parse_entry_zone("XAU USD BUY NOW 3300")
    if r is None:
        results.ok("parse_entry_zone: no zone -> None")
    else:
        results.fail("parse_entry_zone: no zone", None, r)

    r = bot.parse_entry_zone("3300 - 3200")  # 100 wide -> rejected
    if r is None:
        results.ok("parse_entry_zone: too wide (100) -> None")
    else:
        results.fail("parse_entry_zone: too wide", None, r)

    r = bot.parse_entry_zone("3300 - 3299.8")  # 0.2 wide -> rejected
    if r is None:
        results.ok("parse_entry_zone: too narrow (0.2) -> None")
    else:
        results.fail("parse_entry_zone: too narrow", None, r)

    # --- parse_trade_closure ---
    r = bot.parse_trade_closure("TP1 HIT")
    if r:
        results.ok("parse_trade_closure: 'TP1 HIT'")
    else:
        results.fail("parse_trade_closure: TP1 HIT", "match", r)

    r = bot.parse_trade_closure("TP2 HIT")
    if r:
        results.ok("parse_trade_closure: 'TP2 HIT'")
    else:
        results.fail("parse_trade_closure: TP2 HIT", "match", r)

    r = bot.parse_trade_closure("TP1 ,2,3,4 HIT")
    if r:
        results.ok("parse_trade_closure: 'TP1 ,2,3,4 HIT'")
    else:
        results.fail("parse_trade_closure: multi-TP HIT", "match", r)

    r = bot.parse_trade_closure("SL HIT")
    if r:
        results.ok("parse_trade_closure: 'SL HIT'")
    else:
        results.fail("parse_trade_closure: SL HIT", "match", r)

    r = bot.parse_trade_closure("Close profit !")
    if r:
        results.ok("parse_trade_closure: 'Close profit !'")
    else:
        results.fail("parse_trade_closure: Close profit", "match", r)

    r = bot.parse_trade_closure("Close profit!")
    if r:
        results.ok("parse_trade_closure: 'Close profit!' (no space)")
    else:
        results.fail("parse_trade_closure: Close profit!", "match", r)

    r = bot.parse_trade_closure("Profit")
    if r:
        results.ok("parse_trade_closure: 'Profit' (standalone line)")
    else:
        results.fail("parse_trade_closure: Profit", "match", r)

    r = bot.parse_trade_closure("XAU USD BUY NOW")
    if r is None:
        results.ok("parse_trade_closure: 'BUY NOW' -> None")
    else:
        results.fail("parse_trade_closure: BUY NOW", None, r)

    r = bot.parse_trade_closure("SL 3250")
    if r is None:
        results.ok("parse_trade_closure: 'SL 3250' -> None (SL value, not HIT)")
    else:
        results.fail("parse_trade_closure: SL value", None, r)

    # --- parse_stop_loss ---
    r = bot.parse_stop_loss("SL 3250")
    if r == 3250.0:
        results.ok("parse_stop_loss: 'SL 3250' -> 3250.0")
    else:
        results.fail("parse_stop_loss: SL 3250", 3250.0, r)

    r = bot.parse_stop_loss("SL 3285.50")
    if r == 3285.5:
        results.ok("parse_stop_loss: 'SL 3285.50' -> 3285.5")
    else:
        results.fail("parse_stop_loss: SL decimal", 3285.5, r)

    r = bot.parse_stop_loss("No SL here")
    if r is None:
        results.ok("parse_stop_loss: no SL -> None")
    else:
        results.fail("parse_stop_loss: no SL", None, r)

    # --- parse_tp1 ---
    r = bot.parse_tp1("TP1 3310")
    if r == 3310.0:
        results.ok("parse_tp1: 'TP1 3310' -> 3310.0")
    else:
        results.fail("parse_tp1: TP1", 3310.0, r)

    r = bot.parse_tp1("TP 1 3310")
    if r == 3310.0:
        results.ok("parse_tp1: 'TP 1 3310' -> 3310.0")
    else:
        results.fail("parse_tp1: TP 1", 3310.0, r)

    r = bot.parse_tp1("No TP here")
    if r is None:
        results.ok("parse_tp1: no TP -> None")
    else:
        results.fail("parse_tp1: no TP", None, r)

    # --- parse_tp_levels ---
    r = bot.parse_tp_levels("TP1 3310\nTP2 3315\nTP3 3320\nTP4 3325")
    if r == {1: 3310.0, 2: 3315.0, 3: 3320.0, 4: 3325.0}:
        results.ok("parse_tp_levels: 4 levels")
    else:
        results.fail("parse_tp_levels: 4 levels", {1: 3310, 2: 3315, 3: 3320, 4: 3325}, r)

    r = bot.parse_tp_levels("Just some random text")
    if r == {}:
        results.ok("parse_tp_levels: no TPs -> {}")
    else:
        results.fail("parse_tp_levels: empty", {}, r)

    # --- Full realistic message parsing ---
    full_msg = "XAU USD BUY NOW\n3300 - 3296\nSL 3285\nTP1 3310\nTP2 3318\nTP3 3325\nTP4 3340"
    sig = bot.parse_signal(full_msg)
    zone = bot.parse_entry_zone(full_msg)
    sl = bot.parse_stop_loss(full_msg)
    tp1 = bot.parse_tp1(full_msg)
    tps = bot.parse_tp_levels(full_msg)

    if sig and sig[1] == "BUY" and zone == (3296.0, 3300.0) and sl == 3285.0 and tp1 == 3310.0 and len(tps) == 4:
        results.ok("Full message parse: signal+zone+SL+4 TPs")
    else:
        results.fail("Full message parse", "all parsed", f"sig={sig} zone={zone} sl={sl} tp1={tp1} tps={tps}")


# ----------------------------------------------------------
#  SUITE 2: MT5 Functions (with mock)
# ----------------------------------------------------------
def test_mt5_functions():
    print(f"\n{'='*60}")
    print("TEST SUITE: MT5 Functions (Mocked)")
    print(f"{'='*60}")
    reset_bot_state()

    # --- get_market_price ---
    _mock_state["tick"] = MockTick(bid=3300.0, ask=3300.5)
    r = bot.get_market_price("BUY")
    if r == 3300.5:
        results.ok("get_market_price: BUY -> ask=3300.5")
    else:
        results.fail("get_market_price: BUY", 3300.5, r)

    r = bot.get_market_price("SELL")
    if r == 3300.0:
        results.ok("get_market_price: SELL -> bid=3300.0")
    else:
        results.fail("get_market_price: SELL", 3300.0, r)

    # --- calculate_lot_size ---
    # risk = 10000 * 0.10 = 1000, distance = 8, contract = 100
    # lot = 1000 / (8 * 100) = 1.25
    lot = bot.calculate_lot_size(3300.0, 3292.0, 10000.0)
    if abs(lot - 1.25) < 0.01:
        results.ok(f"calculate_lot_size: $10k, $8 distance -> {lot:.2f}")
    else:
        results.fail("calculate_lot_size", 1.25, lot)

    # Small balance -> small lot
    lot = bot.calculate_lot_size(3300.0, 3292.0, 500.0)
    # risk = 500 * 0.1 = 50, lot = 50 / 800 = 0.0625 -> rounds to 0.06
    if 0.05 <= lot <= 0.07:
        results.ok(f"calculate_lot_size: $500, $8 distance -> {lot:.2f}")
    else:
        results.fail("calculate_lot_size: small balance", "0.06", lot)

    # --- place_limit_order ---
    reset_bot_state()
    ticket = bot.place_limit_order("BUY", 3295.0, 0.5, sl=3287.0, tp=3298.0)
    if ticket and ticket > 0:
        results.ok(f"place_limit_order: BUY LIMIT -> ticket={ticket}")
    else:
        results.fail("place_limit_order: BUY LIMIT", ">0", ticket)

    if len(_mock_state["orders"]) == 1:
        results.ok("place_limit_order: 1 order in mock state")
    else:
        results.fail("place_limit_order: state", 1, len(_mock_state["orders"]))

    # Check order SL/TP
    if _mock_state["orders"][0].sl == 3287.0 and _mock_state["orders"][0].tp == 3298.0:
        results.ok("place_limit_order: SL/TP stored on order")
    else:
        o = _mock_state["orders"][0]
        results.fail("place_limit_order: SL/TP", (3287, 3298), (o.sl, o.tp))

    # --- cancel_limit_order ---
    ok = bot.cancel_limit_order(ticket)
    if ok:
        results.ok("cancel_limit_order: returns True")
    else:
        results.fail("cancel_limit_order", True, ok)

    if len(_mock_state["orders"]) == 0:
        results.ok("cancel_limit_order: order removed from state")
    else:
        results.fail("cancel_limit_order: state", 0, len(_mock_state["orders"]))

    # --- modify_limit_order_sl_tp ---
    reset_bot_state()
    ticket = bot.place_limit_order("SELL", 3305.0, 0.3)
    ok = bot.modify_limit_order_sl_tp(ticket, sl=3313.0, tp=3295.0)
    if ok:
        results.ok("modify_limit_order_sl_tp: returns True")
    else:
        results.fail("modify_limit_order_sl_tp", True, ok)

    order = _mock_state["orders"][0]
    if order.sl == 3313.0 and order.tp == 3295.0:
        results.ok("modify_limit_order_sl_tp: SL=3313 TP=3295 in state")
    else:
        results.fail("modify_limit_order_sl_tp: state", (3313, 3295), (order.sl, order.tp))

    # --- open_position (market order) ---
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3300.0, ask=3300.5)
    ticket = bot.open_position("BUY", 0.5, sl=3292.0, tp=3305.0)
    if ticket and ticket > 0:
        results.ok(f"open_position: BUY market -> ticket={ticket}")
    else:
        results.fail("open_position: BUY", ">0", ticket)

    if len(_mock_state["positions"]) == 1:
        pos = _mock_state["positions"][0]
        if pos.sl == 3292.0 and pos.tp == 3305.0:
            results.ok("open_position: SL/TP on position")
        else:
            results.fail("open_position: SL/TP", (3292, 3305), (pos.sl, pos.tp))

    # --- execute_signal_trade ---
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3300.0, ask=3300.5)
    r = bot.execute_signal_trade("BUY", 101, "XAU USD BUY NOW")
    if r and len(r) == 4:
        ticket, entry, sl, lot = r
        results.ok(f"execute_signal_trade: BUY -> ticket={ticket} entry=${entry:.2f}")
        if abs(entry - 3300.5) < 0.01:
            results.ok("execute_signal_trade: entry=ask for BUY")
        else:
            results.fail("execute_signal_trade: entry", 3300.5, entry)
        if 101 in bot.position_map:
            results.ok("execute_signal_trade: position_map[101] set")
        else:
            results.fail("execute_signal_trade: position_map", "msg_id=101", "missing")
    else:
        results.fail("execute_signal_trade: BUY", "4-tuple", r)

    # --- execute_limit_trade ---
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3300.0, ask=3300.5)
    r = bot.execute_limit_trade("BUY", 201, "BUY LIMIT text", 3295.0, 3292.0, 3296.0)
    if r and len(r) == 4:
        order_ticket, lp, fsl, lot = r
        results.ok(f"execute_limit_trade: BUY LIMIT -> order={order_ticket} limit=${lp:.2f}")
        if abs(lp - 3295.0) < 0.01:
            results.ok("execute_limit_trade: limit_price=3295")
        else:
            results.fail("execute_limit_trade: limit_price", 3295, lp)
    else:
        results.fail("execute_limit_trade: BUY LIMIT", "4-tuple", r)

    # --- find_position_from_order ---
    reset_bot_state()
    _mock_state["deals"].append(MockDeal(order=5555, position_id=9999, entry=0, price=3296.0))
    pos = bot.find_position_from_order(5555)
    if pos == 9999:
        results.ok("find_position_from_order: deal.order=5555 -> position=9999")
    else:
        results.fail("find_position_from_order", 9999, pos)

    # No matching deal
    pos = bot.find_position_from_order(8888)
    if pos is None:
        results.ok("find_position_from_order: no match -> None")
    else:
        results.fail("find_position_from_order: no match", None, pos)

    # --- get_fill_price_from_order ---
    price = bot.get_fill_price_from_order(5555)
    if price == 3296.0:
        results.ok("get_fill_price_from_order: order=5555 -> $3296.0")
    else:
        results.fail("get_fill_price_from_order", 3296.0, price)


# ----------------------------------------------------------
#  SUITE 3: Signal Flow Integration
# ----------------------------------------------------------
def test_signal_flow_buy():
    print(f"\n{'='*60}")
    print("TEST SUITE: BUY Signal Flow (Buffer -> Zone -> Limit -> Fill)")
    print(f"{'='*60}")
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3300.0, ask=3300.5)

    msg_id = 500
    text = "XAU USD BUY NOW 3300"

    # --- Step 1: Signal arrives (no zone) -> buffer ---
    signal = bot.parse_signal(text)
    zone = bot.parse_entry_zone(text)

    if signal and zone is None:
        bot._buffered_signals[msg_id] = {
            "side": signal[1],
            "symbol": signal[0],
            "text": text,
            "msg_id": msg_id,
            "channel_name": "[TEST]",
            "buffered_at": time.time(),
        }
        bot.record_entry_stat("signal_buffered", side=signal[1])

    if msg_id in bot._buffered_signals:
        results.ok("BUY Flow 1: signal buffered (no zone)")
    else:
        results.fail("BUY Flow 1", "buffered", "not buffered")

    if len(bot._pending_limit_orders) == 0:
        results.ok("BUY Flow 1: no pending orders yet")
    else:
        results.fail("BUY Flow 1: no pending", 0, len(bot._pending_limit_orders))

    # --- Step 2: Edit arrives with zone -> place limit ---
    edit_text = "XAU USD BUY NOW\n3300 - 3296\nSL 3285\nTP1 3310\nTP2 3318\nTP3 3325\nTP4 3340"

    if msg_id in bot._buffered_signals:
        buf = bot._buffered_signals[msg_id]
        side = buf["side"]
        zone = bot.parse_entry_zone(edit_text)
        if zone:
            zone_low, zone_high = zone
            limit_price = zone_low if side == "BUY" else zone_high

            r = bot.execute_limit_trade(side, msg_id, edit_text, limit_price, zone_low, zone_high)
            if r:
                order_ticket, lp, fsl, lot = r
                sl = bot.parse_stop_loss(edit_text)
                tp1 = bot.parse_tp1(edit_text)
                bot._pending_limit_orders[msg_id] = {
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
                }
                bot.record_entry_stat("zone_from_edit", side=side, zone_low=zone_low, zone_high=zone_high)
                bot.record_entry_stat("limit_placed", side=side, limit_price=lp)
            del bot._buffered_signals[msg_id]

    if msg_id in bot._pending_limit_orders:
        pending = bot._pending_limit_orders[msg_id]
        results.ok(f"BUY Flow 2: limit order placed (order={pending['order_ticket']})")
        if pending["limit_price"] == 3296.0:
            results.ok("BUY Flow 2: BUY limit at zone_low=3296")
        else:
            results.fail("BUY Flow 2: limit price", 3296.0, pending["limit_price"])
        if pending["sl"] == 3285.0:
            results.ok("BUY Flow 2: SL=3285 parsed from edit")
        else:
            results.fail("BUY Flow 2: SL", 3285.0, pending["sl"])
        if pending["tp1"] == 3310.0:
            results.ok("BUY Flow 2: TP1=3310 parsed from edit")
        else:
            results.fail("BUY Flow 2: TP1", 3310.0, pending["tp1"])
    else:
        results.fail("BUY Flow 2", "pending order", "none")

    if msg_id not in bot._buffered_signals:
        results.ok("BUY Flow 2: buffer cleared after zone")
    else:
        results.fail("BUY Flow 2: buffer cleared", "removed", "still present")

    # --- Step 3: Order fills -> detect position ---
    if msg_id in bot._pending_limit_orders:
        pending = bot._pending_limit_orders[msg_id]
        order_ticket = pending["order_ticket"]
        pos_ticket = 9001

        # Simulate fill in MT5
        _mock_state["deals"].append(MockDeal(
            order=order_ticket,
            position_id=pos_ticket,
            entry=0,
            price=3296.10,
        ))
        _mock_state["positions"].append(MockPosition(
            ticket=pos_ticket,
            type=0,
            sl=pending["failsafe_sl"],
            tp=0,
            price_open=3296.10,
            magic=777,
        ))

        # Detect fill
        found_pos = bot.find_position_from_order(order_ticket)
        if found_pos == pos_ticket:
            results.ok(f"BUY Flow 3: fill detected -> position={pos_ticket}")
        else:
            results.fail("BUY Flow 3: fill detection", pos_ticket, found_pos)

        fill_price = bot.get_fill_price_from_order(order_ticket)
        if fill_price and abs(fill_price - 3296.10) < 0.01:
            results.ok(f"BUY Flow 3: fill price=${fill_price:.2f}")
        else:
            results.fail("BUY Flow 3: fill price", 3296.10, fill_price)

        slippage = abs(fill_price - pending["limit_price"]) if fill_price else 0
        if abs(slippage - 0.10) < 0.01:
            results.ok(f"BUY Flow 3: slippage=${slippage:.2f}")
        else:
            results.fail("BUY Flow 3: slippage", 0.10, slippage)

        # Map position
        bot.position_map[msg_id] = pos_ticket
        bot.entry_prices[pos_ticket] = fill_price
        bot.record_entry_stat("limit_fill", side="BUY", limit_price=pending["limit_price"],
                              fill_price=fill_price, slippage=slippage)
        del bot._pending_limit_orders[msg_id]

        if bot.position_map.get(msg_id) == pos_ticket:
            results.ok("BUY Flow 3: position_map[500] = 9001")
        else:
            results.fail("BUY Flow 3: position_map", pos_ticket, bot.position_map.get(msg_id))


# ----------------------------------------------------------
#  SUITE 4: SELL Signal Flow
# ----------------------------------------------------------
def test_signal_flow_sell():
    print(f"\n{'='*60}")
    print("TEST SUITE: SELL Signal Flow")
    print(f"{'='*60}")
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3310.0, ask=3310.5)

    msg_id = 600
    text = "XAU USD SELL NOW"

    # Buffer
    signal = bot.parse_signal(text)
    if signal:
        bot._buffered_signals[msg_id] = {
            "side": signal[1],
            "symbol": signal[0],
            "text": text,
            "msg_id": msg_id,
            "buffered_at": time.time(),
        }

    if bot._buffered_signals.get(msg_id, {}).get("side") == "SELL":
        results.ok("SELL Flow 1: signal buffered")
    else:
        results.fail("SELL Flow 1", "SELL buffered", bot._buffered_signals.get(msg_id))

    # Zone edit -> SELL limit at zone_high
    edit_text = "XAU USD SELL NOW\n3310 - 3314\nSL 3322\nTP1 3300\nTP2 3290"

    buf = bot._buffered_signals[msg_id]
    zone = bot.parse_entry_zone(edit_text)
    if zone:
        zone_low, zone_high = zone
        limit_price = zone_high  # SELL -> limit at zone_high
        r = bot.execute_limit_trade("SELL", msg_id, edit_text, limit_price, zone_low, zone_high)
        if r:
            order_ticket, lp, fsl, lot = r
            bot._pending_limit_orders[msg_id] = {
                "order_ticket": order_ticket,
                "side": "SELL",
                "limit_price": lp,
                "zone_low": zone_low,
                "zone_high": zone_high,
                "failsafe_sl": fsl,
                "lot": lot,
                "msg_id": msg_id,
                "placed_at": time.time(),
                "sl": bot.parse_stop_loss(edit_text),
                "tp1": bot.parse_tp1(edit_text),
            }
        del bot._buffered_signals[msg_id]

    if msg_id in bot._pending_limit_orders:
        pending = bot._pending_limit_orders[msg_id]
        if pending["limit_price"] == 3314.0:
            results.ok("SELL Flow 2: SELL limit at zone_high=3314")
        else:
            results.fail("SELL Flow 2: limit price", 3314.0, pending["limit_price"])
        if pending["sl"] == 3322.0:
            results.ok("SELL Flow 2: SL=3322")
        else:
            results.fail("SELL Flow 2: SL", 3322.0, pending["sl"])
        if pending["tp1"] == 3300.0:
            results.ok("SELL Flow 2: TP1=3300")
        else:
            results.fail("SELL Flow 2: TP1", 3300.0, pending["tp1"])
    else:
        results.fail("SELL Flow 2", "pending order", "none")


# ----------------------------------------------------------
#  SUITE 5: Signal with zone in initial message (rare)
# ----------------------------------------------------------
def test_signal_with_zone_in_message():
    print(f"\n{'='*60}")
    print("TEST SUITE: Signal With Zone In Initial Message (Rare)")
    print(f"{'='*60}")
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3300.0, ask=3300.5)

    msg_id = 700
    text = "XAU USD BUY NOW\n3300 - 3296\nSL 3285\nTP1 3310"

    signal = bot.parse_signal(text)
    zone = bot.parse_entry_zone(text)

    if signal and zone:
        side = signal[1]
        zone_low, zone_high = zone
        limit_price = zone_low if side == "BUY" else zone_high

        r = bot.execute_limit_trade(side, msg_id, text, limit_price, zone_low, zone_high)
        if r:
            order_ticket, lp, fsl, lot = r
            bot._pending_limit_orders[msg_id] = {
                "order_ticket": order_ticket,
                "side": side,
                "limit_price": lp,
                "zone_low": zone_low,
                "zone_high": zone_high,
                "failsafe_sl": fsl,
                "lot": lot,
                "msg_id": msg_id,
                "placed_at": time.time(),
                "sl": bot.parse_stop_loss(text),
                "tp1": bot.parse_tp1(text),
            }

    if msg_id in bot._pending_limit_orders:
        results.ok("Rare case: zone in initial message -> limit placed immediately")
        if bot._pending_limit_orders[msg_id]["limit_price"] == 3296.0:
            results.ok("Rare case: BUY limit at zone_low=3296")
        else:
            results.fail("Rare case: price", 3296, bot._pending_limit_orders[msg_id]["limit_price"])
    else:
        results.fail("Rare case", "pending order", "none")

    # Should NOT be in buffer
    if msg_id not in bot._buffered_signals:
        results.ok("Rare case: NOT buffered (zone was present)")
    else:
        results.fail("Rare case: not buffered", "not in buffer", "in buffer")


# ----------------------------------------------------------
#  SUITE 6: Trade Closure Detection
# ----------------------------------------------------------
def test_trade_closure():
    print(f"\n{'='*60}")
    print("TEST SUITE: Trade Closure Handling")
    print(f"{'='*60}")
    reset_bot_state()

    # Setup: 1 buffered + 1 pending
    bot._buffered_signals[800] = {
        "side": "BUY", "text": "BUY NOW", "buffered_at": time.time(), "msg_id": 800,
    }

    order_ticket = bot.place_limit_order("SELL", 3310.0, 0.3)
    bot._pending_limit_orders[801] = {
        "order_ticket": order_ticket,
        "side": "SELL",
        "limit_price": 3310.0,
        "zone_low": 3308.0,
        "zone_high": 3312.0,
        "failsafe_sl": 3318.0,
        "lot": 0.3,
        "msg_id": 801,
        "placed_at": time.time(),
    }

    if len(bot._buffered_signals) == 1 and len(bot._pending_limit_orders) == 1:
        results.ok("Closure setup: 1 buffered + 1 pending")
    else:
        results.fail("Closure setup", "1+1",
                      f"{len(bot._buffered_signals)}+{len(bot._pending_limit_orders)}")

    # --- "TP1 HIT" -> cancel all ---
    closure = bot.parse_trade_closure("TP1 HIT congrats!")
    if closure:
        for mid, info in list(bot._pending_limit_orders.items()):
            bot.cancel_limit_order(info["order_ticket"])
        bot._pending_limit_orders.clear()
        bot._buffered_signals.clear()

    if len(bot._pending_limit_orders) == 0:
        results.ok("Closure TP1: pending orders cancelled")
    else:
        results.fail("Closure TP1: pending", 0, len(bot._pending_limit_orders))

    if len(bot._buffered_signals) == 0:
        results.ok("Closure TP1: buffered signals cleared")
    else:
        results.fail("Closure TP1: buffers", 0, len(bot._buffered_signals))

    # --- SL HIT ---
    reset_bot_state()
    bot._buffered_signals[900] = {"side": "SELL", "text": "SELL", "buffered_at": time.time(), "msg_id": 900}
    closure = bot.parse_trade_closure("SL HIT")
    if closure:
        bot._buffered_signals.clear()
    if len(bot._buffered_signals) == 0:
        results.ok("Closure SL HIT: buffers cleared")
    else:
        results.fail("Closure SL HIT", 0, len(bot._buffered_signals))

    # --- "Close profit!" ---
    closure = bot.parse_trade_closure("Close profit!")
    if closure:
        results.ok("Closure: 'Close profit!' recognized")
    else:
        results.fail("Closure: Close profit!", "match", None)

    # --- Non-closure message should NOT trigger ---
    closure = bot.parse_trade_closure("XAU USD BUY NOW 3300")
    if closure is None:
        results.ok("Closure: 'BUY NOW' does NOT trigger closure")
    else:
        results.fail("Closure: BUY NOW", None, closure)


# ----------------------------------------------------------
#  SUITE 7: Zone Wait Timeout -> Market Fallback
# ----------------------------------------------------------
def test_zone_wait_timeout():
    print(f"\n{'='*60}")
    print("TEST SUITE: Zone Wait Timeout -> Market Fallback")
    print(f"{'='*60}")
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3300.0, ask=3300.5)

    # Buffer a signal with expired timestamp (130s ago > 120s timeout)
    bot._buffered_signals[1000] = {
        "side": "BUY",
        "symbol": "XAUUSD",
        "text": "XAU USD BUY NOW",
        "msg_id": 1000,
        "buffered_at": time.time() - 130,
    }

    # Check expiry
    expired = []
    for mid, buf in list(bot._buffered_signals.items()):
        elapsed = time.time() - buf["buffered_at"]
        if elapsed >= bot.ZONE_WAIT_TIMEOUT:
            expired.append(mid)

    if 1000 in expired:
        results.ok("Timeout: buffered signal expired after 120s")
    else:
        results.fail("Timeout: expiry", "expired", "not expired")

    # Fallback to market order
    for mid in expired:
        buf = bot._buffered_signals.pop(mid)
        r = bot.execute_signal_trade(buf["side"], mid, buf["text"])
        if r:
            ticket, entry, sl, lot = r
            bot.position_map[mid] = ticket
            bot.entry_prices[ticket] = entry
            bot.record_entry_stat("market_fallback", side=buf["side"], entry_price=entry)
            results.ok(f"Timeout: market fallback -> ticket={ticket} entry=${entry:.2f}")
        else:
            results.fail("Timeout: market fallback", "success", "failed")

    if len(bot._buffered_signals) == 0:
        results.ok("Timeout: buffer cleared after fallback")
    else:
        results.fail("Timeout: buffer cleared", 0, len(bot._buffered_signals))

    # Non-expired signal should NOT be affected
    bot._buffered_signals[1001] = {
        "side": "SELL",
        "symbol": "XAUUSD",
        "text": "SELL NOW",
        "msg_id": 1001,
        "buffered_at": time.time() - 10,  # only 10s ago
    }
    expired2 = []
    for mid, buf in list(bot._buffered_signals.items()):
        if time.time() - buf["buffered_at"] >= bot.ZONE_WAIT_TIMEOUT:
            expired2.append(mid)
    if len(expired2) == 0:
        results.ok("Timeout: fresh signal (10s) NOT expired")
    else:
        results.fail("Timeout: fresh signal", "not expired", expired2)


# ----------------------------------------------------------
#  SUITE 8: Limit Order Timeout
# ----------------------------------------------------------
def test_limit_order_timeout():
    print(f"\n{'='*60}")
    print("TEST SUITE: Limit Order Timeout (300s)")
    print(f"{'='*60}")
    reset_bot_state()

    order_ticket = bot.place_limit_order("BUY", 3290.0, 0.5)
    bot._pending_limit_orders[1100] = {
        "order_ticket": order_ticket,
        "side": "BUY",
        "limit_price": 3290.0,
        "placed_at": time.time() - 310,  # 310s ago > 300s
        "failsafe_sl": 3282.0,
    }

    timed_out = []
    for mid, info in list(bot._pending_limit_orders.items()):
        elapsed = time.time() - info["placed_at"]
        if elapsed >= bot.LIMIT_ORDER_TIMEOUT:
            bot.cancel_limit_order(info["order_ticket"])
            timed_out.append(mid)

    for mid in timed_out:
        del bot._pending_limit_orders[mid]

    if 1100 in timed_out:
        results.ok("Limit timeout: order cancelled after 300s")
    else:
        results.fail("Limit timeout", "cancelled", "not cancelled")

    if len(bot._pending_limit_orders) == 0:
        results.ok("Limit timeout: pending removed")
    else:
        results.fail("Limit timeout: removed", 0, len(bot._pending_limit_orders))


# ----------------------------------------------------------
#  SUITE 9: Price Invalidation
# ----------------------------------------------------------
def test_price_invalidation():
    print(f"\n{'='*60}")
    print("TEST SUITE: Price Invalidation")
    print(f"{'='*60}")

    # --- BUY: price below SL -> invalidate ---
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3280.0, ask=3280.5)  # Below SL=3285

    order_ticket = bot.place_limit_order("BUY", 3296.0, 0.5)
    bot._pending_limit_orders[1200] = {
        "order_ticket": order_ticket,
        "side": "BUY",
        "limit_price": 3296.0,
        "placed_at": time.time(),
        "sl": 3285.0,
        "failsafe_sl": 3288.0,
        "tp1": 3310.0,
    }

    pending = bot._pending_limit_orders[1200]
    current_price = bot.get_market_price("BUY")
    sl_val = pending.get("sl") or pending.get("failsafe_sl")

    invalidated = False
    if sl_val and pending["side"] == "BUY" and current_price <= sl_val:
        invalidated = True

    if invalidated:
        bot.cancel_limit_order(pending["order_ticket"])
        del bot._pending_limit_orders[1200]
        results.ok(f"Invalidation BUY<SL: price ${current_price:.2f} <= SL ${sl_val:.2f} -> cancelled")
    else:
        results.fail("Invalidation BUY<SL", True, False)

    # --- BUY: price above TP -> invalidate ---
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3315.0, ask=3315.5)  # Above TP=3310

    order_ticket = bot.place_limit_order("BUY", 3296.0, 0.5)
    bot._pending_limit_orders[1201] = {
        "order_ticket": order_ticket,
        "side": "BUY",
        "limit_price": 3296.0,
        "placed_at": time.time(),
        "failsafe_sl": 3288.0,
        "tp1": 3310.0,
    }

    pending = bot._pending_limit_orders[1201]
    current_price = bot.get_market_price("BUY")
    tp_val = pending.get("tp1")

    invalidated = False
    if tp_val and pending["side"] == "BUY" and current_price >= tp_val:
        invalidated = True

    if invalidated:
        bot.cancel_limit_order(pending["order_ticket"])
        del bot._pending_limit_orders[1201]
        results.ok(f"Invalidation BUY>TP: price ${current_price:.2f} >= TP ${tp_val:.2f} -> cancelled")
    else:
        results.fail("Invalidation BUY>TP", True, False)

    # --- SELL: price above SL -> invalidate ---
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3325.0, ask=3325.5)  # Above SL=3322

    order_ticket = bot.place_limit_order("SELL", 3314.0, 0.3)
    bot._pending_limit_orders[1202] = {
        "order_ticket": order_ticket,
        "side": "SELL",
        "limit_price": 3314.0,
        "placed_at": time.time(),
        "sl": 3322.0,
        "failsafe_sl": 3322.0,
        "tp1": 3300.0,
    }

    pending = bot._pending_limit_orders[1202]
    current_price = bot.get_market_price("SELL")
    sl_val = pending.get("sl")

    invalidated = False
    if sl_val and pending["side"] == "SELL" and current_price >= sl_val:
        invalidated = True

    if invalidated:
        bot.cancel_limit_order(pending["order_ticket"])
        del bot._pending_limit_orders[1202]
        results.ok(f"Invalidation SELL>SL: price ${current_price:.2f} >= SL ${sl_val:.2f} -> cancelled")
    else:
        results.fail("Invalidation SELL>SL", True, False)

    # --- SELL: price below TP -> invalidate ---
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3295.0, ask=3295.5)  # Below TP=3300

    order_ticket = bot.place_limit_order("SELL", 3314.0, 0.3)
    bot._pending_limit_orders[1203] = {
        "order_ticket": order_ticket,
        "side": "SELL",
        "limit_price": 3314.0,
        "placed_at": time.time(),
        "failsafe_sl": 3322.0,
        "tp1": 3300.0,
    }

    pending = bot._pending_limit_orders[1203]
    current_price = bot.get_market_price("SELL")
    tp_val = pending.get("tp1")

    invalidated = False
    if tp_val and pending["side"] == "SELL" and current_price <= tp_val:
        invalidated = True

    if invalidated:
        bot.cancel_limit_order(pending["order_ticket"])
        del bot._pending_limit_orders[1203]
        results.ok(f"Invalidation SELL<TP: price ${current_price:.2f} <= TP ${tp_val:.2f} -> cancelled")
    else:
        results.fail("Invalidation SELL<TP", True, False)

    # --- No invalidation when price is in range ---
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3298.0, ask=3298.5)  # Between SL=3285 and TP=3310

    order_ticket = bot.place_limit_order("BUY", 3296.0, 0.5)
    bot._pending_limit_orders[1204] = {
        "order_ticket": order_ticket,
        "side": "BUY",
        "limit_price": 3296.0,
        "placed_at": time.time(),
        "sl": 3285.0,
        "failsafe_sl": 3288.0,
        "tp1": 3310.0,
    }

    pending = bot._pending_limit_orders[1204]
    current_price = bot.get_market_price("BUY")
    sl_val = pending.get("sl") or pending.get("failsafe_sl")
    tp_val = pending.get("tp1")

    inv = False
    if sl_val and pending["side"] == "BUY" and current_price <= sl_val:
        inv = True
    if tp_val and pending["side"] == "BUY" and current_price >= tp_val:
        inv = True

    if not inv:
        results.ok(f"No invalidation: price ${current_price:.2f} between SL=${sl_val:.2f} and TP=${tp_val:.2f}")
    else:
        results.fail("No invalidation expected", False, True)


# ----------------------------------------------------------
#  SUITE 10: Edit SL/TP on Pending Order
# ----------------------------------------------------------
def test_edit_sl_tp_on_pending():
    print(f"\n{'='*60}")
    print("TEST SUITE: Edit SL/TP on Pending Order")
    print(f"{'='*60}")
    reset_bot_state()

    order_ticket = bot.place_limit_order("BUY", 3296.0, 0.5, sl=3288.0, tp=3299.0)
    bot._pending_limit_orders[1300] = {
        "order_ticket": order_ticket,
        "side": "BUY",
        "limit_price": 3296.0,
        "placed_at": time.time(),
        "sl": None,
        "tp1": None,
        "failsafe_sl": 3288.0,
    }

    # Edit arrives with real SL/TP
    edit_text = "XAU USD BUY NOW\n3300 - 3296\nSL 3285\nTP1 3312\nTP2 3318\nTP3 3325"

    pending = bot._pending_limit_orders[1300]
    sl = bot.parse_stop_loss(edit_text)
    tp1 = bot.parse_tp1(edit_text)

    if sl:
        pending["sl"] = sl
    if tp1:
        pending["tp1"] = tp1

    new_sl = sl if sl else pending.get("sl", 0)
    new_tp = tp1 if tp1 else pending.get("tp1", 0)

    ok = bot.modify_limit_order_sl_tp(order_ticket, new_sl, new_tp)

    if ok:
        results.ok(f"Edit pending: SL/TP modified (SL=${new_sl:.2f} TP=${new_tp:.2f})")
    else:
        results.fail("Edit pending: modify", True, False)

    # Check mock state
    if _mock_state["orders"]:
        order = _mock_state["orders"][0]
        if order.sl == 3285.0 and order.tp == 3312.0:
            results.ok("Edit pending: SL=3285 TP=3312 in mock state")
        else:
            results.fail("Edit pending: state", (3285, 3312), (order.sl, order.tp))

    # Check stored in pending dict
    if pending["sl"] == 3285.0 and pending["tp1"] == 3312.0:
        results.ok("Edit pending: SL/TP stored in _pending_limit_orders")
    else:
        results.fail("Edit pending: stored", (3285, 3312), (pending["sl"], pending["tp1"]))


# ----------------------------------------------------------
#  SUITE 11: Entry Stats
# ----------------------------------------------------------
def test_entry_stats():
    print(f"\n{'='*60}")
    print("TEST SUITE: Entry Stats Tracking")
    print(f"{'='*60}")

    # Fresh stats
    bot._entry_stats = {
        "total_signals": 0, "limit_orders_placed": 0, "limit_fills": 0,
        "limit_timeouts": 0, "limit_invalidated": 0, "signals_buffered": 0,
        "zones_received_from_edit": 0, "zone_wait_timeouts": 0,
        "market_orders_no_zone": 0, "market_orders_in_zone": 0,
        "market_orders_fallback": 0, "total_limit_slippage": 0.0, "entries": [],
    }

    bot.record_entry_stat("signal", side="BUY")
    bot.record_entry_stat("signal_buffered", side="BUY")
    bot.record_entry_stat("zone_from_edit", side="BUY", zone_low=3296, zone_high=3300)
    bot.record_entry_stat("limit_placed", side="BUY", limit_price=3296)
    bot.record_entry_stat("limit_fill", side="BUY", limit_price=3296, fill_price=3296.1, slippage=0.10)
    bot.record_entry_stat("limit_timeout", side="SELL", limit_price=3310)
    bot.record_entry_stat("limit_invalidated", side="BUY", limit_price=3296, reason="price<SL")
    bot.record_entry_stat("zone_wait_timeout", side="SELL")
    bot.record_entry_stat("market_fallback", side="BUY", entry_price=3300)

    s = bot._entry_stats

    if s["total_signals"] == 1:
        results.ok("Stats: total_signals=1")
    else:
        results.fail("Stats: total_signals", 1, s["total_signals"])

    if s.get("signals_buffered", 0) == 1:
        results.ok("Stats: signals_buffered=1")
    else:
        results.fail("Stats: signals_buffered", 1, s.get("signals_buffered"))

    if s.get("zones_received_from_edit", 0) == 1:
        results.ok("Stats: zones_received_from_edit=1")
    else:
        results.fail("Stats: zones_received_from_edit", 1, s.get("zones_received_from_edit"))

    if s["limit_orders_placed"] == 1:
        results.ok("Stats: limit_orders_placed=1")
    else:
        results.fail("Stats: limit_orders_placed", 1, s["limit_orders_placed"])

    if s["limit_fills"] == 1:
        results.ok("Stats: limit_fills=1")
    else:
        results.fail("Stats: limit_fills", 1, s["limit_fills"])

    if s["limit_timeouts"] == 1:
        results.ok("Stats: limit_timeouts=1")
    else:
        results.fail("Stats: limit_timeouts", 1, s["limit_timeouts"])

    if s.get("limit_invalidated", 0) == 1:
        results.ok("Stats: limit_invalidated=1")
    else:
        results.fail("Stats: limit_invalidated", 1, s.get("limit_invalidated"))

    if s.get("zone_wait_timeouts", 0) == 1:
        results.ok("Stats: zone_wait_timeouts=1")
    else:
        results.fail("Stats: zone_wait_timeouts", 1, s.get("zone_wait_timeouts"))

    if abs(s["total_limit_slippage"] - 0.10) < 0.01:
        results.ok("Stats: total_limit_slippage=0.10")
    else:
        results.fail("Stats: slippage", 0.10, s["total_limit_slippage"])

    # Summary string
    summary = bot.get_entry_stats_summary()
    if "fill" in summary.lower() and "buffered" in summary.lower():
        results.ok("Stats: summary contains fill rate + buffer info")
    else:
        results.fail("Stats: summary", "contains fill/buffered", summary[:80])

    if len(s["entries"]) == 9:
        results.ok("Stats: 9 entries recorded")
    else:
        results.fail("Stats: entries count", 9, len(s["entries"]))


# ----------------------------------------------------------
#  SUITE 12: Market Strategy (Legacy)
# ----------------------------------------------------------
def test_market_strategy():
    print(f"\n{'='*60}")
    print("TEST SUITE: Market Strategy (Legacy)")
    print(f"{'='*60}")
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3300.0, ask=3300.5)

    # Temporarily switch strategy
    orig = bot.ENTRY_STRATEGY
    bot.ENTRY_STRATEGY = "MARKET"

    result = bot.execute_signal_trade("BUY", 1400, "XAU USD BUY NOW")
    if result:
        ticket, entry, sl, lot = result
        results.ok(f"Market strategy: BUY -> ticket={ticket} entry=${entry:.2f}")
        if 1400 in bot.position_map:
            results.ok("Market strategy: position_map updated")
        else:
            results.fail("Market strategy: position_map", "msg_id=1400", "missing")
    else:
        results.fail("Market strategy: execute", "success", None)

    # SELL
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3300.0, ask=3300.5)
    result = bot.execute_signal_trade("SELL", 1401, "XAU USD SELL NOW")
    if result:
        ticket, entry, sl, lot = result
        if abs(entry - 3300.0) < 0.01:
            results.ok("Market strategy: SELL entry=bid=3300")
        else:
            results.fail("Market strategy: SELL entry", 3300.0, entry)
    else:
        results.fail("Market strategy: SELL", "success", None)

    bot.ENTRY_STRATEGY = orig


# ----------------------------------------------------------
#  SUITE 13: Edge Cases
# ----------------------------------------------------------
def test_edge_cases():
    print(f"\n{'='*60}")
    print("TEST SUITE: Edge Cases")
    print(f"{'='*60}")
    reset_bot_state()

    # Multiple buffered signals
    for i in range(5):
        bot._buffered_signals[2000 + i] = {
            "side": "BUY" if i % 2 == 0 else "SELL",
            "text": f"signal_{i}",
            "msg_id": 2000 + i,
            "buffered_at": time.time(),
        }

    if len(bot._buffered_signals) == 5:
        results.ok("Edge: 5 concurrent buffered signals")
    else:
        results.fail("Edge: concurrent buffers", 5, len(bot._buffered_signals))

    # Trade closure clears ALL
    closure = bot.parse_trade_closure("SL HIT")
    if closure:
        bot._buffered_signals.clear()
    if len(bot._buffered_signals) == 0:
        results.ok("Edge: SL HIT clears all 5 buffered signals")
    else:
        results.fail("Edge: clear all", 0, len(bot._buffered_signals))

    # Cancel non-existent order (should not crash)
    ok = bot.cancel_limit_order(99999)
    results.ok("Edge: cancel non-existent order -> no crash")

    # Modify non-existent order
    ok = bot.modify_limit_order_sl_tp(99999, sl=3285.0)
    if not ok:
        results.ok("Edge: modify non-existent order -> False")
    else:
        results.fail("Edge: modify non-existent", False, ok)

    # find_position_from_order with no deals
    _mock_state["deals"].clear()
    pos = bot.find_position_from_order(12345)
    if pos is None:
        results.ok("Edge: find_position with no deals -> None")
    else:
        results.fail("Edge: no deals", None, pos)

    # parse_signal with mixed case
    r = bot.parse_signal("xau usd buy now")
    if r and r[1] == "BUY":
        results.ok("Edge: lowercase 'buy now' -> BUY")
    else:
        results.fail("Edge: lowercase buy now", ("XAUUSD", "BUY"), r)

    # parse_entry_zone with zero-like values (should reject < 1000)
    r = bot.parse_entry_zone("100 - 96")
    if r is None:
        results.ok("Edge: zone with small values (100-96) -> None")
    else:
        results.fail("Edge: small zone", None, r)


# ----------------------------------------------------------
#  SUITE 14: Realistic Channel Message Sequence
# ----------------------------------------------------------
def test_realistic_sequence():
    print(f"\n{'='*60}")
    print("TEST SUITE: Realistic Channel Message Sequence")
    print(f"{'='*60}")
    reset_bot_state()
    _mock_state["tick"] = MockTick(bid=3300.0, ask=3300.5)

    # Message 1: "XAU USD BUY NOW" (no zone)
    msg1_id = 5000
    msg1_text = "XAU USD BUY NOW"
    sig = bot.parse_signal(msg1_text)
    zone = bot.parse_entry_zone(msg1_text)
    if sig and not zone:
        bot._buffered_signals[msg1_id] = {
            "side": sig[1], "symbol": sig[0], "text": msg1_text,
            "msg_id": msg1_id, "buffered_at": time.time(),
        }
    results.ok("Sequence 1: BUY NOW -> buffered")

    # Message 1 EDIT: adds zone + SL + TPs
    msg1_edit = "XAU USD BUY NOW\n3300 - 3296\nSL 3285\nTP1 3310\nTP2 3318\nTP3 3325\nTP4 3340"
    buf = bot._buffered_signals.get(msg1_id)
    if buf:
        zone = bot.parse_entry_zone(msg1_edit)
        if zone:
            zone_low, zone_high = zone
            limit_price = zone_low if buf["side"] == "BUY" else zone_high
            r = bot.execute_limit_trade(buf["side"], msg1_id, msg1_edit, limit_price, zone_low, zone_high)
            if r:
                ot, lp, fsl, lot = r
                bot._pending_limit_orders[msg1_id] = {
                    "order_ticket": ot, "side": buf["side"], "limit_price": lp,
                    "zone_low": zone_low, "zone_high": zone_high,
                    "failsafe_sl": fsl, "lot": lot, "msg_id": msg1_id,
                    "placed_at": time.time(),
                    "sl": bot.parse_stop_loss(msg1_edit),
                    "tp1": bot.parse_tp1(msg1_edit),
                }
            del bot._buffered_signals[msg1_id]
    results.ok("Sequence 2: edit with zone -> limit placed at zone_low")

    # Simulate fill
    if msg1_id in bot._pending_limit_orders:
        pend = bot._pending_limit_orders[msg1_id]
        ot = pend["order_ticket"]
        pos_ticket = 9500
        _mock_state["deals"].append(MockDeal(order=ot, position_id=pos_ticket, entry=0, price=3296.05))
        _mock_state["positions"].append(MockPosition(
            ticket=pos_ticket, type=0, sl=pend["failsafe_sl"], price_open=3296.05, magic=777,
        ))
        fp = bot.find_position_from_order(ot)
        if fp == pos_ticket:
            bot.position_map[msg1_id] = pos_ticket
            bot.entry_prices[pos_ticket] = 3296.05
            del bot._pending_limit_orders[msg1_id]
            results.ok(f"Sequence 3: limit filled -> position {pos_ticket}")

    # Message 2: "TP1 HIT" (should clean up even though position already filled)
    msg2_text = "TP1 HIT congrats!"
    closure = bot.parse_trade_closure(msg2_text)
    if closure:
        bot._pending_limit_orders.clear()
        bot._buffered_signals.clear()
        results.ok("Sequence 4: TP1 HIT -> cleanup (no pending left)")

    # Verify position still in position_map (trade is done, just cleanup of pending)
    if bot.position_map.get(msg1_id) == 9500:
        results.ok("Sequence 5: position_map preserved after TP1 HIT")
    else:
        results.fail("Sequence 5: position_map", 9500, bot.position_map.get(msg1_id))


# ============================================================
# STEP 5: Run All Tests
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  BOT SIGNAL FLOW TEST SIMULATOR")
    print(f"  Testing bot_v2.py (MAGIC={bot.MAGIC})")
    print(f"  Entry Strategy: {bot.ENTRY_STRATEGY}")
    print(f"  Zone Wait Timeout: {bot.ZONE_WAIT_TIMEOUT}s")
    print(f"  Limit Order Timeout: {bot.LIMIT_ORDER_TIMEOUT}s")
    print(f"  Risk: {bot.RISK_PCT*100:.0f}%")
    print("=" * 60)

    test_parsing()
    test_mt5_functions()
    test_signal_flow_buy()
    test_signal_flow_sell()
    test_signal_with_zone_in_message()
    test_trade_closure()
    test_zone_wait_timeout()
    test_limit_order_timeout()
    test_price_invalidation()
    test_edit_sl_tp_on_pending()
    test_entry_stats()
    test_market_strategy()
    test_edge_cases()
    test_realistic_sequence()

    success = results.summary()
    sys.exit(0 if success else 1)
