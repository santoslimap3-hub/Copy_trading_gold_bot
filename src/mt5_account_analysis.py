#!/usr/bin/env python3
"""
MetaTrader5 Account Analysis Tool
Analyzes trading history, generates account balance over time graph,
and calculates PnL metrics.
"""

import MetaTrader5 as mt5
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
import json
from typing import Tuple, List, Dict
import sys
import os

# ===================== CONFIGURATION =====================
SYMBOL = "XAUUSD"
# Get the directory of the current script and go up to project root
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data")

# ===================== MAIN CLASS =====================

class MT5AccountAnalyzer:
    def __init__(self):
        self.initialized = False
        self.deals = []
        self.balance_history = []
        self.equity_history = []
        
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
        
        print(f"\n📊 Account Information:")
        print(f"   Login: {account.login}")
        print(f"   Server: {account.server}")
        print(f"   Currency: {account.currency}")
        print(f"   Initial Balance: ${account.balance:.2f}")
        print(f"   Current Balance: ${account.balance:.2f}")
        print(f"   Current Equity: ${account.equity:.2f}")
        print(f"   Floating P&L: ${account.profit:.2f}")
        
        self.initialized = True
        return True
    
    def get_all_deals(self, days_back: int = 365) -> bool:
        """Fetch all deals from the last N days"""
        if not self.initialized:
            print("❌ MT5 not initialized")
            return False
        
        print(f"\n📥 Fetching deals from the last {days_back} days...")
        
        # Get deals from the past N days
        from_date = datetime.now() - timedelta(days=days_back)
        
        # Get all deals
        deals = mt5.history_deals_get(from_date, datetime.now())
        
        if deals is None:
            print(f"❌ Failed to get deals: {mt5.last_error()}")
            return False
        
        if len(deals) == 0:
            print("ℹ️ No deals found for the specified period")
            return False
        
        print(f"✅ Retrieved {len(deals)} deals")
        
        # Convert to list of dictionaries
        self.deals = []
        for deal in deals:
            deal_dict = {
                'ticket': deal.ticket,
                'order': deal.order,
                'time': deal.time,
                'time_msc': deal.time_msc,
                'type': 'BUY' if deal.type == mt5.DEAL_TYPE_BUY else 'SELL',
                'entry': deal.entry,
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
            }
            self.deals.append(deal_dict)
        
        # Sort by time
        self.deals.sort(key=lambda x: x['time'])
        
        print(f"\n📋 Deal Summary:")
        print(f"   First deal: {datetime.fromtimestamp(self.deals[0]['time'])}")
        print(f"   Last deal: {datetime.fromtimestamp(self.deals[-1]['time'])}")
        
        return True
    
    def calculate_balance_over_time(self) -> bool:
        """Calculate account balance over time based on deals"""
        if not self.deals:
            print("❌ No deals to process")
            return False
        
        print("\n💹 Calculating balance progression...")
        
        # Get initial balance from first deal
        # We'll need to work backwards to find the initial balance
        account = mt5.account_info()
        current_balance = account.balance
        
        # Calculate total P&L from all deals
        total_pnl = sum(deal['profit'] for deal in self.deals)
        initial_balance = current_balance - total_pnl
        
        print(f"   Initial Balance: ${initial_balance:.2f}")
        print(f"   Current Balance: ${current_balance:.2f}")
        print(f"   Total P&L: ${total_pnl:.2f}")
        
        # Build balance history
        running_balance = initial_balance
        self.balance_history = []
        
        for deal in self.deals:
            running_balance += deal['profit'] + deal['commission'] + deal['swap']
            self.balance_history.append({
                'timestamp': datetime.fromtimestamp(deal['time']),
                'balance': running_balance,
                'deal_ticket': deal['ticket'],
                'pnl': deal['profit'],
                'commission': deal['commission'],
                'swap': deal['swap'],
                'type': deal['type'],
                'symbol': deal['symbol'],
            })
        
        return True
    
    def calculate_metrics(self) -> Dict:
        """Calculate trading metrics"""
        if not self.deals or not self.balance_history:
            return {}
        
        print("\n📈 Calculating Trading Metrics...")
        
        # Basic metrics
        total_deals = len(self.deals)
        buy_deals = sum(1 for d in self.deals if d['type'] == 'BUY')
        sell_deals = sum(1 for d in self.deals if d['type'] == 'SELL')
        
        # P&L metrics
        winning_deals = sum(1 for d in self.deals if d['profit'] > 0)
        losing_deals = sum(1 for d in self.deals if d['profit'] < 0)
        breakeven_deals = sum(1 for d in self.deals if d['profit'] == 0)
        
        total_profit = sum(d['profit'] for d in self.deals if d['profit'] > 0)
        total_loss = abs(sum(d['profit'] for d in self.deals if d['profit'] < 0))
        net_pnl = sum(d['profit'] for d in self.deals)
        total_commission = abs(sum(d['commission'] for d in self.deals))
        total_swap = sum(d['swap'] for d in self.deals)
        
        # Win rate
        win_rate = (winning_deals / total_deals * 100) if total_deals > 0 else 0
        
        # Profit factor
        profit_factor = total_profit / total_loss if total_loss > 0 else 0
        
        # Average P&L per trade
        avg_pnl = net_pnl / total_deals if total_deals > 0 else 0
        avg_profit = total_profit / winning_deals if winning_deals > 0 else 0
        avg_loss = total_loss / losing_deals if losing_deals > 0 else 0
        
        # Time-based metrics
        first_trade = self.balance_history[0]['timestamp']
        last_trade = self.balance_history[-1]['timestamp']
        trading_days = (last_trade - first_trade).days
        trading_days = max(trading_days, 1)  # Avoid division by zero
        
        avg_pnl_per_day = net_pnl / trading_days
        trades_per_day = total_deals / trading_days
        
        metrics = {
            'total_deals': total_deals,
            'buy_deals': buy_deals,
            'sell_deals': sell_deals,
            'winning_deals': winning_deals,
            'losing_deals': losing_deals,
            'breakeven_deals': breakeven_deals,
            'win_rate_percent': win_rate,
            'total_profit': total_profit,
            'total_loss': total_loss,
            'net_pnl': net_pnl,
            'total_commission': total_commission,
            'total_swap': total_swap,
            'profit_factor': profit_factor,
            'avg_pnl_per_trade': avg_pnl,
            'avg_profit_per_win': avg_profit,
            'avg_loss_per_loss': avg_loss,
            'avg_pnl_per_day': avg_pnl_per_day,
            'trades_per_day': trades_per_day,
            'trading_days': trading_days,
            'first_trade': first_trade.isoformat(),
            'last_trade': last_trade.isoformat(),
        }
        
        return metrics
    
    def print_metrics(self, metrics: Dict):
        """Print formatted metrics"""
        print("\n" + "="*60)
        print("📊 TRADING PERFORMANCE METRICS")
        print("="*60)
        
        print(f"\n📋 TRADES:")
        print(f"   Total Trades: {metrics['total_deals']}")
        print(f"   Buy Trades: {metrics['buy_deals']}")
        print(f"   Sell Trades: {metrics['sell_deals']}")
        print(f"   Trades per Day: {metrics['trades_per_day']:.2f}")
        
        print(f"\n✅ WINNING STATISTICS:")
        print(f"   Winning Trades: {metrics['winning_deals']}")
        print(f"   Losing Trades: {metrics['losing_deals']}")
        print(f"   Breakeven Trades: {metrics['breakeven_deals']}")
        print(f"   Win Rate: {metrics['win_rate_percent']:.2f}%")
        
        print(f"\n💰 PROFIT & LOSS:")
        print(f"   Total Profit: ${metrics['total_profit']:.2f}")
        print(f"   Total Loss: ${metrics['total_loss']:.2f}")
        print(f"   Net P&L: ${metrics['net_pnl']:.2f}")
        print(f"   Commission Paid: ${metrics['total_commission']:.2f}")
        print(f"   Swap: ${metrics['total_swap']:.2f}")
        
        print(f"\n📊 AVERAGES:")
        print(f"   Avg P&L per Trade: ${metrics['avg_pnl_per_trade']:.2f}")
        print(f"   Avg Profit per Win: ${metrics['avg_profit_per_win']:.2f}")
        print(f"   Avg Loss per Loss: ${metrics['avg_loss_per_loss']:.2f}")
        print(f"   Avg P&L per Day: ${metrics['avg_pnl_per_day']:.2f}")
        
        print(f"\n📈 EFFICIENCY:")
        print(f"   Profit Factor: {metrics['profit_factor']:.2f}")
        print(f"   Trading Period: {metrics['trading_days']} days")
        print(f"   From: {metrics['first_trade']}")
        print(f"   To: {metrics['last_trade']}")
        
        print("\n" + "="*60)
    
    def generate_chart(self, output_file: str = None):
        """Generate balance history chart"""
        if output_file is None:
            output_file = os.path.join(OUTPUT_DIR, "account_balance_history.png")
        
        if not self.balance_history:
            print("❌ No balance history to chart")
            return False
        
        print(f"\n📊 Generating chart...")
        
        # Create DataFrame
        df = pd.DataFrame(self.balance_history)
        
        # Create figure with subplots
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
        
        # Plot 1: Account Balance Over Time
        ax1.plot(df['timestamp'], df['balance'], linewidth=2, label='Account Balance', color='#1f77b4')
        ax1.fill_between(df['timestamp'], df['balance'], alpha=0.3, color='#1f77b4')
        ax1.set_ylabel('Balance ($)', fontsize=12, fontweight='bold')
        ax1.set_title('Account Balance Over Time', fontsize=14, fontweight='bold', pad=20)
        ax1.grid(True, alpha=0.3)
        ax1.legend(fontsize=11)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        ax1.xaxis.set_major_locator(mdates.AutoDateLocator())
        fig.autofmt_xdate()
        
        # Add balance statistics to the plot
        min_balance = df['balance'].min()
        max_balance = df['balance'].max()
        final_balance = df['balance'].iloc[-1]
        ax1.axhline(y=min_balance, color='red', linestyle='--', alpha=0.5, label=f'Min: ${min_balance:.2f}')
        ax1.axhline(y=max_balance, color='green', linestyle='--', alpha=0.5, label=f'Max: ${max_balance:.2f}')
        ax1.legend(fontsize=10)
        
        # Plot 2: Daily P&L
        df['date'] = df['timestamp'].dt.date
        daily_pnl = df.groupby('date')['pnl'].sum()
        colors = ['green' if x > 0 else 'red' for x in daily_pnl]
        ax2.bar(daily_pnl.index, daily_pnl.values, color=colors, alpha=0.7)
        ax2.set_ylabel('Daily P&L ($)', fontsize=12, fontweight='bold')
        ax2.set_xlabel('Date', fontsize=12, fontweight='bold')
        ax2.set_title('Daily P&L', fontsize=14, fontweight='bold', pad=20)
        ax2.grid(True, alpha=0.3, axis='y')
        ax2.axhline(y=0, color='black', linestyle='-', linewidth=0.8)
        fig.autofmt_xdate(rotation=45)
        
        plt.tight_layout()
        
        # Save figure
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        print(f"✅ Chart saved to: {output_file}")
        
        # Show the chart
        plt.show()
        
        return True
    
    def save_analysis_json(self, metrics: Dict, output_file: str = None):
        """Save analysis results to JSON"""
        if output_file is None:
            output_file = os.path.join(OUTPUT_DIR, "account_analysis.json")
        
        print(f"\n💾 Saving analysis to JSON...")
        
        analysis = {
            'metadata': {
                'analysis_date': datetime.now().isoformat(),
                'symbol': SYMBOL,
            },
            'metrics': metrics,
            'balance_history': self.balance_history[:100],  # Save first 100 for reference
            'deals_count': len(self.deals),
        }
        
        with open(output_file, 'w') as f:
            json.dump(analysis, f, indent=2, default=str)
        
        print(f"✅ Analysis saved to: {output_file}")
        return True
    
    def shutdown(self):
        """Shutdown MT5"""
        if self.initialized:
            mt5.shutdown()
            print("👋 MetaTrader5 connection closed")

# ===================== MAIN =====================

def main():
    analyzer = MT5AccountAnalyzer()
    
    try:
        # Connect to MT5
        if not analyzer.connect_mt5():
            sys.exit(1)
        
        # Get all deals
        if not analyzer.get_all_deals(days_back=365):
            print("⚠️ No deals found, but continuing with analysis...")
        
        # Calculate balance history
        if not analyzer.calculate_balance_over_time():
            sys.exit(1)
        
        # Calculate metrics
        metrics = analyzer.calculate_metrics()
        if metrics:
            analyzer.print_metrics(metrics)
            analyzer.save_analysis_json(metrics)
        
        # Generate chart
        analyzer.generate_chart()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        analyzer.shutdown()

if __name__ == "__main__":
    main()
