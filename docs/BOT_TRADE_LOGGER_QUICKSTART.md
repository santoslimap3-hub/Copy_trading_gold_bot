# Quick Start: Bot Trade Logger

## What's New?

Your bot now has a **complete trade tracking and P&L analysis system**!

Every trade placed by the bot is automatically logged with:
- Entry/exit prices
- P&L (profit/loss) calculation
- Take profit levels and which ones are hit
- Trade performance statistics
- Win rate, average wins/losses, drawdown analysis

## How to Use

### 1. Run Your Bot (No Changes Needed!)
The logging happens **automatically** when bot_zone.py runs:

```bash
python my_bot.py  # or however you normally start it
```

The bot will automatically create `src/bot_trades.json` to store all trade data.

### 2. View Your Performance (3 Options)

#### Option A: CLI Dashboard (Terminal)
```bash
cd src/
python bot_trade_analyzer.py
```

Shows:
```
Total Trades:        45
Total P&L:           $1,250.50  
Win Rate:            71.11%
Largest Win:         $450.00
Largest Loss:        -$380.50
```

#### Option B: Interactive Web Dashboard
```bash
cd src/
python bot_trade_dashboard.py --open
```

Opens http://localhost:8764 with:
- 📈 Cumulative P&L chart
- 📊 Win/Loss distribution
- 🎯 Take profit hit distribution
- 📋 Recent trades table
- 🟡 Open trades list

#### Option C: Raw Data
Look directly at `src/bot_trades.json` (human-readable JSON)

### 3. Interpret the Metrics

| Metric | What It Means | Target |
|--------|---------------|--------|
| **Total P&L** | Overall profit/loss | Positive! 📈 |
| **Win Rate** | % of winning trades | 60%+ is good |
| **Avg Win/Loss** | Average size of wins/losses | Win > Loss |
| **Max Drawdown** | Largest losing streak | Smaller is better |
| **Risk/Reward** | Win size vs loss size | 1.5+ is good |

## Example Scenarios

### Scenario 1: Just Started
```
Total Trades: 5
Total P&L: $150.25
Win Rate: 60%
Status: ✅ Good start! Monitor a few more trades.
```

### Scenario 2: Running Well
```
Total Trades: 50
Total P&L: $2,500.00
Win Rate: 74%
Max Drawdown: -$300
Status: ✅ Strong performance! Consider scaling up.
```

### Scenario 3: Needs Improvement
```
Total Trades: 30
Total P&L: -$450.00
Win Rate: 40%
Max Drawdown: -$1,200
Status: ⚠️ Strategy needs adjustment.
```

## Where's My Data?

### Main Files
- **Trade Data**: `src/bot_trades.json` (stores all trades)
- **Analyzer**: `src/bot_trade_analyzer.py` (CLI viewer)
- **Dashboard**: `src/bot_trade_dashboard.py` (web viewer)
- **Logger**: `src/bot_trade_logger.py` (core library - auto-used)

### Backup Your Data
```bash
# Keep a backup of trades
cp src/bot_trades.json src/bot_trades_$(date +%Y%m%d).json
```

## Understanding Each Trade Entry

Each closed trade shows:

```json
{
  "ticket": 123456,           # MT5 ticket number
  "side": "BUY",              # Trade direction
  "entry_price": 2750.00,     # Where you entered
  "close_price": 2755.50,     # Where you exited
  "pnl": 225.00,              # Profit/loss in USD
  "tp_hit": "1",              # Which take profit hit (1=TP1, 2=TP2, etc)
  "close_reason": "TP1_HIT",  # Why it closed
  "lot_size": 0.45,           # Volume traded
  "risk_reward": 1.85         # Risk vs reward ratio
}
```

## Advanced: Filtering Data

### View only SELL trades performance
```python
# Open bot_trade_analyzer.py and modify the stats section
# or use Python to filter:
import json
data = json.load(open('bot_trades.json'))
sell_trades = [t for t in data['trades'] if t['side'] == 'SELL']
sell_pnl = sum(t.get('pnl', 0) for t in sell_trades if t.get('status') == 'CLOSED')
print(f"SELL Win Rate: {len([t for t in sell_trades if t.get('pnl', 0) > 0]) / len(sell_trades) * 100}%")
```

### Find your best trades
```python
import json
from operator import itemgetter
data = json.load(open('bot_trades.json'))
closed = [t for t in data['trades'] if t.get('status') == 'CLOSED']
best_trades = sorted(closed, key=itemgetter('pnl'), reverse=True)[:5]
for t in best_trades:
    print(f"Ticket {t['ticket']}: +${t['pnl']:.2f}")
```

## Troubleshooting

### Q: Why doesn't my first trade show?
**A:** The bot needs to complete a full trade cycle (open + close). Once a trade closes, it appears in the logs.

### Q: Bot keeps crashing?
**A:** Make sure `trade_logger` is not conflicting. Comment out these lines if needed:
```python
# if trade_logger:
#     trade_logger.log_trade_close(...)
```

### Q: Dashboard shows "No data"?
**A:** The bot hasn't placed trades yet, or the file path is wrong. Check:
1. Bot is actually trading (check logs)
2. File exists at `src/bot_trades.json`
3. Permissions are correct

### Q: How do I reset/clear all trades?
**A:** Delete the file - a new one will be created:
```bash
rm src/bot_trades.json
# or on Windows:
del src\bot_trades.json
```

## Next Steps

1. **Run your bot normally** - it logs automatically
2. **Check CLI analyzer** `python bot_trade_analyzer.py` after a few trades
3. **Open web dashboard** `python bot_trade_dashboard.py --open` for detailed view
4. **Monitor metrics** over time to understand system performance
5. **Keep backups** of `bot_trades.json` in case of data loss

---

**Questions?** Check the full guide: [BOT_TRADE_LOGGER_GUIDE.md](BOT_TRADE_LOGGER_GUIDE.md)
