# 🤖 Copy Trading Gold Bot

A high-performance trading bot that monitors Telegram signals and executes automated trades on MetaTrader 5 with intelligent risk management.

## ✨ Core Components

### 🎯 Trading Bots
- **`bot_zone.py`** - Zone-based signal trading bot (zone ranges from Telegram)
- **`bot_v2.py`** - Signal-based trading bot (exact entry/TP/SL from Telegram)
- **`my_bot.py`** - Minimal trading bot example
- **`mt5_account_analysis.py`** - MT5 account and position analysis utility

### 📊 Trade Tracking & Analytics
- **`bot_trade_logger.py`** - Core trade logging and P&L calculation engine
- **`bot_trade_analyzer.py`** - CLI dashboard for viewing trade statistics
- **`bot_trade_dashboard.py`** - Web dashboard with interactive charts

### 🔧 Infrastructure
- **`signal_queue.py`** - Persistent signal queue for reliable signal processing
- **`session_manager.py`** - Telegram session management and security
- **`reconnect_monitor.py`** - Auto-reconnect and connection monitoring

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run a Trading Bot
```bash
# Zone-based bot
python src/bot_zone.py

# Signal-based bot
python src/bot_v2.py
```

### 3. Monitor Trades
```bash
# CLI analytics
python src/bot_trade_analyzer.py

# Web dashboard
python src/bot_trade_dashboard.py
# Opens at http://localhost:8764
```

### 4. Analyze Account
```bash
python src/mt5_account_analysis.py
```

## ⚙️ Configuration

Edit the bot file before running:
```python
# Telegram
API_ID = "YOUR_API_ID"
API_HASH = "YOUR_API_HASH"
CHANNEL_ID = "YOUR_CHANNEL_ID"

# Trading
SYMBOL = "XAUUSD"
RISK_PCT = 0.05  # 5% risk per trade
MAGIC = 777      # Order magic number
```

## 📊 Trade Tracking

The bot automatically logs all trades with:
- Entry price, stop loss, take profit levels
- Lot size and risk/reward ratio
- Close price and reason (TP hit, SL hit, manual close)
- Calculated P&L based on actual lot size

**Key Formula**: `P&L = (price_difference × lot_size × 100)`

View statistics anytime:
```bash
python src/bot_trade_analyzer.py
```

## 🛑 Safety Features

- ✅ Automatic position sizing based on risk percentage
- ✅ Hard caps on maximum loss per trade
- ✅ Symbol and price validation
- ✅ Margin verification before trading
- ✅ Auto-reconnect on connection loss
- ✅ Signal persistence (survives crashes)

## ⚠️ Disclaimer

**Use at your own risk.** Test on a demo account first. 

- Monitor bot activity closely
- Maintain adequate account capital
- Enable AutoTrading in MT5 before running bot
- Trading involves risk of significant loss

## 📝 Files Overview

| File | Purpose |
|------|---------|
| `bot_zone.py` | Main trading bot for zone-based signals |
| `bot_v2.py` | Alternative bot for exact entry signals |
| `my_bot.py` | Minimal example bot |
| `bot_trade_logger.py` | Trade logging engine |
| `bot_trade_analyzer.py` | CLI analytics |
| `bot_trade_dashboard.py` | Web analytics with charts |
| `mt5_account_analysis.py` | Account analysis utility |
| `signal_queue.py` | Persistent signal storage |
| `session_manager.py` | Telegram session manager |
| `reconnect_monitor.py` | Connection monitoring |

---

**Made for automated trading 📈**
