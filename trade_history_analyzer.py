"""
Trade History Analyzer
Extracts trading signals and results from Telegram messages,
analyzes performance, and saves to JSON with calculated metrics.
"""

import re
import json
import asyncio
import sys
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

from telethon import TelegramClient

# Fix Windows encoding for emoji support
if sys.platform == "win32":
    os.system("chcp 65001 > nul")

# ===================== TELEGRAM CONFIG =====================
api_id = 34597981
api_hash = "2cd59609b6cacb56da261e43fdb897ea"
CHANNEL = -1003349563414  # EGN GOLD (Live Trading)

# ===================== PATTERNS =====================
# Main trade signal pattern: "XAU USD BUY NOW 4703 - 4699" or "XAU USD SELL NOW 4..."
TRADE_SIGNAL_PATTERN = r"(XAU USD|XAUUSD)\s+(BUY|SELL)\s+NOW\s+([\d.]+)\s*-\s*([\d.]+)"

# TP/SL patterns: "TP1 4705", "SL 4695", etc.
TP_PATTERN = r"TP(\d+)\s+([\d.]+)"
SL_PATTERN = r"SL\s+([\d.]+)"

# Result patterns: "TP1 HIT", "TP2 HIT", "TP1 HIT again", "SL 4790"
TP_HIT_PATTERN = r"TP(\d+)\s+HIT"
SL_HIT_PATTERN = r"SL\s+([\d.]+)"


class TradeAnalyzer:
    def __init__(self, api_id: int, api_hash: str, channel_id: int):
        self.api_id = api_id
        self.api_hash = api_hash
        self.channel_id = channel_id
        self.client = TelegramClient('analyzer_session_telethon', api_id=api_id, api_hash=api_hash)
        self.trades: List[Dict] = []
        self.results: List[Dict] = []

    async def connect(self):
        """Connect to Telegram"""
        await self.client.start()
        print("✓ Connected to Telegram")

    async def disconnect(self):
        """Disconnect from Telegram"""
        await self.client.disconnect()
        print("✓ Disconnected from Telegram")

    async def fetch_previous_month_messages(self) -> List:
        """Fetch all messages from the previous month"""
        now = datetime.now()
        # Get first day of previous month
        first_day_this_month = now.replace(day=1)
        last_day_prev_month = first_day_this_month - timedelta(days=1)
        first_day_prev_month = last_day_prev_month.replace(day=1)

        print(f"📅 Fetching messages from: {first_day_prev_month.date()} to {last_day_prev_month.date()}")

        messages = []
        async for message in self.client.iter_messages(
            self.channel_id,
            offset_date=last_day_prev_month,
            reverse=True
        ):
            if message.date.date() < first_day_prev_month.date():
                break
            messages.append(message)

        print(f"✓ Fetched {len(messages)} messages")
        return messages

    def parse_trade_signal(self, text: str, message_id: int, timestamp: datetime) -> Optional[Dict]:
        """Parse main trade signal message"""
        match = re.search(TRADE_SIGNAL_PATTERN, text)
        if not match:
            return None

        symbol = match.group(1).replace(" ", "")
        direction = match.group(2).upper()
        price_high = float(match.group(3))
        price_low = float(match.group(4))

        # Extract all TP levels
        tp_matches = re.finditer(TP_PATTERN, text)
        tps = {int(m.group(1)): float(m.group(2)) for m in tp_matches}

        # Extract SL
        sl_match = re.search(SL_PATTERN, text)
        sl = float(sl_match.group(1)) if sl_match else None

        return {
            "message_id": message_id,
            "timestamp": timestamp.isoformat(),
            "symbol": symbol,
            "direction": direction,
            "entry_high": price_high,
            "entry_low": price_low,
            "entry_range": price_high - price_low,
            "take_profits": tps,
            "stop_loss": sl,
            "text": text,
            "results": []
        }

    def parse_result_message(self, text: str, message_id: int, timestamp: datetime) -> Optional[Dict]:
        """Parse result message (TP HIT, SL HIT, etc.)"""
        result = {
            "message_id": message_id,
            "timestamp": timestamp.isoformat(),
            "text": text,
            "tp_hits": [],
            "sl_hit": None
        }

        # Extract TP hits
        tp_hit_matches = re.finditer(TP_HIT_PATTERN, text)
        result["tp_hits"] = [int(m.group(1)) for m in tp_hit_matches]

        # Extract SL hit
        sl_match = re.search(SL_HIT_PATTERN, text)
        if sl_match:
            result["sl_hit"] = float(sl_match.group(1))

        # Only return if there's actual result data
        if result["tp_hits"] or result["sl_hit"]:
            return result

        return None

    def match_trades_to_results(self, messages: List):
        """Match trade signals with their result messages"""
        trade_map = {}  # message_id -> trade

        # First pass: extract all trades
        for msg in messages:
            text = msg.message
            if text:
                trade = self.parse_trade_signal(text, msg.id, msg.date)
                if trade:
                    trade_map[msg.id] = trade
                    self.trades.append(trade)

        # Second pass: match results to trades
        for msg in messages:
            text = msg.message
            if text:
                result = self.parse_result_message(text, msg.id, msg.date)
                if result:
                    # Try to find the most recent trade before this result
                    best_trade_id = None
                    for trade_id in sorted(trade_map.keys()):
                        if trade_id < msg.id:
                            best_trade_id = trade_id

                    if best_trade_id:
                        trade_map[best_trade_id]["results"].append(result)
                    self.results.append(result)

        print(f"✓ Found {len(self.trades)} trades")
        print(f"✓ Found {len(self.results)} result messages")

    def calculate_metrics(self) -> Dict:
        """Calculate performance metrics"""
        if not self.trades:
            return {}

        # Count trades with results
        trades_with_results = [t for t in self.trades if t["results"]]
        trades_with_hits = [t for t in trades_with_results if any(r["tp_hits"] for r in t["results"])]
        trades_with_sl = [t for t in trades_with_results if any(r["sl_hit"] for r in t["results"])]

        # Win rate (TP hit vs SL hit)
        win_rate = len(trades_with_hits) / len(trades_with_results) if trades_with_results else 0

        # TP hit frequency
        total_tp_hits = sum(len(r["tp_hits"]) for t in trades_with_results for r in t["results"])
        total_tps_available = sum(len(t["take_profits"]) for t in trades_with_results)

        tp_hit_rate = total_tp_hits / total_tps_available if total_tps_available > 0 else 0

        # TP level breakdown
        tp_level_hits = defaultdict(int)
        for trade in trades_with_results:
            for result in trade["results"]:
                for tp_level in result["tp_hits"]:
                    tp_level_hits[tp_level] += 1

        # Direction analysis
        buy_trades = [t for t in self.trades if t["direction"] == "BUY"]
        sell_trades = [t for t in self.trades if t["direction"] == "SELL"]

        buy_wins = len([t for t in buy_trades if any(r["tp_hits"] for r in t["results"])])
        sell_wins = len([t for t in sell_trades if any(r["tp_hits"] for r in t["results"])])

        buy_win_rate = buy_wins / len(buy_trades) if buy_trades else 0
        sell_win_rate = sell_wins / len(sell_trades) if sell_trades else 0

        return {
            "total_trades": len(self.trades),
            "trades_with_results": len(trades_with_results),
            "trades_with_tp_hits": len(trades_with_hits),
            "trades_with_sl_hit": len(trades_with_sl),
            "win_rate_percent": round(win_rate * 100, 2),
            "tp_hit_rate_percent": round(tp_hit_rate * 100, 2),
            "buy_trades_count": len(buy_trades),
            "buy_win_rate_percent": round(buy_win_rate * 100, 2),
            "sell_trades_count": len(sell_trades),
            "sell_win_rate_percent": round(sell_win_rate * 100, 2),
            "total_tp_hits": total_tp_hits,
            "total_tps_available": total_tps_available,
            "tp_level_hits": dict(sorted(tp_level_hits.items())),
            "analysis_date": datetime.now().isoformat()
        }

    def save_to_json(self, filename: str = "trade_history.json"):
        """Save trades, results, and metrics to JSON file"""
        metrics = self.calculate_metrics()

        output = {
            "metadata": {
                "channel_id": self.channel_id,
                "analysis_date": datetime.now().isoformat(),
                "period": "previous_month"
            },
            "metrics": metrics,
            "trades": self.trades,
            "results": self.results
        }

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        print(f"✓ Saved data to {filename}")
        return output


async def main():
    """Main execution"""
    analyzer = TradeAnalyzer(api_id, api_hash, CHANNEL)

    try:
        await analyzer.connect()
        messages = await analyzer.fetch_previous_month_messages()
        analyzer.match_trades_to_results(messages)
        output = analyzer.save_to_json()

        # Display summary
        print("\n" + "="*50)
        print("📊 ANALYSIS SUMMARY")
        print("="*50)
        metrics = output["metrics"]
        if metrics:
            print(f"Total Trades: {metrics['total_trades']}")
            print(f"Trades with Results: {metrics['trades_with_results']}")
            print(f"Win Rate: {metrics['win_rate_percent']}%")
            print(f"TP Hit Rate: {metrics['tp_hit_rate_percent']}%")
            print(f"Buy Win Rate: {metrics['buy_win_rate_percent']}% ({metrics['buy_trades_count']} trades)")
            print(f"Sell Win Rate: {metrics['sell_win_rate_percent']}% ({metrics['sell_trades_count']} trades)")
            print()
            print("📍 TP Level Breakdown:")
            if metrics['tp_level_hits']:
                for tp_level in sorted(metrics['tp_level_hits'].keys()):
                    hits = metrics['tp_level_hits'][tp_level]
                    print(f"   TP{tp_level}: {hits} hits")
            else:
                print("   No TP hits recorded")
            print("="*50)

    finally:
        await analyzer.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
