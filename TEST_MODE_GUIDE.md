# 🧪 Test Mode Guide

## How to Use Test Mode

Test Mode allows you to safely test the trading bot with a test Telegram channel and a demo MetaTrader account.

---

## ✅ Quick Setup

### Step 1: Find Your Test Channel ID

```bash
python find_channel.py
```

Look for the channel named **"testing the bot do not touch"** and copy its ID.

### Step 2: Configure Test Channel

Edit `script.py` and find this line:

```python
TEST_CHANNEL = None  # TODO: Set to your test channel ID (use find_channel.py to find it)
```

Replace `None` with your test channel ID:

```python
TEST_CHANNEL = -1003349563414  # Your test channel ID
```

### Step 3: Switch to Demo Account in MT5

1. Open **MetaTrader 5**
2. Go to **File → Open an Account**
3. Select or create a **DEMO account**
4. Close and restart MT5

### Step 4: Run in Test Mode

```bash
python script.py --test-mode
```

Or use the short form:

```bash
python script.py -t
```

---

## 📊 What Happens in Test Mode

When you run the bot in test mode:

✅ **Telegram Channel**
- Bot listens to your **TEST channel** instead of live channel
- No signals from live channel are processed
- Safe to test without affecting real trades

✅ **Demo Account**
- Bot verifies you're on a **DEMO account**
- If live account detected, shows warning
- Prevents accidental live trading

✅ **Visual Indicator**
- Yellow banner: **🧪 TEST MODE ACTIVATED**
- Clear indication in the terminal
- Shows both test and live channel IDs

---

## 🚀 Running the Bot

### Live Mode (Default)
```bash
python script.py
```

Output shows: **🚀 LIVE MODE**

### Test Mode
```bash
python script.py --test-mode
```

Output shows: **🧪 TEST MODE ACTIVATED**

---

## ⚠️ Safety Features

### Test Mode Checks

1. **Channel Verification**
   - Confirms TEST_CHANNEL is configured
   - Exits with error if not set

2. **Account Detection**
   - Checks if account is demo or live
   - Warns if live account detected
   - 5-second pause to review warning

3. **Clear Labeling**
   - Displays channel IDs for reference
   - Shows mode status at startup
   - Continuous visual indicator

---

## 🔄 Switching Between Modes

### From Live to Test Mode

```bash
# Currently running live (Ctrl+C to stop)
python script.py

# Stop with Ctrl+C, then run:
python script.py --test-mode
```

### From Test to Live Mode

```bash
# Currently running test (Ctrl+C to stop)
python script.py --test-mode

# Stop with Ctrl+C, then switch MT5 back to live account

# Run:
python script.py
```

---

## 💡 Test Channel Setup

Your test channel should be configured with:
- Real or test signals in the same format
- Your own test account as admin
- Optional: other test users

This allows you to:
- ✅ Test signal parsing
- ✅ Test order placement
- ✅ Test position management
- ✅ Test error handling
- ✅ Verify bot behavior safely

---

## 🐛 Troubleshooting

### Error: "TEST_CHANNEL not configured!"

**Solution:**
```bash
# Step 1: Find your test channel
python find_channel.py

# Step 2: Copy the test channel ID

# Step 3: Edit script.py and update:
TEST_CHANNEL = YOUR_CHANNEL_ID

# Step 4: Run again
python script.py --test-mode
```

### Error: "Not logged in?"

**Solution:** Check that MetaTrader 5 is running and connected

### Warning: "Live account detected in test mode"

**Solution:** Switch to demo account in MT5:
1. File → Open an Account
2. Select DEMO account
3. Restart the bot

### No signals being received

**Solution:** Send test signals to your test channel using the format:
```
BUY NOW @ 2050.00
TP 1 2055.50
SL 2045.00
```

---

## 📋 Test Mode Checklist

Before running test mode:

- [ ] Run `find_channel.py`
- [ ] Found test channel ID
- [ ] Updated `TEST_CHANNEL` in script.py
- [ ] Switched MT5 to demo account
- [ ] Enabled AutoTrading in MT5
- [ ] Ready to run `python script.py --test-mode`

---

## 🎯 Example Test Session

```bash
# Step 1: Find channel
$ python find_channel.py
# ... see your channels ...
# Copy: -1003349563414

# Step 2: Edit script.py
# TEST_CHANNEL = -1003349563414

# Step 3: Switch MT5 to demo account
# (done in MT5 GUI)

# Step 4: Run in test mode
$ python script.py --test-mode

# Output:
# ═══════════════════════════════════════════
# 🧪 TEST MODE ENABLED
# ═══════════════════════════════════════════
# 
# This bot will:
#   • Connect to the TEST Telegram channel
#   • Require a DEMO MetaTrader 5 account
#   • NOT execute trades on your live account

# Step 5: Send test signal to test channel
# (in Telegram)

# Step 6: Watch bot execute on demo account
# ✅ Order received and executed!
```

---

## 🔐 Safety Reminder

**Test Mode is SAFE because:**
- ✅ Uses separate test channel
- ✅ Requires demo account
- ✅ No live account access
- ✅ Clear visual warnings
- ✅ Asks for confirmation

**Live Mode is SAFE because:**
- ✅ Requires live account intentionally
- ✅ No test channel interference
- ✅ All safety checks active
- ✅ Hard loss caps enforced

---

## 📞 Need Help?

Common questions:

**Q: Can I run test and live simultaneously?**
A: No, one bot instance per account. Switch modes between runs.

**Q: Will test signals affect my live trades?**
A: No, test channel is separate from live channel.

**Q: Can I test with real money?**
A: Not recommended. Use demo account for testing.

**Q: How do I know which mode is active?**
A: Check the banner at startup:
- **🚀 LIVE MODE** = Live channel, live account
- **🧪 TEST MODE** = Test channel, demo account

---

**Happy testing!** 🧪✨

