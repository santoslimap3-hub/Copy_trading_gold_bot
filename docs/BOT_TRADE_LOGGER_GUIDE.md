# Bot Trade PnL Tracking System

## Overview

A comprehensive system has been added to track **all trades placed by the bot** with detailed P&L calculations, statistics, and analysis tools.

## Components

### 1. **bot_trade_logger.py** - Core Logging Engine
Main module for tracking individual trades throughout their lifecycle.

**Key Features:**
- Logs trade opening with entry price, targets, stop loss, lot size
- Logs trade closing with exit price and close reason
- Automatic P&L calculation (in USD)
- Risk/Reward ratio calculation
- Persistent JSON storage with atomic writes
- Summary statistics computation

**Usage in bot_zone.py:**
```python
# When a trade opens
trade_logger.log_trade_open(
    ticket=ticket_number,
    side="BUY" or "SELL",
    entry_price=price,
    stop_loss=sl_price,
    targets=[tp1, tp2, tp3, ...],
    lot_size=lot,
    message_id=msg_id
)

# When a trade closes
trade_logger.log_trade_close(
    ticket=ticket_number,
    close_price=price,
    close_reason="TP1_HIT" | "SL_HIT" | "SAFETY_EXIT" | "TP3_HIT" etc,
    tp_hit="1" | "2" | "3" etc  # Which TP was hit
)
```

### 2. **bot_trades.json** - Data Storage
Persistent file storing all trade data and statistics.

#### File Structure:
```json
{
  "metadata": {
    "created_at": "2026-02-12T...",
    "bot_version": "v2",
    "symbol": "XAUUSD"
  },
  "summary": {
    "total_trades": 45,
    "total_pnl": 1250.50,
    "total_wins": 32,
    "total_losses": 13,
    "win_rate_percent": 71.11,
    "avg_win": 85.42,
    "avg_loss": -92.31,
    "max_drawdown": 250.00,
    "largest_win": 450.00,
    "largest_loss": -380.50,
    "pending_trades": 2,
    "last_updated": "2026-02-12T..."
  },
  "trades": [
    {
      "ticket": 123456,
      "status": "CLOSED",
      "side": "BUY",
      "entry_price": 2750.12345,
      "stop_loss": 2742.12345,
      "take_profits": {
        "1": 2755.00,
        "2": 2760.00,
        "3": 2765.00
      },
      "lot_size": 0.45,
      "opened_at": "2026-02-12T10:30:00+00:00",
      "closed_at": "2026-02-12T10:45:00+00:00",
      "close_price": 2755.50,
      "close_reason": "TP1_HIT",
      "tp_hit": "1",
      "pnl": 125.50,
      "risk_reward": 1.85,
      "message_id": 2755
    }
  ]
}
```

#### Trade Status
- **OPEN** - Currently active, not closed
- **CLOSED** - Trade has been closed with final P&L

#### Close Reasons
- `TP1_HIT`, `TP2_HIT`, `TP3_HIT` - Take profit level hit
- `SL_HIT` - Stop loss triggered
- `SAFETY_EXIT` - TP3 hit safety exit (closes all open positions)
- `MANUAL` - Manually closed
- `BREAKEVEN` - Closed at breakeven

### 3. **bot_trade_analyzer.py** - CLI Analytics Tool
Terminal-based dashboard for viewing trade statistics and P&L.

**Usage:**
```bash
python bot_trade_analyzer.py                      # Uses default bot_trades.json
python bot_trade_analyzer.py custom_trades.json   # Uses custom file
```

**Output Shows:**
- Overall trading summary (total trades, wins, losses, win rate)
- P&L metrics (total P&L, avg win/loss, largest win/loss, max drawdown)
- Open trades list with details
- Recent closed trades with P&L
- Trade performance by side (BUY vs SELL)
- Detailed statistics (P&L distribution, close reasons, TP hits)

**Example Output:**
```
================================================================================
  BOT TRADING SUMMARY
================================================================================

Total Trades (Closed):    45
Pending Trades:           2
Total Wins:               32
Total Losses:             13
Win Rate:                 71.11%

Total P&L:                $1,250.50
Average Win:              $85.42
Average Loss:             -$92.31
Largest Win:              $450.00
Largest Loss:             -$380.50
Max Drawdown:             -$250.00
```

### 4. **bot_trade_dashboard.py** - Web Dashboard
Interactive web-based dashboard with charts and real-time statistics.

**Features:**
- Real-time P&L charts
- Win/Loss distribution pie chart
- TP hit distribution bar chart
- Close reason distribution
- Recent trades table (last 20 closed trades)
- Open trades list
- Responsive design with color-coded metrics

**Usage:**
```bash
python bot_trade_dashboard.py                      # Default: serves on http://localhost:8764
python bot_trade_dashboard.py --port 9000         # Custom port
python bot_trade_dashboard.py --open              # Auto-open browser
python bot_trade_dashboard.py --file custom.json  # Custom data file
```

**Example Commands:**
```bash
# Start dashboard and auto-open in browser
python bot_trade_dashboard.py --open

# Serve on custom port (useful if 8764 is in use)
python bot_trade_dashboard.py --port 8765

# View different trade file
python bot_trade_dashboard.py --file bot_trades_backup.json
```

## Integration with bot_zone.py

The trade logger is automatically initialized when bot_zone.py starts:

```python
# In main() function
trade_logger = BotTradeLogger("bot_trades.json")
```

**Automatic Trade Logging Events:**

1. **Trade Opens** - When `open_position()` succeeds:
   - Logs ticket, entry price, targets, stop loss, lot size
   
2. **Trade Closes** - When `close_position()` executes:
   - Logs close price, close reason, TP hit
   - Calculates P&L automatically
   - Updates summary statistics

3. **TP3 Hit Safety Exit** - When TP3 hit message detected:
   - Closes all open positions
   - Logs with reason "SAFETY_EXIT" and tp_hit="3"

## P&L Calculation

**Formula for Gold (XAUUSD):**
```
P&L = (Close_Price - Entry_Price) * Lot_Size * 100

For SELL trades:
P&L = (Entry_Price - Close_Price) * Lot_Size * 100
```

**Why multiply by 100?**
- Gold contract size: 1 lot = 100 troy ounces
- Each point = 1 USD per lot
- Example: Entry 2750.00, Close 2755.00, Lot 0.45
  - Price diff: 5.00 points
  - P&L = 5.00 * 0.45 * 100 = $225 profit

## Statistics Computed

### Summary Stats
- **Total P&L**: Sum of all closed trade P&L
- **Win Rate**: (Winning Trades / Total Closed Trades) * 100
- **Average Win/Loss**: Mean P&L of winning/losing trades
- **Max Drawdown**: Largest cumulative loss from peak
- **Largest Win/Loss**: Best and worst single trade

### Distribution Metrics
- **Close Reasons**: Count of each close reason (TP1, TP2, TP3, SL, etc)
- **TP Hits**: Which take profit levels are hit most often
- **Side Performance**: Separate stats for BUY and SELL trades

## Viewing Your Trade Data

### Quick View (CLI)
```bash
cd src/
python bot_trade_analyzer.py
```

### Interactive Dashboard (Web)
```bash
cd src/
python bot_trade_dashboard.py --open
# Opens http://localhost:8764 in your browser
```

### Raw Data
Look at `src/bot_trades.json` directly (JSON format)

## File Locations

- **Trade Logger Source**: `src/bot_trade_logger.py`
- **Trade Data**: `src/bot_trades.json` (created on first bot run)
- **CLI Analyzer**: `src/bot_trade_analyzer.py`
- **Web Dashboard**: `src/bot_trade_dashboard.py`
- **Bot Integration**: `src/bot_zone.py` (lines where logger is called)

## Key Metrics to Monitor

1. **Total P&L** - Overall bot profitability
2. **Win Rate** - Consistency of strategy (aim for 60%+)
3. **Risk/Reward** - Each trade's risk vs potential reward
4. **Max Drawdown** - Largest losing streak
5. **TP Hit Distribution** - Which take profits are being hit most
6. **Close Reasons** - Understanding how trades close

## Examples

### Analyzing Performance by Side
```bash
python bot_trade_analyzer.py
# Look at "STATISTICS BY SIDE" section
# Example:
#   BUY Trades: 24 | Win Rate: 75% | Total P&L: $950
#   SELL Trades: 21 | Win Rate: 67% | Total P&L: $300
```

### Checking Recent Performance
```bash
python bot_trade_analyzer.py
# Look at "RECENT CLOSED TRADES" section
# Shows last 15-20 trades with P&L and close reason
```

### Real-Time Monitoring
```bash
python bot_trade_dashboard.py --open
# Refresh browser (F5) to see latest data
# Charts update automatically when bot trades
```

## Troubleshooting

### No data showing?
- Check that bot has actually placed trades (check bot_zone.py logs)
- Verify `src/bot_trades.json` exists
- Ensure `trade_logger` is initialized in `main()`

### Dashboard not loading?
- Make sure port 8764 is available (use `--port 9000` to change)
- Check firewall settings if accessing from another machine
- Try accessing `http://127.0.0.1:8764` directly

### P&L calculations wrong?
- Verify lot size is correct (shown in trade entry)
- Check entry and close prices match MT5
- Remember: 1 lot = 100 oz, multiply by 100 in formula

## Future Enhancements

Potential additions:
- [ ] Database storage (SQLite/PostgreSQL)
- [ ] Email alerts for large losses
- [ ] Advanced filtering (by date range, symbol, etc)
- [ ] Trade journal with notes
- [ ] Performance comparison to channel signals
- [ ] Automated strategy backtesting
- [ ] Monthly/weekly performance reports

