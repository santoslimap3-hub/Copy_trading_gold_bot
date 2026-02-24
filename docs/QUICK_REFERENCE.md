# 🚀 Bot Reliability - Quick Reference

## ⚡ What Changed (Quick Summary)

### 1. **Heartbeat System**
- Bot logs status every 60 seconds
- Proves bot is alive and shows metrics
- Look for: `💓 HEARTBEAT #XX - BOT ALIVE`

### 2. **Connection Monitoring**
- Checks MT5 and Telegram health every 30 seconds
- Auto-recovers MT5 if connection drops
- Look for: `🔌 MT5: ✅ | Telegram: ✅`

### 3. **Message Flow Debugging**
- Every message logged with full details
- Shows why messages are ignored
- Shows parsing results
- Look for: `📨 NEW MESSAGE RECEIVED`

### 4. **Auto-Recovering Tasks**
- All monitors restart after crashes
- No more silent failures
- Detailed error tracking

### 5. **Comprehensive Metrics**
- Messages received/processed/ignored
- Trades executed/failed
- Connection losses tracked
- Last activity timestamps

---

## 🔥 Critical Logs to Watch

### ✅ GOOD (Bot Working)
```
[INFO] 💓 HEARTBEAT #XX - BOT ALIVE              ← Bot alive
[INFO] 🔌 MT5: ✅ | Telegram: ✅                  ← Connections OK
[INFO] 🤖 Monitors: Zone=✅ | Breakeven=✅        ← Tasks running
[INFO] 📨 [MAIN] NEW MESSAGE RECEIVED            ← Messages coming in
[INFO] 🎯 ZONE DETECTED: BUY                     ← Signals detected
[INFO] ✅ Position opened successfully           ← Trades executing
```

### ⚠️ WARNING (Potential Issues)
```
[WARN] ⚠️ No Telegram activity for 600s          ← Connection may be dead
[WARN] 🚨 MT5 connection unhealthy               ← Connection issues
[WARN] ⏭️  Message not recognized as signal      ← Parsing failed
[WARN] ⚠️ No open positions to update            ← State mismatch
```

### ❌ ERROR (Action Needed)
```
[ERROR] ❌ CRITICAL: AutoTrading is DISABLED     ← Enable in MT5
[ERROR] ❌ Order FAILED - Retcode: 10027         ← Trading disabled
[ERROR] ❌ CRITICAL EXCEPTION in on_new_message  ← Code error
[ERROR] ❌ MT5 reconnection failed               ← MT5 down
```

---

## 📊 Status Report (Every 5 Minutes)

```
======================================================================
📊 BOT HEALTH STATUS
======================================================================
⏱️  Uptime: 2.50 hours (9000s)                   ← How long bot running
📨 Messages: Received=50 | Processed=15 | Ignored=35   ← Message stats
📈 Trades: Executed=5 | Failed=0                 ← Trade success rate
🔌 Connections: MT5=✅ | Telegram=✅              ← Connection health
🔄 Connection Losses: MT5=0 | Telegram=0         ← Stability indicator
🤖 Monitors: Zone=✅ | Breakeven=✅               ← Task health
💼 Active Positions: 3                           ← Current trades
🧭 Pending Zone: YES                             ← Zone waiting
📬 Last Message: 45s ago                         ← Activity indicator
======================================================================
```

**What to check:**
- ✅ All connections should be ✅
- ✅ Connection Losses should be 0 (or low)
- ✅ Monitors should be ✅
- ✅ Last Message should be recent (during market hours)
- ✅ Messages Processed should increase over time

---

## 🔍 Quick Diagnostic Commands

### Check latest logs:
```powershell
Get-Content bot_output.log -Tail 50
```

### Find errors only:
```powershell
Select-String -Path bot_output.log -Pattern "ERROR|CRITICAL" -Context 2
```

### Check last heartbeat:
```powershell
Select-String -Path bot_output.log -Pattern "HEARTBEAT" | Select-Object -Last 1
```

### Count messages received:
```powershell
Select-String -Path bot_output.log -Pattern "NEW MESSAGE RECEIVED" | Measure-Object
```

### Check connection status:
```powershell
Select-String -Path bot_output.log -Pattern "BOT HEALTH STATUS" -Context 10 | Select-Object -Last 15
```

### Watch live logs:
```powershell
Get-Content bot_output.log -Wait -Tail 30
```

---

## 🎯 Common Issues & Quick Fixes

### Issue: No messages detected
**Check:** Look for `📥 [RAW] chat_id=...` in logs
- **If YES:** Bot receiving but not processing → Check parsing
- **If NO:** Telegram connection dead → Check `Telegram: ✅`

**Quick Fix:**
1. Verify CHANNEL_ID is correct in code
2. Check Telegram connection in status report
3. Look for `TypeNotFoundError` in logs

---

### Issue: Zone detected but not executed
**Check:** Look for `🧭 Pending zone stored` and `🔍 Zone check` logs
- Zone monitoring should show price checks every 2 seconds
- Look for `✅ ZONE TRIGGER` when price enters zone

**Quick Fix:**
1. Verify zone range includes current price
2. Check `🤖 Monitors: Zone=✅` (zone monitor alive)
3. Look for MT5 connection issues

---

### Issue: Bot stopped responding
**Check:** Last heartbeat time
- Look for: `💓 HEARTBEAT #XX`
- If > 2 minutes ago → Bot crashed

**Quick Fix:**
1. Look for last ERROR/CRITICAL message
2. Check if Python process still running
3. Restart bot if needed

---

### Issue: AutoTrading disabled
**Check:** Look for `❌ CRITICAL: AutoTrading is DISABLED`

**Quick Fix:**
1. Open MT5 Terminal
2. Tools → Options → Expert Advisors
3. Enable "Allow algorithmic trading" checkbox
4. Restart bot

---

## 📝 Message Flow Example (What to See)

When channel sends signal, you should see:

```
1. MESSAGE RECEIVED
======================================================================
📨 [MAIN] NEW MESSAGE RECEIVED
   Message ID: 12345
   Preview: BUY ZONE: 2650.00 - 2652.00...
======================================================================

2. PARSING
🔍 Parsing: side=(BUY) | zone_range=(2650.0, 2652.0)

3. SIGNAL DETECTION
🎯 ZONE DETECTED: BUY 2650.00-2652.00
   Targets: [2655.0, 2658.0, 2660.0]
   SL: 2648.0

4. ZONE STORED
🧭 Pending zone stored: BUY 2650.00-2652.00

5. ZONE MONITORING (every 2 seconds)
🔍 Zone check: BUY | Zone: 2650.00-2652.00 | Bid: $2649.50...
🔍 Zone check: BUY | Zone: 2650.00-2652.00 | Bid: $2650.25...

6. ZONE TRIGGERED
✅ ZONE TRIGGER: BUY zone 2650.00-2652.00 - executing trade at $2650.30

7. TRADE EXECUTION
🚀 Opening BUY position with lot size 0.01...
✅ Position opened successfully - Ticket: 12345

8. TRADE SETUP
🛡️ Updating SL for ticket 12345 to $2648.00...
✅ SL updated - Ticket 12345: $2648.00
🎯 Failsafe TP3 price: $2657.30
✅ TP updated - Ticket 12345: $2655.00
```

**If ANY step is missing → That's where the issue is!**

---

## 🆘 Emergency Checklist

If bot completely dead:
- [ ] Check last heartbeat time (should be < 2 min ago)
- [ ] Look for ERROR/CRITICAL messages
- [ ] Check if Python process still running
- [ ] Check MT5 is open and logged in
- [ ] Check internet connection
- [ ] Try restarting bot

---

## 💡 Pro Tips

### Tip 1: Keep logs in a file
```powershell
python bot_zone.py 2>&1 | Tee-Object -FilePath "bot_output.log"
```
Can review history and search for patterns

### Tip 2: Filter for specific events
```powershell
# Only show trades
Select-String -Path bot_output.log -Pattern "Position opened|ZONE TRIGGER"

# Only show errors
Select-String -Path bot_output.log -Pattern "ERROR|CRITICAL" -Context 2

# Only show status reports
Select-String -Path bot_output.log -Pattern "BOT HEALTH STATUS" -Context 12
```

### Tip 3: Monitor in real-time
```powershell
Get-Content bot_output.log -Wait -Tail 30
```
Like watching live logs

### Tip 4: Check bot is responding
If not sure if stuck, just wait 60 seconds for next heartbeat
- If heartbeat appears → Bot alive
- If no heartbeat → Bot crashed

---

## 📧 What to Send When Asking for Help

1. **Last 100 lines of logs:**
   ```powershell
   Get-Content bot_output.log -Tail 100 | Set-Clipboard
   ```

2. **All errors/criticals:**
   ```powershell
   Select-String -Path bot_output.log -Pattern "ERROR|CRITICAL" -Context 2 | Select-Object -Last 20
   ```

3. **Last status report:**
   ```powershell
   Select-String -Path bot_output.log -Pattern "BOT HEALTH STATUS" -Context 12 | Select-Object -Last 15
   ```

4. **Description:**
   - What you expected
   - What actually happened
   - Exact time it happened
   - Message that triggered issue (if applicable)

---

## ✅ Health Check (Run Before Leaving Bot)

Before walking away, verify:
1. ✅ Heartbeat appearing every 60 seconds
2. ✅ Connections show ✅ (MT5 and Telegram)
3. ✅ Monitors show ✅ (Zone and Breakeven)
4. ✅ No ERROR/CRITICAL messages in last 5 minutes
5. ✅ Messages being received (if channel active)
6. ✅ Connection losses = 0

**If ALL green → Bot is healthy!** 🎉

---

## 🔄 Normal Log Pattern (What Success Looks Like)

Every minute you should see:
- 💓 Heartbeat showing bot alive
- 🔍 Zone monitor checking price (if pending zone)
- 🔍 Breakeven monitor checking positions (if trades open)
- 📨 Messages when channel posts (with full details)
- ✅ All status indicators green

Every 5 minutes:
- 📊 Full health status report
- 📋 Signal log summary
- Reconnect monitor summary (if any reconnects)

**This is normal!** Bot is working correctly.

---

## 🎓 Log Symbols Quick Reference

| Symbol | Meaning | Severity |
|--------|---------|----------|
| 💓 | Heartbeat | Info |
| 🔌 | Connection Status | Info |
| 📨 | Message Received | Info |
| 🔍 | Checking/Searching | Debug |
| 🎯 | Signal Detected | Info |
| ✅ | Success/Healthy | Info |
| ⚠️ | Warning | Warn |
| ❌ | Error/Failed | Error |
| 🚨 | Critical Issue | Critical |
| 🔄 | Reconnecting/Retry | Warn |
| 📊 | Statistics/Report | Info |
| 🤖 | Monitor Status | Info |

---

**Remember:** The bot now has extensive self-diagnostics. If something is wrong, it WILL tell you in the logs!
