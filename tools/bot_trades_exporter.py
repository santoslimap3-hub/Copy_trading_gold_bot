#!/usr/bin/env python3
"""
Bot Trades Exporter
Retrieves all trades placed by the bot from MT5 and exports them to JSON.
Each entry in the output represents one complete trade (open → close),
built by pairing DEAL_ENTRY_IN deals with DEAL_ENTRY_OUT deals by position_id.
"""

import MetaTrader5 as mt5
import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set

# ===================== CONFIGURATION =====================
SYMBOL = "XAUUSD"
BOT_MAGIC_NUMBERS = [777, 780]
DAYS_BACK = 365

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
OUTPUT_FILE = os.path.join(PROJECT_ROOT, "data", "bot_trades.json")

# Positions to exclude (e.g. test trades)
BLACKLISTED_POSITIONS: Set[int] = set()

# ===================== HELPERS =====================

_CLOSE_REASON_MAP = {
    0: "MANUAL",    # DEAL_REASON_CLIENT
    1: "MANUAL",    # DEAL_REASON_MOBILE
    2: "MANUAL",    # DEAL_REASON_WEB
    3: "EXPERT",    # DEAL_REASON_EXPERT (EA / robot)
    4: "SL",        # DEAL_REASON_SL
    5: "TP",        # DEAL_REASON_TP
    6: "STOP_OUT",  # DEAL_REASON_SO
}

def _close_reason_str(reason: int) -> str:
    return _CLOSE_REASON_MAP.get(reason, f"OTHER({reason})")


# ===================== MAIN CLASS =====================

class BotTradesExporter:
    def __init__(self, magic_numbers=BOT_MAGIC_NUMBERS, blacklisted_positions=BLACKLISTED_POSITIONS):
        self.initialized = False
        self.magic_numbers = set(magic_numbers)
        self.blacklisted_positions = set(blacklisted_positions)

        self._entry_in  = {mt5.DEAL_ENTRY_IN}
        self._entry_out = {mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY}

        # Raw deals grouped by position_id after fetching
        self._deals_by_position: Dict[int, List[dict]] = {}

        # Final output
        self.completed_trades: List[dict] = []

    # ------------------------------------------------------------------
    def connect_mt5(self) -> bool:
        print("Connecting to MetaTrader5...")
        if not mt5.initialize():
            print(f"Failed to initialize MetaTrader5: {mt5.last_error()}")
            return False

        account = mt5.account_info()
        if account is None:
            print("Failed to get account info")
            return False

        print(f"Account: {account.login} | {account.server} | Balance: ${account.balance:.2f}")
        self.initialized = True
        return True

    # ------------------------------------------------------------------
    def fetch_and_group_deals(self, days_back: int = DAYS_BACK) -> bool:
        """Fetch all MT5 deals and group them by position_id."""
        if not self.initialized:
            print("MT5 not initialized")
            return False

        from_date = datetime.now() - timedelta(days=days_back)
        to_date   = datetime.now() + timedelta(days=1)

        print(f"Fetching deals from the last {days_back} days...")
        deals = mt5.history_deals_get(from_date, to_date)

        if deals is None:
            print(f"Failed to get deals: {mt5.last_error()}")
            return False

        print(f"Retrieved {len(deals)} total deals")

        # Use Bali timezone (UTC+8)
        import pytz
        bali_tz = pytz.timezone('Asia/Makassar')
        for deal in deals:
            position_id = getattr(deal, 'position_id', None)
            if position_id is None:
                continue
            # Convert deal.time (seconds since epoch) to Bali time
            dt_bali = datetime.fromtimestamp(deal.time, tz=pytz.utc).astimezone(bali_tz)

            deal_dict = {
                'ticket':      deal.ticket,
                'order':       deal.order,
                'position_id': position_id,
                'time':        deal.time,
                'time_str':    dt_bali.isoformat(),
                'type':        'BUY' if deal.type == mt5.DEAL_TYPE_BUY else 'SELL',
                'entry':       deal.entry,
                'magic':       deal.magic,
                'reason':      deal.reason,
                'volume':      deal.volume,
                'price':       deal.price,
                'commission':  deal.commission,
                'swap':        deal.swap,
                'profit':      deal.profit,
                'fee':         deal.fee,
                'symbol':      deal.symbol,
                'comment':     deal.comment,
            }
            self._deals_by_position.setdefault(position_id, []).append(deal_dict)

        return True

    # ------------------------------------------------------------------
    def build_completed_trades(self) -> int:
        """
        For each position that was opened by the bot, pair entry and exit deals
        into a single trade record.  Only fully closed positions are included.
        """
        print(f"Building completed trades for magic numbers {list(self.magic_numbers)}...")

        skipped_manual = 0
        skipped_open   = 0
        skipped_black  = 0

        for position_id, deals in self._deals_by_position.items():
            # Skip blacklisted positions
            if position_id in self.blacklisted_positions:
                skipped_black += 1
                continue

            entry_deals = [d for d in deals if d['entry'] in (mt5.DEAL_ENTRY_IN,)]
            exit_deals  = [d for d in deals if d['entry'] in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]

            # The position must have been opened by the bot
            bot_entry = any(d['magic'] in self.magic_numbers for d in entry_deals)
            if not bot_entry:
                continue

            # Skip positions that are still open
            if not exit_deals:
                skipped_open += 1
                continue

            # Skip manually closed trades
            close_reason_raw = exit_deals[-1]['reason']
            if close_reason_raw in (
                mt5.DEAL_REASON_CLIENT,
                mt5.DEAL_REASON_MOBILE,
                mt5.DEAL_REASON_WEB,
            ):
                skipped_manual += 1
                continue

            # Use the earliest entry deal for open info
            entry_deals.sort(key=lambda d: d['time'])
            exit_deals.sort(key=lambda d: d['time'])
            open_deal  = entry_deals[0]
            close_deal = exit_deals[-1]

            # Aggregate financials across all exit deals (handles partial closes)
            total_profit     = sum(d['profit']     for d in exit_deals)
            total_commission = sum(d['commission'] for d in exit_deals)
            total_swap       = sum(d['swap']       for d in exit_deals)
            total_volume     = sum(d['volume']     for d in exit_deals)

            trade = {
                'position_id':   position_id,
                'magic':         open_deal['magic'],
                'symbol':        open_deal['symbol'],
                'direction':     open_deal['type'],        # BUY or SELL
                'volume':        round(total_volume, 2),
                'open_time':     open_deal['time_str'],
                'open_price':    open_deal['price'],
                'open_ticket':   open_deal['ticket'],
                'open_comment':  open_deal['comment'],
                'close_time':    close_deal['time_str'],
                'close_price':   close_deal['price'],
                'close_ticket':  close_deal['ticket'],
                'close_comment': close_deal['comment'],
                'close_reason':  _close_reason_str(close_reason_raw),
                'profit':        round(total_profit, 2),
                'commission':    round(total_commission, 2),
                'swap':          round(total_swap, 2),
                'net_profit':    round(total_profit + total_commission + total_swap, 2),
            }
            self.completed_trades.append(trade)

        # Sort chronologically by close time
        self.completed_trades.sort(key=lambda t: t['close_time'])

        if skipped_black:
            print(f"  Excluded {skipped_black} blacklisted positions")
        if skipped_manual:
            print(f"  Excluded {skipped_manual} manually closed trades")
        if skipped_open:
            print(f"  Skipped {skipped_open} positions still open")
        print(f"Completed trades: {len(self.completed_trades)}")
        return len(self.completed_trades)

    # ------------------------------------------------------------------
    def calculate_metrics(self) -> Dict:
        trades = self.completed_trades
        if not trades:
            return {}

        total  = len(trades)
        wins   = sum(1 for t in trades if t['net_profit'] > 0)
        losses = sum(1 for t in trades if t['net_profit'] < 0)
        be     = total - wins - losses

        gross_profit = sum(t['net_profit'] for t in trades if t['net_profit'] > 0)
        gross_loss   = abs(sum(t['net_profit'] for t in trades if t['net_profit'] < 0))
        net_pnl      = sum(t['net_profit'] for t in trades)

        return {
            'total_trades':       total,
            'buy_trades':         sum(1 for t in trades if t['direction'] == 'BUY'),
            'sell_trades':        sum(1 for t in trades if t['direction'] == 'SELL'),
            'winning_trades':     wins,
            'losing_trades':      losses,
            'breakeven_trades':   be,
            'win_rate_percent':   round(wins / total * 100, 2) if total else 0,
            'gross_profit':       round(gross_profit, 2),
            'gross_loss':         round(gross_loss, 2),
            'net_pnl':            round(net_pnl, 2),
            'total_commission':   round(sum(t['commission'] for t in trades), 2),
            'total_swap':         round(sum(t['swap'] for t in trades), 2),
            'avg_net_per_trade':  round(net_pnl / total, 2) if total else 0,
            'avg_win':            round(gross_profit / wins, 2) if wins else 0,
            'avg_loss':           round(gross_loss / losses, 2) if losses else 0,
            'profit_factor':      round(gross_profit / gross_loss, 2) if gross_loss else 0,
            'close_reason_breakdown': _close_reason_breakdown(trades),
        }

    # ------------------------------------------------------------------
    def save_to_json(self, output_file: str = OUTPUT_FILE) -> bool:
        print(f"Saving to {output_file}...")

        output_data = {
            'metadata': {
                'created_at':    datetime.now().isoformat(),
                'symbol':        SYMBOL,
                'magic_numbers': list(self.magic_numbers),
                'days_back':     DAYS_BACK,
            },
            'summary': self.calculate_metrics(),
            'trades':  self.completed_trades,
        }

        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w') as f:
            json.dump(output_data, f, indent=2, default=str)

        print(f"Saved {len(self.completed_trades)} trades to {output_file}")
        return True

    # ------------------------------------------------------------------
    def print_summary(self):
        if not self.completed_trades:
            print("No completed bot trades found")
            return

        m = self.calculate_metrics()
        print("\n" + "=" * 60)
        print("BOT TRADES SUMMARY")
        print("=" * 60)
        print(f"  Total trades:    {m['total_trades']}  (BUY: {m['buy_trades']} | SELL: {m['sell_trades']})")
        print(f"  Win rate:        {m['win_rate_percent']}%  ({m['winning_trades']}W / {m['losing_trades']}L / {m['breakeven_trades']}BE)")
        print(f"  Net P&L:         ${m['net_pnl']:.2f}")
        print(f"  Gross profit:    ${m['gross_profit']:.2f}")
        print(f"  Gross loss:      ${m['gross_loss']:.2f}")
        print(f"  Profit factor:   {m['profit_factor']:.2f}")
        print(f"  Avg win:         ${m['avg_win']:.2f}")
        print(f"  Avg loss:        ${m['avg_loss']:.2f}")
        print(f"  Commission:      ${m['total_commission']:.2f}")
        print(f"  Swap:            ${m['total_swap']:.2f}")
        print(f"  Close reasons:   {m['close_reason_breakdown']}")
        print("=" * 60)

    # ------------------------------------------------------------------
    def shutdown(self):
        if self.initialized:
            mt5.shutdown()
            print("MetaTrader5 connection closed")


# ===================== HELPERS =====================

def _close_reason_breakdown(trades: List[dict]) -> Dict[str, int]:
    breakdown: Dict[str, int] = {}
    for t in trades:
        r = t['close_reason']
        breakdown[r] = breakdown.get(r, 0) + 1
    return breakdown


# ===================== MAIN =====================

def main():
    exporter = BotTradesExporter(
        magic_numbers=BOT_MAGIC_NUMBERS,
        blacklisted_positions=BLACKLISTED_POSITIONS,
    )

    try:
        if not exporter.connect_mt5():
            return False

        if not exporter.fetch_and_group_deals(days_back=DAYS_BACK):
            print("Error retrieving deals")
            return False

        count = exporter.build_completed_trades()

        if count > 0:
            exporter.save_to_json()
            exporter.print_summary()
        else:
            print("No completed bot trades found to export")

        return True

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        exporter.shutdown()


if __name__ == "__main__":
    exit(0 if main() else 1)
