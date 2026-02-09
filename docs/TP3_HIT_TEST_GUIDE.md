# TP3 HIT Safety Exit Test Guide

## Overview
The `generate_tp3_test_signal.py` script simulates the edge case where incomplete signal messages are sent from the trading channel, requiring the bot to safely close all positions when "TP3 HIT" arrives.

## Edge Cases Tested

### Edge Case 1: Incomplete Signal Messages
**Scenario:** Traders send partial messages with only TP hits as they update during the trade.

```
Message 1: TP1 HIT
Message 2: TP2 HIT
Message 3: TP3 HIT  ← Bot must close ALL positions
```

**What the bot should do:**
- ✅ Close ALL open positions immediately
- ✅ Ignore TP/SL settings (for safety)
- ✅ Log the action with "TP3 HIT - SAFETY EXIT"

### Edge Case 2: Fragmented Updates with Prices
**Scenario:** Initial signal lacks full TP details, updates arrive with prices.

```
Message 1: XAU USD BUY NOW (no explicit TPs)
Message 2: TP1 HIT
Message 3: TP2 HIT  
Message 4: TP3 HIT  ← Bot must close ALL positions
```

### Edge Case 3: Isolated TP3 HIT
**Scenario:** TP3 HIT arrives as standalone message with no prior trade signal.

```
Message 1: TP3 HIT  ← Bot should close all open positions

Safety feature: Even if the bot somehow has stale open positions,
TP3 HIT will force closure for safety.
```

---

## Usage

### Preview Mode (No Telegram Send)
See what signals will be sent without actually sending:

```bash
# Realistic scenario preview
python src/generate_tp3_test_signal.py realistic

# Edge case scenario preview  
python src/generate_tp3_test_signal.py edge

# Standalone TP3 HIT preview
python src/generate_tp3_test_signal.py standalone
```

### Send to Telegram Test Channel

```bash
# Send realistic scenario
python src/generate_tp3_test_signal.py realistic --send

# Send edge case scenario
python src/generate_tp3_test_signal.py edge --send

# Send standalone TP3 HIT (for quick testing)
python src/generate_tp3_test_signal.py standalone --send
```

### Customize Delays

```bash
# Wait 10 seconds before sending initial signal
python src/generate_tp3_test_signal.py edge --send --initial-delay 10

# Wait 5 seconds between each TP update
python src/generate_tp3_test_signal.py edge --send --tp-delay 5

# Combine both
python src/generate_tp3_test_signal.py edge --send --initial-delay 5 --tp-delay 3
```

---

## Step-by-Step Testing

### 1. Prepare the Bot
```bash
# Terminal 1: Start the bot
cd src
python bot_v2.py
# Bot should be listening to the test channel
```

### 2. Send Test Signals
```bash
# Terminal 2: Send test signals
cd src
python generate_tp3_test_signal.py edge --send --tp-delay 3
```

### 3. Verify Results

**Check bot logs for:**
```
[✅] Initial signal sent
[🚨] TP3 HIT DETECTED - SAFETY EXIT!
[✅] CLOSED N POSITION(S) on TP3 HIT
```

**Check MT5 platform:**
- All positions should be closed
- Close reason should show "TP3 HIT - SAFETY EXIT"
- No open positions should remain

**Check PnL:**
```bash
python src/pnl_viewer.py
# Should show the closed position with exit reason
```

---

## Test Scenarios

### Scenario A: Realistic Progression
**Mode:** `realistic`
**What happens:**
1. Bot opens BUY position
2. TP1 HIT update arrives → bot may log or track
3. TP2 HIT update arrives → bot may log or track
4. TP3 HIT update arrives → **Bot CLOSES all positions**

**Command:**
```bash
python generate_tp3_test_signal.py realistic --send --tp-delay 3
```

### Scenario B: Edge Case - Minimal Messages
**Mode:** `edge`
**What happens:**
1. Bot receives isolated TP1 HIT (no initial signal)
2. Bot receives isolated TP2 HIT
3. Bot receives isolated TP3 HIT → **Bot finds and CLOSES all open positions**

**Command:**
```bash
python generate_tp3_test_signal.py edge --send --tp-delay 2
```

**Why this is an edge case:**
- No initial BUY/SELL signal before TP hits
- Bot must find open positions and close them
- Tests robustness of TP HIT detection

### Scenario C: Standalone TP3 HIT
**Mode:** `standalone`
**What happens:**
- Only sends "TP3 HIT" message
- Bot should close all open positions
- Pure safety exit test

**Command:**
```bash
python generate_tp3_test_signal.py standalone --send
```

**Why this matters:**
- Even if something goes wrong with signal detection
- TP3 HIT will force closure for account safety
- Last resort protection mechanism

---

## Expected Bot Behavior

### On Receiving TP3 HIT:
```
🚨 TP3 HIT DETECTED - SAFETY EXIT!
🎯 Closing position: Ticket 123456 | Volume 0.100
✅ ORDER SEND (CLOSE): ticket=123456 | side=SELL | price=5004.56
✅ ORDER RESULT (CLOSE): retcode=10009
✅ CLOSED 1 POSITION(S) on TP3 HIT
```

### Safety Guarantees:
✅ Closes ALL positions (not just one)
✅ Works regardless of TP/SL settings
✅ Works even if no trade signal was initially received
✅ Executes at market price (guaranteed fill)
✅ Logs action for audit trail

---

## Troubleshooting

### Bot doesn't close positions on TP3 HIT

**Check:**
1. ✓ Bot is connected to test channel
2. ✓ Test signal script successfully sent message
3. ✓ Bot logs show "TP3 HIT DETECTED"
4. ✓ Positions exist in MT5 account
5. ✓ Not in drawdown/liquidation

**Solution:**
```bash
# Set DEBUG to True in bot.py to see all messages
DEBUG = True

# Restart bot and test again
python src/bot_v2.py
```

### Signal not sending to Telegram

**Check:**
1. ✓ Session file exists in `sessions/` folder
2. ✓ API credentials are correct
3. ✓ Test channel ID is correct (-1003860296364)
4. ✓ Telegram not blocking (check for 2FA)

**Solution:**
```bash
# Delete old session and re-authenticate
rm sessions/zinra_test_session_telethon.session

# Run script again to re-login
python generate_tp3_test_signal.py standalone
```

### Positions not closing

**Check:**
1. ✓ Positions exist in MT5
2. ✓ Account has sufficient balance
3. ✓ Symbol is tradeable (market open)
4. ✓ No pending orders blocking closure

**Solution:**
```bash
# Check current positions
python src/pnl_viewer.py

# Try standalone TP3 HIT
python src/generate_tp3_test_signal.py standalone --send

# Check MT5 logs for rejection reason
```

---

## Verification Checklist

### Before Testing:
- [ ] Bot is running: `python src/bot_v2.py`
- [ ] MT5 connection is active
- [ ] Test channel is accessible
- [ ] Account has tradeable balance

### During Testing:
- [ ] Test signal sends successfully
- [ ] Bot logs show "TP3 HIT DETECTED"
- [ ] MT5 shows position closing
- [ ] No error messages

### After Testing:
- [ ] All positions are closed
- [ ] Close reason includes "TP3 HIT - SAFETY EXIT"
- [ ] PnL is recorded correctly
- [ ] Bot remains operational for next trade

---

## Performance Metrics

| Metric | Expected | Actual |
|--------|----------|--------|
| Time to detect TP3 HIT | <1 second | _____ |
| Time to close position | <3 seconds | _____ |
| Error rate | 0% | _____ |
| Positions closed | 100% | _____ |

---

## Related Documentation

- 📄 [TP3_HIT_SAFETY_EXIT.md](../docs/TP3_HIT_SAFETY_EXIT.md) - Feature implementation details
- 📄 [TEST_MODE_GUIDE.md](../docs/TEST_MODE_GUIDE.md) - General testing guide
- 📄 [bot.py](./bot.py) - Main bot source code (see `on_new()` and `on_edit()` handlers)

---

## Success Criteria

✅ Test passed if:
1. TP3 HIT message is detected by bot
2. All open positions are closed
3. Close order is executed at market price
4. Bot logs show successful closure
5. MT5 platform confirms position closure
6. PnL is recorded in trade history

---

## Notes

- This test requires an active MT5 connection
- Always test on a demo account first!
- Start with `standalone` mode for quick verification
- Review bot logs thoroughly after each test
- Keep this guide for future testing iterations
