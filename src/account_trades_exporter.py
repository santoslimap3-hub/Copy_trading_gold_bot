#!/usr/bin/env python3
"""
Full Account Trades Exporter
Retrieves ALL trades from a live MT5 account (not just bot trades)
and exports to JSON for the account-wide dashboard.
"""

import MetaTrader5 as mt5
import json
import os
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from collections import defaultdict

# Fix Unicode output on Windows (cp1252 can't handle emoji)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ===================== CONFIGURATION =====================
DAYS_BACK = 365  # How many days back to retrieve trades

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
OUTPUT_FILE = os.path.join(PROJECT_ROOT, "account_trades.json")


class AccountTradesExporter:
    def __init__(self):
        self.initialized = False
        self.all_deals = []
        self.closed_deals = []
        self.account_info = {}

        self._closed_entries = set()

    def connect_mt5(self) -> bool:
        """Initialize MT5 connection and capture account snapshot."""
        print("🔌 Connecting to MetaTrader5...")

        if not mt5.initialize():
            print(f"❌ Failed to initialize MT5: {mt5.last_error()}")
            return False

        # Cache the entry-type constants now that mt5 is initialised
        self._closed_entries = {mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY}

        print("✅ MetaTrader5 initialized successfully")

        account = mt5.account_info()
        if account is None:
            print("❌ Failed to get account info")
            return False

        # Get open positions for the snapshot
        positions = mt5.positions_get()
        open_positions = []
        floating_pnl = 0.0
        if positions:
            for pos in positions:
                floating_pnl += pos.profit
                open_positions.append({
                    "ticket": pos.ticket,
                    "symbol": pos.symbol,
                    "type": "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL",
                    "volume": pos.volume,
                    "price_open": pos.price_open,
                    "price_current": pos.price_current,
                    "sl": pos.sl,
                    "tp": pos.tp,
                    "profit": round(pos.profit, 2),
                    "swap": round(pos.swap, 2),
                    "magic": pos.magic,
                    "comment": pos.comment,
                    "time": int(pos.time),
                    "time_str": datetime.fromtimestamp(pos.time).isoformat(),
                })

        self.account_info = {
            "login": account.login,
            "server": account.server,
            "currency": account.currency,
            "balance": round(account.balance, 2),
            "equity": round(account.equity, 2),
            "margin": round(account.margin, 2),
            "free_margin": round(account.margin_free, 2),
            "margin_level": round(account.margin_level, 2) if account.margin_level else 0,
            "floating_pnl": round(floating_pnl, 2),
            "leverage": account.leverage,
            "open_positions": open_positions,
            "open_positions_count": len(open_positions),
        }

        print(f"📊 Account: {account.login} @ {account.server}")
        print(f"   Balance: ${account.balance:.2f}  |  Equity: ${account.equity:.2f}")
        print(f"   Open positions: {len(open_positions)}  |  Floating P&L: ${floating_pnl:.2f}")

        self.initialized = True
        return True

    # ---- data fetching ----
    def get_all_deals(self, days_back: int = DAYS_BACK) -> bool:
        if not self.initialized:
            return False

        print(f"\n📥 Fetching ALL deals from the last {days_back} days...")
        from_date = datetime.now() - timedelta(days=days_back)
        # Use a future date as the upper bound to avoid timezone mismatches
        # (MT5 uses the broker's server time, which may differ from local time)
        to_date = datetime.now() + timedelta(days=1)
        deals = mt5.history_deals_get(from_date, to_date)

        if deals is None:
            print(f"❌ Failed: {mt5.last_error()}")
            return False

        if len(deals) == 0:
            print("ℹ️ No deals found")
            return True

        print(f"✅ Retrieved {len(deals)} deals")

        for deal in deals:
            is_closed = deal.entry in self._closed_entries
            d = {
                "ticket": deal.ticket,
                "order": deal.order,
                "position_id": getattr(deal, "position_id", None),
                "time": deal.time,
                "time_str": datetime.fromtimestamp(deal.time).isoformat(),
                "type": "BUY" if deal.type == mt5.DEAL_TYPE_BUY else "SELL",
                "entry": deal.entry,
                "is_closed": is_closed,
                "magic": deal.magic,
                "reason": deal.reason,
                "volume": deal.volume,
                "price": deal.price,
                "commission": deal.commission,
                "swap": deal.swap,
                "profit": deal.profit,
                "fee": deal.fee,
                "symbol": deal.symbol,
                "comment": deal.comment,
            }
            self.all_deals.append(d)

        self.all_deals.sort(key=lambda x: x["time"])

        # Separate closed deals for metrics
        self.closed_deals = [d for d in self.all_deals if d["is_closed"]]
        print(f"   Closed deals: {len(self.closed_deals)}")
        return True

    # ---- metrics ----
    def calculate_metrics(self) -> Dict:
        trades = self.closed_deals
        if not trades:
            return {}

        total = len(trades)
        buys = sum(1 for t in trades if t["type"] == "BUY")
        sells = total - buys

        winners = [t for t in trades if t["profit"] > 0]
        losers = [t for t in trades if t["profit"] < 0]
        breakeven = [t for t in trades if t["profit"] == 0]

        total_profit = sum(t["profit"] for t in winners)
        total_loss = abs(sum(t["profit"] for t in losers))
        net_pnl = sum(t["profit"] for t in trades)
        total_commission = abs(sum(t["commission"] for t in trades))
        total_swap = sum(t["swap"] for t in trades)
        total_fees = abs(sum(t["fee"] for t in trades))
        net_after_costs = net_pnl - total_commission + total_swap - total_fees

        win_rate = (len(winners) / total * 100) if total else 0
        profit_factor = (total_profit / total_loss) if total_loss else 0
        avg_pnl = net_pnl / total if total else 0
        avg_win = total_profit / len(winners) if winners else 0
        avg_loss = total_loss / len(losers) if losers else 0

        # Largest win / loss
        largest_win = max((t["profit"] for t in winners), default=0)
        largest_loss = min((t["profit"] for t in losers), default=0)

        # Consecutive wins / losses
        max_consec_wins = max_consec_losses = cur_w = cur_l = 0
        for t in trades:
            if t["profit"] > 0:
                cur_w += 1; cur_l = 0
                max_consec_wins = max(max_consec_wins, cur_w)
            elif t["profit"] < 0:
                cur_l += 1; cur_w = 0
                max_consec_losses = max(max_consec_losses, cur_l)
            else:
                cur_w = cur_l = 0

        # Balance curve & drawdown
        account_balance = self.account_info.get("balance", 0)
        total_all_pnl = sum(d["profit"] + d["commission"] + d["swap"] for d in trades)
        initial_balance = account_balance - total_all_pnl

        balance_curve = []
        running = initial_balance
        peak = initial_balance
        max_dd = 0
        max_dd_pct = 0

        for t in trades:
            running += t["profit"] + t["commission"] + t["swap"]
            peak = max(peak, running)
            dd = peak - running
            dd_pct = (dd / peak * 100) if peak > 0 else 0
            max_dd = max(max_dd, dd)
            max_dd_pct = max(max_dd_pct, dd_pct)
            balance_curve.append({
                "time": t["time"],
                "time_str": t["time_str"],
                "balance": round(running, 2),
                "pnl": round(t["profit"], 2),
                "drawdown": round(dd, 2),
                "drawdown_pct": round(dd_pct, 2),
            })

        # Daily P&L
        daily = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0, "losses": 0})
        for t in trades:
            day = datetime.fromtimestamp(t["time"]).strftime("%Y-%m-%d")
            daily[day]["pnl"] += t["profit"]
            daily[day]["trades"] += 1
            if t["profit"] > 0:
                daily[day]["wins"] += 1
            elif t["profit"] < 0:
                daily[day]["losses"] += 1

        daily_pnl = [{"date": k, "pnl": round(v["pnl"], 2), "trades": v["trades"],
                       "wins": v["wins"], "losses": v["losses"]}
                      for k, v in sorted(daily.items())]

        profitable_days = sum(1 for d in daily_pnl if d["pnl"] > 0)
        losing_days = sum(1 for d in daily_pnl if d["pnl"] < 0)
        best_day = max(daily_pnl, key=lambda d: d["pnl"]) if daily_pnl else {}
        worst_day = min(daily_pnl, key=lambda d: d["pnl"]) if daily_pnl else {}

        # Symbol breakdown
        sym_map = defaultdict(lambda: {"trades": 0, "pnl": 0, "volume": 0, "wins": 0, "losses": 0})
        for t in trades:
            s = t["symbol"]
            sym_map[s]["trades"] += 1
            sym_map[s]["pnl"] += t["profit"]
            sym_map[s]["volume"] += t["volume"]
            if t["profit"] > 0:
                sym_map[s]["wins"] += 1
            elif t["profit"] < 0:
                sym_map[s]["losses"] += 1

        symbol_breakdown = [
            {"symbol": k, "trades": v["trades"], "pnl": round(v["pnl"], 2),
             "volume": round(v["volume"], 2), "wins": v["wins"], "losses": v["losses"],
             "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0}
            for k, v in sorted(sym_map.items(), key=lambda x: x[1]["pnl"], reverse=True)
        ]

        # Monthly breakdown
        monthly_map = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0, "losses": 0})
        for t in trades:
            month = datetime.fromtimestamp(t["time"]).strftime("%Y-%m")
            monthly_map[month]["pnl"] += t["profit"]
            monthly_map[month]["trades"] += 1
            if t["profit"] > 0:
                monthly_map[month]["wins"] += 1
            elif t["profit"] < 0:
                monthly_map[month]["losses"] += 1

        monthly_breakdown = [
            {"month": k, "pnl": round(v["pnl"], 2), "trades": v["trades"],
             "wins": v["wins"], "losses": v["losses"]}
            for k, v in sorted(monthly_map.items())
        ]

        # Trading-period stats
        first_ts = trades[0]["time"]
        last_ts = trades[-1]["time"]
        trading_days = max((last_ts - first_ts) / 86400, 1)

        return {
            "total_trades": total,
            "buy_trades": buys,
            "sell_trades": sells,
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "breakeven_trades": len(breakeven),
            "win_rate": round(win_rate, 2),
            "total_profit": round(total_profit, 2),
            "total_loss": round(total_loss, 2),
            "net_pnl": round(net_pnl, 2),
            "total_commission": round(total_commission, 2),
            "total_swap": round(total_swap, 2),
            "total_fees": round(total_fees, 2),
            "net_after_costs": round(net_after_costs, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_pnl": round(avg_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "largest_win": round(largest_win, 2),
            "largest_loss": round(largest_loss, 2),
            "max_consecutive_wins": max_consec_wins,
            "max_consecutive_losses": max_consec_losses,
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "initial_balance": round(initial_balance, 2),
            "final_balance": round(running, 2) if balance_curve else round(initial_balance, 2),
            "profitable_days": profitable_days,
            "losing_days": losing_days,
            "best_day": best_day,
            "worst_day": worst_day,
            "trades_per_day": round(total / trading_days, 2),
            "avg_daily_pnl": round(net_pnl / trading_days, 2),
            "trading_days": int(trading_days),
            "first_trade": datetime.fromtimestamp(first_ts).isoformat(),
            "last_trade": datetime.fromtimestamp(last_ts).isoformat(),
            "balance_curve": balance_curve,
            "daily_pnl": daily_pnl,
            "symbol_breakdown": symbol_breakdown,
            "monthly_breakdown": monthly_breakdown,
        }

    # ---- save ----
    def save_to_json(self, output_file: str = OUTPUT_FILE) -> bool:
        print(f"\n💾 Saving full account data to {output_file}...")

        metrics = self.calculate_metrics()

        output = {
            "metadata": {
                "created_at": datetime.now().isoformat(),
                "days_back": DAYS_BACK,
                "exporter": "account_trades_exporter",
            },
            "account": self.account_info,
            "summary": metrics,
            "trades": self.closed_deals,
        }

        os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)

        with open(output_file, "w") as f:
            json.dump(output, f, indent=2, default=str)

        print(f"✅ Saved {len(self.closed_deals)} closed trades to {output_file}")
        return True

    def shutdown(self):
        if self.initialized:
            mt5.shutdown()
            print("👋 MT5 connection closed")


def main():
    exporter = AccountTradesExporter()
    try:
        if not exporter.connect_mt5():
            return False
        if not exporter.get_all_deals(DAYS_BACK):
            return False
        exporter.save_to_json()
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        exporter.shutdown()


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
