#!/usr/bin/env python3
"""
Bot Trades Exporter
Retrieves all trades placed by the bot from MT5 and exports them to JSON.
Uses closed orders as the primary trade list to match MT5 history tickets.
"""

import MetaTrader5 as mt5
import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional

# ===================== CONFIGURATION =====================
SYMBOL = "XAUUSD"
BOT_MAGIC_NUMBERS = [779]  # All bot magic numbers
DAYS_BACK = 365  # How many days back to retrieve trades

# Get the directory of the current script and go up to project root
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
OUTPUT_FILE = os.path.join(PROJECT_ROOT, "data", "bot_trades.json")

# ===================== MAIN CLASS =====================

class BotTradesExporter:
    def __init__(self):
        self.initialized = False
        self.bot_trades = []
        self.bot_orders = []
        self.bot_closed_deals = []
        self.all_deals = []
        self.all_orders = []

        # MT5 deal entry types used to identify closed trades
        self._closed_entries = {
            mt5.DEAL_ENTRY_OUT,
            mt5.DEAL_ENTRY_OUT_BY,
        }

        # Reasons that indicate a trade was closed manually (ignore these)
        self._manual_close_reasons = {
            mt5.DEAL_REASON_CLIENT,   # 0 - closed from desktop terminal
            mt5.DEAL_REASON_MOBILE,   # 1 - closed from mobile app
            mt5.DEAL_REASON_WEB,      # 2 - closed from web platform
        }

        # Order tickets for test trades to permanently exclude (matched by position_id)
        self._blacklisted_positions = {40371931, 40371975, 40371949, 40371717}

    @staticmethod
    def _get_attr(obj, name: str, default=None):
        return getattr(obj, name, default)
    
    def connect_mt5(self) -> bool:
        """Initialize MT5 connection"""
        print("🔌 Connecting to MetaTrader5...")
        
        if not mt5.initialize():
            print(f"❌ failed to initialize MetaTrader5: {mt5.last_error()}")
            return False
        
        print("✅ MetaTrader5 initialized successfully")
        
        # Get account info
        account = mt5.account_info()
        if account is None:
            print("❌ Failed to get account info")
            return False
        
        print(f"📊 Account Information:")
        print(f"   Login: {account.login}")
        print(f"   Server: {account.server}")
        print(f"   Currency: {account.currency}")
        print(f"   Balance: ${account.balance:.2f}")
        print(f"   Equity: ${account.equity:.2f}")
        
        self.initialized = True
        return True
    
    def get_all_deals(self, days_back: int = DAYS_BACK) -> bool:
        """Fetch all deals from MT5"""
        if not self.initialized:
            print("❌ MT5 not initialized")
            return False
        
        print(f"\n📥 Fetching deals from the last {days_back} days...")
        
        # Get deals from the past N days
        from_date = datetime.now() - timedelta(days=days_back)
        # Use end-of-tomorrow to guarantee today's latest deals are captured
        # (MT5 server timezone may differ from local time, and recently closed
        # deals can take a moment to appear in history)
        to_date = datetime.now() + timedelta(days=1)
        
        # Get all deals
        deals = mt5.history_deals_get(from_date, to_date)
        
        if deals is None:
            print(f"❌ Failed to get deals: {mt5.last_error()}")
            return False
        
        if len(deals) == 0:
            print("ℹ️ No deals found for the specified period")
            return True
        
        print(f"✅ Retrieved {len(deals)} total deals")
        
        # Convert to list of dictionaries
        for deal in deals:
            entry_value = deal.entry
            is_closed = entry_value in self._closed_entries
            deal_dict = {
                'ticket': deal.ticket,
                'order': deal.order,
                'position_id': self._get_attr(deal, 'position_id', None),
                'time': deal.time,
                'time_str': datetime.fromtimestamp(deal.time).isoformat(),
                'type': 'BUY' if deal.type == mt5.DEAL_TYPE_BUY else 'SELL',
                'entry': entry_value,
                'is_closed': is_closed,
                'magic': deal.magic,
                'reason': deal.reason,
                'volume': deal.volume,
                'price': deal.price,
                'commission': deal.commission,
                'swap': deal.swap,
                'profit': deal.profit,
                'fee': deal.fee,
                'symbol': deal.symbol,
                'comment': deal.comment,
                'external_id': deal.external_id,
                'source': 'deal',
            }
            self.all_deals.append(deal_dict)
        
        # Sort by time
        self.all_deals.sort(key=lambda x: x['time'])
        
        return True

    def get_all_orders(self, days_back: int = DAYS_BACK) -> bool:
        """Fetch all orders from MT5"""
        if not self.initialized:
            print("❌ MT5 not initialized")
            return False

        print(f"\n📥 Fetching orders from the last {days_back} days...")

        from_date = datetime.now() - timedelta(days=days_back)
        to_date = datetime.now() + timedelta(days=1)
        orders = mt5.history_orders_get(from_date, to_date)

        if orders is None:
            print(f"❌ Failed to get orders: {mt5.last_error()}")
            return False

        if len(orders) == 0:
            print("ℹ️ No orders found for the specified period")
            return True

        print(f"✅ Retrieved {len(orders)} total orders")

        for order in orders:
            time_value = self._get_attr(order, 'time_done', 0) or self._get_attr(order, 'time_setup', 0)
            order_dict = {
                'ticket': order.ticket,
                'order': self._get_attr(order, 'order', order.ticket),
                'time': time_value,
                'time_str': datetime.fromtimestamp(time_value).isoformat() if time_value else None,
                'type': 'BUY' if order.type == mt5.ORDER_TYPE_BUY else 'SELL' if order.type == mt5.ORDER_TYPE_SELL else str(order.type),
                'entry': None,
                'magic': order.magic,
                'reason': self._get_attr(order, 'reason', None),
                'volume': self._get_attr(order, 'volume_current', self._get_attr(order, 'volume_initial', 0.0)),
                'price': self._get_attr(order, 'price_current', self._get_attr(order, 'price_open', 0.0)),
                'commission': self._get_attr(order, 'commission', 0.0),
                'swap': self._get_attr(order, 'swap', 0.0),
                'profit': self._get_attr(order, 'profit', 0.0),
                'fee': self._get_attr(order, 'fee', 0.0),
                'symbol': order.symbol,
                'comment': order.comment,
                'external_id': self._get_attr(order, 'external_id', None),
                'source': 'order',
                'state': self._get_attr(order, 'state', None),
                'time_setup': self._get_attr(order, 'time_setup', None),
                'time_done': self._get_attr(order, 'time_done', None),
                'price_open': self._get_attr(order, 'price_open', None),
                'price_current': self._get_attr(order, 'price_current', None),
                'sl': self._get_attr(order, 'sl', None),
                'tp': self._get_attr(order, 'tp', None),
                'is_closed': bool(self._get_attr(order, 'time_done', 0)),
            }
            self.all_orders.append(order_dict)

        self.all_orders.sort(key=lambda x: x['time'])
        return True
    
    def filter_bot_trades(self) -> int:
        """Filter trades by bot's magic numbers"""
        print(f"\n🤖 Filtering trades with magic numbers {BOT_MAGIC_NUMBERS}...")
        
        # Filter deals by magic number
        self.bot_trades = [
            deal for deal in self.all_deals
            if deal['magic'] in BOT_MAGIC_NUMBERS
            and deal.get('position_id') not in self._blacklisted_positions
        ]

        # Build a set of bot-linked position/order IDs (from entry deals)
        bot_position_ids = {
            deal['position_id'] for deal in self.bot_trades
            if deal.get('position_id')
        }
        bot_order_ids = {
            deal['order'] for deal in self.bot_trades
            if deal.get('order')
        }

        # Closed deals: match by magic number or position/order linkage
        # Exclude blacklisted positions
        all_closed = [
            deal for deal in self.all_deals
            if deal.get('is_closed')
            and deal.get('position_id') not in self._blacklisted_positions
            and (
                deal['magic'] in BOT_MAGIC_NUMBERS
                or deal.get('position_id') in bot_position_ids
                or deal.get('order') in bot_order_ids
            )
        ]
        self.bot_closed_deals = [
            deal for deal in all_closed
            if deal.get('reason') not in self._manual_close_reasons
        ]
        manually_closed = len(all_closed) - len(self.bot_closed_deals)
        blacklisted = sum(
            1 for deal in self.all_deals
            if deal.get('is_closed')
            and deal.get('position_id') in self._blacklisted_positions
        )
        if blacklisted > 0:
            print(f"⚠️ Excluded {blacklisted} blacklisted test trades")
        if manually_closed > 0:
            print(f"⚠️ Excluded {manually_closed} manually closed trades")
        
        print(f"✅ Found {len(self.bot_trades)} bot trades")
        
        if self.bot_trades:
            print(f"\n📋 Bot Trade Summary:")
            print(f"   First trade: {self.bot_trades[0]['time_str']}")
            print(f"   Last trade: {self.bot_trades[-1]['time_str']}")
            
            # Calculate some quick stats
            total_pnl = sum(trade['profit'] for trade in self.bot_trades)
            winning_trades = sum(1 for trade in self.bot_trades if trade['profit'] > 0)
            losing_trades = sum(1 for trade in self.bot_trades if trade['profit'] < 0)
            
            print(f"   Total P&L: ${total_pnl:.2f}")
            print(f"   Winning trades: {winning_trades}")
            print(f"   Losing trades: {losing_trades}")
        
        return len(self.bot_trades)

    def filter_bot_orders(self) -> int:
        """Filter closed orders by bot's magic numbers"""
        print(f"\n🤖 Filtering closed orders with magic numbers {BOT_MAGIC_NUMBERS}...")

        filled_states = {mt5.ORDER_STATE_FILLED, mt5.ORDER_STATE_PARTIAL}
        self.bot_orders = [
            order for order in self.all_orders
            if order['magic'] in BOT_MAGIC_NUMBERS
            and (order.get('state') in filled_states or order.get('is_closed'))
        ]

        print(f"✅ Found {len(self.bot_orders)} closed bot orders")
        return len(self.bot_orders)
    
    def calculate_metrics(self, trades_override=None) -> Dict:
        """Calculate trading metrics for bot trades"""
        trades = trades_override if trades_override is not None else self.bot_closed_deals
        if not trades:
            return {}
        
        print("\n📈 Calculating Bot Trading Metrics...")
        
        # Basic metrics
        total_trades = len(trades)
        buy_trades = sum(1 for d in trades if d['type'] == 'BUY')
        sell_trades = sum(1 for d in trades if d['type'] == 'SELL')
        
        # P&L metrics
        winning_trades = sum(1 for d in trades if d['profit'] > 0)
        losing_trades = sum(1 for d in trades if d['profit'] < 0)
        breakeven_trades = sum(1 for d in trades if d['profit'] == 0)

        total_profit = sum(d['profit'] for d in trades if d['profit'] > 0)
        total_loss = abs(sum(d['profit'] for d in trades if d['profit'] < 0))
        net_pnl = sum(d['profit'] for d in trades)
        total_commission = abs(sum(d['commission'] for d in trades))
        total_swap = sum(d['swap'] for d in trades)
        
        # Win rate
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        # Average P&L per trade
        avg_pnl = net_pnl / total_trades if total_trades > 0 else 0
        avg_profit = total_profit / winning_trades if winning_trades > 0 else 0
        avg_loss = total_loss / losing_trades if losing_trades > 0 else 0
        
        # Profit factor
        profit_factor = total_profit / total_loss if total_loss > 0 else 0
        
        metrics = {
            'total_trades': total_trades,
            'buy_trades': buy_trades,
            'sell_trades': sell_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'breakeven_trades': breakeven_trades,
            'win_rate_percent': round(win_rate, 2),
            'total_profit': round(total_profit, 2),
            'total_loss': round(total_loss, 2),
            'net_pnl': round(net_pnl, 2),
            'total_commission': round(total_commission, 2),
            'total_swap': round(total_swap, 2),
            'avg_pnl_per_trade': round(avg_pnl, 2),
            'avg_profit_per_win': round(avg_profit, 2),
            'avg_loss_per_loss': round(avg_loss, 2),
            'profit_factor': round(profit_factor, 2),
        }
        
        return metrics
    
    def save_to_json(self, output_file: str = OUTPUT_FILE) -> bool:
        """Save bot trades to JSON file."""
        print(f"\n💾 Saving bot trades to JSON...")

        trades_output = list(self.bot_closed_deals)  # copy so we don't mutate
        trade_source = 'deals_closed'
        
        # Prepare the output
        output_data = {
            'metadata': {
                'created_at': datetime.now().isoformat(),
                'bot_version': 'zone',
                'symbol': SYMBOL,
                'magic_numbers': BOT_MAGIC_NUMBERS,
                'days_back': DAYS_BACK,
                'trade_source': trade_source,
                'closed_deals_match': 'magic_or_position_or_order',
            },
            'summary': self.calculate_metrics(trades_override=trades_output),
            'trades': trades_output,
            'deals': self.bot_trades,
            'orders': self.bot_orders,
        }
        
        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        
        # Save to JSON
        with open(output_file, 'w') as f:
            json.dump(output_data, f, indent=2, default=str)
        
        print(f"✅ Bot trades saved to: {output_file}")
        return True
    
    def print_summary(self):
        """Print a summary of the bot trades"""
        trades = self.bot_closed_deals
        if not trades:
            print("\n⚠️ No bot trades found")
            return
        
        metrics = self.calculate_metrics()
        
        print("\n" + "="*60)
        print("🤖 BOT TRADES SUMMARY")
        print("="*60)
        
        print(f"\n📊 TRADE STATISTICS:")
        print(f"   Total Trades: {metrics['total_trades']}")
        print(f"   Buy Trades: {metrics['buy_trades']}")
        print(f"   Sell Trades: {metrics['sell_trades']}")
        
        print(f"\n✅ WINNING STATISTICS:")
        print(f"   Winning Trades: {metrics['winning_trades']}")
        print(f"   Losing Trades: {metrics['losing_trades']}")
        print(f"   Breakeven Trades: {metrics['breakeven_trades']}")
        print(f"   Win Rate: {metrics['win_rate_percent']}%")
        
        print(f"\n💰 PROFIT & LOSS:")
        print(f"   Total Profit: ${metrics['total_profit']:.2f}")
        print(f"   Total Loss: ${metrics['total_loss']:.2f}")
        print(f"   Net P&L: ${metrics['net_pnl']:.2f}")
        print(f"   Commission Paid: ${metrics['total_commission']:.2f}")
        print(f"   Swap: ${metrics['total_swap']:.2f}")
        
        print(f"\n📈 AVERAGES:")
        print(f"   Avg P&L per Trade: ${metrics['avg_pnl_per_trade']:.2f}")
        print(f"   Avg Profit per Win: ${metrics['avg_profit_per_win']:.2f}")
        print(f"   Avg Loss per Loss: ${metrics['avg_loss_per_loss']:.2f}")
        print(f"   Profit Factor: {metrics['profit_factor']:.2f}")
        
        print("\n" + "="*60)
    
    def shutdown(self):
        """Shutdown MT5"""
        if self.initialized:
            mt5.shutdown()
            print("\n👋 MetaTrader5 connection closed")

# ===================== MAIN =====================

def main():
    exporter = BotTradesExporter()
    
    try:
        # Connect to MT5
        if not exporter.connect_mt5():
            return False
        
        # Get all deals
        if not exporter.get_all_deals(days_back=DAYS_BACK):
            print("⚠️ Error retrieving deals")
            return False

        # Get all orders
        if not exporter.get_all_orders(days_back=DAYS_BACK):
            print("⚠️ Error retrieving orders")
            return False
        
        # Filter bot deals and orders
        bot_trade_count = exporter.filter_bot_trades()
        bot_order_count = exporter.filter_bot_orders()
        
        # Save to JSON
        if bot_trade_count > 0 or bot_order_count > 0:
            exporter.save_to_json()
            exporter.print_summary()
        else:
            print("\n⚠️ No bot trades found to export")
        
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
