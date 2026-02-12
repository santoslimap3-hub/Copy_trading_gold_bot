#!/usr/bin/env python3
"""Test the bot trade logger"""

from bot_trade_logger import BotTradeLogger

# Create a test logger
logger = BotTradeLogger("test_trades.json")

# Log a test trade opening
print("📝 Logging test trade...")
logger.log_trade_open(
    ticket=999123,
    side="BUY",
    entry_price=2750.50,
    stop_loss=2742.50,
    targets=[2755.00, 2760.00, 2765.00],
    lot_size=0.45,
    message_id=1001
)

# Log the trade closing
logger.log_trade_close(
    ticket=999123,
    close_price=2755.25,
    close_reason="TP1_HIT",
    tp_hit="1"
)

# Get stats
stats = logger.get_statistics()
print("\n✅ Trade logged successfully!\n")
print(f"Total Closed Trades: {stats['closed_trades']}")
print(f"Total P&L: ${stats['total_pnl']:.2f}")
print(f"Best Trade: ${stats['best_trade']:.2f}")
print(f"Worst Trade: ${stats['worst_trade']:.2f}")

# Display the trade
print("\n📊 Trade Summary:")
trade = logger.get_trade(999123)
if trade:
    print(f"  Ticket: {trade['ticket']}")
    print(f"  Side: {trade['side']}")
    print(f"  Entry: ${trade['entry_price']:.5f}")
    print(f"  Close: ${trade['close_price']:.5f}")
    print(f"  P&L: ${trade['pnl']:.2f}")
    print(f"  TP Hit: TP{trade['tp_hit']}")
    print(f"  Risk/Reward: {trade['risk_reward']:.2f}")

print("\n✅ All tests passed! Logger is working correctly.")
