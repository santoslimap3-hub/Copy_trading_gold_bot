# 🤖 Copy Trading Gold Bot

A sophisticated automated trading bot that monitors Telegram signals and executes trades on MetaTrader 5 with intelligent risk management.

## ✨ Features

- **📡 Real-time Telegram Integration**: Listens to trading signals from your Telegram channel
- **💰 Intelligent Risk Management**: Automatic position sizing based on account equity and risk tolerance
- **🛡️ Safety Kill-Switches**: Hard caps on loss percentage and monetary loss per trade
- **🔄 Auto-Reconnect**: Automatically reconnects if Telegram connection drops
- **📊 Beautiful Terminal UI**: Color-coded output with tables and panels for easy monitoring
- **⚡ Retry Logic**: Intelligent retry mechanism for SL/TP modifications with broker rejections
- **🔍 Position Tracking**: Maps Telegram message IDs to MT5 position tickets
- **📈 Real-time Market Snapshots**: Displays bid/ask, market conditions, and position status

## 📋 Requirements

- Python 3.8+
- MetaTrader 5 terminal with AutoTrading enabled
- Active Telegram channel for signal reception
- Valid trading account with the MT5 broker

## 🚀 Installation

1. **Clone or download this repository**

2. **Install dependencies**:
```bash
pip install -r requirements.txt
```

3. **Configure your credentials** in `script.py`:
```python
api_id = YOUR_TELEGRAM_API_ID
api_hash = "YOUR_TELEGRAM_API_HASH"
CHANNEL = YOUR_CHANNEL_ID  # Get this using find_channel.py
```

4. **Find your Telegram channel ID**:
```bash
python find_channel.py
```

## ⚙️ Configuration

Edit `script.py` to customize:

### Risk Management
```python
RISK_PCT = 0.10                 # Risk 10% of account per trade
ASSUMED_SL_PRICE_DIST = 8.0     # Assume SL is $8 away (for sizing)
MAX_LOT = 1.0                   # Hard cap on lot size
HARD_MAX_LOSS_MONEY = 25.0      # Never risk more than $25 per trade
HARD_MAX_LOSS_PCT = 0.15        # Never risk more than 15% of account
```

### Trading Parameters
```python
SYMBOL = "XAUUSD"              # Symbol to trade
MAGIC = 777                     # Magic number for order identification
DEVIATION = 20                  # Slippage tolerance
```

### Logging & Debug
```python
DEBUG = True                    # Enable debug output
DEBUG_SHOW_MARKET_SNAPSHOT = True
DEBUG_SHOW_RISK_MATH = True
DEBUG_SHOW_POSITION_STATE = True
```

## 🎯 How It Works

### Signal Format
The bot listens for messages in this format:

```
BUY NOW @ 2050.00
TP 1 2055.50
SL 2045.00
```

### Entry Execution
1. Bot detects "BUY NOW" or "SELL NOW" in the message
2. Calculates position size based on:
   - Account equity/balance
   - Risk percentage (default 10%)
   - Assumed stop-loss distance
   - Broker margin requirements
3. Executes market order and tracks position ticket
4. Waits for TP1 and SL in edited message

### Stop Loss & Take Profit Update
1. Bot detects message edit with TP1 and SL prices
2. Validates prices against current market (sanity check)
3. Clamps stops to broker minimum distance
4. Retries modification if broker rejects (up to 45 seconds)
5. Confirms successful update with terminal display

## 📊 Terminal Output

The bot displays rich, color-coded output:

- 🎯 **Green**: Successful operations
- 🔴 **Red**: Errors and failures  
- 🟡 **Yellow**: Warnings and retries
- 🔵 **Cyan**: Information and debug logs
- 📊 **Tables**: Account info, positions, risk calculations

## 🛑 Safety Features

- **Equity Check**: Uses current equity (not just balance) for risk sizing
- **Symbol Mismatch Detection**: Refuses to set SL if too far from market
- **Min Lot Check**: Skips trade if minimum lot exceeds hard caps
- **Margin Verification**: Ensures sufficient margin before trading
- **Multi-Position Warning**: Alerts if stacking multiple positions
- **Duplicate Prevention**: Ignores duplicate signal edits

## 🔧 Troubleshooting

### "Trading is NOT allowed in MT5"
✅ Enable AutoTrading in MetaTrader 5:
   - Click "Tools" → "Options"
   - Enable "Allow automated trading"

### "Symbol not available after retries"
✅ Ensure SYMBOL is correct and visible in MT5:
   - Check symbol name matches exactly
   - Symbol must be visible in Market Watch

### "No tick data for symbol"
✅ Market is closed or symbol not tradeable:
   - Wait for market open
   - Check trading hours for the symbol

### Bot won't connect to Telegram
✅ Verify credentials in script.py:
   - Check API ID and API Hash
   - Ensure session file isn't corrupted (delete `zinra_session.session`)

## 📝 Example Telegram Message

```
🔱 Gold Trading Signal 🔱

BUY NOW @ 2050.25

Target: TP 1 2055.75
Stop: SL 2045.00

Risk:Reward = 1:2.2
```

## ⚠️ Disclaimer

**This bot is for educational purposes.** Use at your own risk. 

- Test on demo account first
- Verify all settings before live trading
- Monitor bot activity closely
- Maintain adequate account capital
- Trading involves risk of loss

## 📄 License

Private - Use only as authorized

## 💬 Support

For issues or questions, review the debug output in the terminal. Enable all DEBUG flags for detailed logging.

---

**Made with ❤️ for automated trading**
