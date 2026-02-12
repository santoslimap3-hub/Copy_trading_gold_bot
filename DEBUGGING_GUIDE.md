# 🔧 Bot Debugging & Reliability Guide

## Overview
This guide explains all the debugging features added to make the bot bulletproof and help diagnose issues quickly.

---

## 🎯 Key Improvements Made

### 1. **Heartbeat Monitoring** ⚡
- Bot prints status **every 60 seconds** to prove it's alive
- Shows uptime, messages received, trades executed
- Shows connection status for MT5 and Telegram

**What to look for:**
```
[2025-02-12 10:30:00] [INFO] ======================================================================
[2025-02-12 10:30:00] [INFO] 💓 HEARTBEAT #15 - BOT ALIVE
[2025-02-12 10:30:00] [INFO] ⏱️  Uptime: 0.25h | Msgs: 3 | Trades: 1
[2025-02-12 10:30:00] [INFO] 🔌 MT5: ✅ | Telegram: ✅
[2025-02-12 10:30:00] [INFO] ======================================================================
```

**If heartbeat stops:** Bot has crashed - check for error messages above the last heartbeat

---

### 2. **Connection Health Monitoring** 🏥
- Actively checks MT5 connection **every 30 seconds**
- Monitors Telegram connection activity
- **Auto-recovers** MT5 connection if it dies
- Warns if Telegram hasn't had activity for 10+ minutes

**What to look for:**
```
[2025-02-12 10:30:30] [DEBUG] 🔍 Running connection health checks...
[2025-02-12 10:30:30] [DEBUG] ✅ MT5 connection healthy
```

**If connection fails:**
```
[2025-02-12 10:30:30] [WARN] 🚨 MT5 connection unhealthy - attempting recovery
[2025-02-12 10:30:30] [WARN] 🔄 Attempting MT5 reconnection...
[2025-02-12 10:30:32] [INFO] ✅ MT5 reconnection successful
```

---

### 3. **Message Flow Debugging** 📨
Every message received is now logged in detail:

**Message Reception:**
```
[2025-02-12 10:35:00] [INFO] ======================================================================
[2025-02-12 10:35:00] [INFO] 📨 [MAIN] NEW MESSAGE RECEIVED
[2025-02-12 10:35:00] [INFO]    Message ID: 12345
[2025-02-12 10:35:00] [INFO]    Chat ID: -1003142865169
[2025-02-12 10:35:00] [INFO]    Length: 150 chars
[2025-02-12 10:35:00] [INFO]    Preview: BUY ZONE: 2650.00 - 2652.00 TARGETS: 2655 2658 2660 SL: 2648
[2025-02-12 10:35:00] [INFO] ======================================================================
```

**Parsing Results:**
```
[2025-02-12 10:35:00] [DEBUG] 🔍 Parsing: side=(BUY) | zone_range=(2650.0, 2652.0)
[2025-02-12 10:35:00] [INFO] 🎯 ZONE DETECTED: BUY 2650.00-2652.00
[2025-02-12 10:35:00] [DEBUG]    Targets: [2655.0, 2658.0, 2660.0]
[2025-02-12 10:35:00] [DEBUG]    SL: 2648.0
```

**If message ignored:**
```
[2025-02-12 10:35:00] [DEBUG] ⏭️  Message not recognized as signal - ignoring
[2025-02-12 10:35:00] [DEBUG]    Checks: TP3_HIT=False | CANCEL=False | SIDE=None | ACTIVE=False
```

---

### 4. **Comprehensive Metrics Tracking** 📊
Bot now tracks everything:
- Messages received, processed, ignored
- Trades executed, failed
- Connection losses (MT5 & Telegram)
- Monitor health status
- Active positions
- Pending zones

**Status Report (every 5 minutes):**
```
[2025-02-12 10:40:00] [INFO] ======================================================================
[2025-02-12 10:40:00] [INFO] 📊 BOT HEALTH STATUS
[2025-02-12 10:40:00] [INFO] ======================================================================
[2025-02-12 10:40:00] [INFO] ⏱️  Uptime: 1.50 hours (5400s)
[2025-02-12 10:40:00] [INFO] 📨 Messages: Received=25 | Processed=8 | Ignored=17
[2025-02-12 10:40:00] [INFO] 📈 Trades: Executed=3 | Failed=0
[2025-02-12 10:40:00] [INFO] 🔌 Connections: MT5=✅ | Telegram=✅
[2025-02-12 10:40:00] [INFO] 🔄 Connection Losses: MT5=0 | Telegram=0
[2025-02-12 10:40:00] [INFO] 🤖 Monitors: Zone=✅ | Breakeven=✅
[2025-02-12 10:40:00] [INFO] 💼 Active Positions: 2
[2025-02-12 10:40:00] [INFO] 🧭 Pending Zone: YES
[2025-02-12 10:40:00] [INFO] 📬 Last Message: 120s ago
[2025-02-12 10:40:00] [INFO] ======================================================================
```

---

### 5. **Auto-Recovering Background Tasks** 🔄
All background monitors now:
- **Auto-restart on exceptions** (instead of dying silently)
- Log detailed error traces
- Mark themselves as alive/dead in metrics
- Wait 10 seconds before retrying after errors

**Normal operation:**
```
[2025-02-12 10:30:00] [DEBUG] 🔍 Breakeven monitor check #150: Checking 2 position(s)
[2025-02-12 10:30:00] [DEBUG] 🧭 Zone monitor check #90: Active zone (age: 45s)
```

**If task crashes:**
```
[2025-02-12 10:30:05] [ERROR] ❌ CRITICAL: Zone monitor exception: division by zero
[2025-02-12 10:30:05] [ERROR]    Traceback (most recent call last):
[2025-02-12 10:30:05] [ERROR]    File "bot_zone.py", line 950...
```
*(Task will automatically restart after 10 seconds)*

---

### 6. **Enhanced Error Handling** 🛡️
All exceptions now include:
- Full stack traces
- Context about what was being processed
- Message ID and text (when available)
- Clear error classification (WARN vs ERROR vs CRITICAL)

**Example:**
```
[2025-02-12 10:35:10] [ERROR] ❌ CRITICAL EXCEPTION in on_new_message: list index out of range
[2025-02-12 10:35:10] [ERROR]    Traceback (most recent call last):
[2025-02-12 10:35:10] [ERROR]    File "bot_zone.py", line 1100, in on_new_message
[2025-02-12 10:35:10] [ERROR]        targets = parse_targets(text)
[2025-02-12 10:35:10] [ERROR]    ...
[2025-02-12 10:35:10] [ERROR]    Message ID: 12345
[2025-02-12 10:35:10] [ERROR]    Message text: BUY ZONE: 2650.00 - 2652.00...
```

---

## 🔍 How to Diagnose Common Issues

### Issue: Bot Not Detecting Messages

**Step 1:** Check if messages are being received at all:
```
Look for: [INFO] 📥 [RAW] chat_id=...
```
- **If YES:** Bot is receiving messages → parsing issue
- **If NO:** Bot not connected to Telegram → connection issue

**Step 2:** Check why messages are ignored:
```
Look for: [DEBUG] ⏭️  Message not recognized as signal - ignoring
Look for: [DEBUG]    Checks: TP3_HIT=False | CANCEL=False | SIDE=None | ACTIVE=False
```
- All checks False = message doesn't match any pattern

**Step 3:** Check Telegram connection:
```
Look for: [INFO] 🔌 MT5: ✅ | Telegram: ❌
```
- If Telegram shows ❌, connection is dead

**Step 4:** Check last message time:
```
Look for: [WARN] ⚠️ No Telegram activity for 800s - connection may be dead
```

---

### Issue: Bot Crashed/Stopped

**Step 1:** Find the last heartbeat:
```
Look for last: [INFO] 💓 HEARTBEAT #XX - BOT ALIVE
```
- Note the time and heartbeat number

**Step 2:** Look for errors AFTER the last heartbeat:
```
Look for: [ERROR] ❌ CRITICAL:
Look for: [ERROR] Traceback
```

**Step 3:** Check if monitors are alive:
```
Look for: [INFO] 🤖 Monitors: Zone=❌ | Breakeven=❌
```
- If both dead → bot crashed completely
- If one dead → that specific monitor crashed

**Step 4:** Check for MT5/Telegram disconnections:
```
Look for: [WARN] 🚨 MT5 connection unhealthy
Look for: [ERROR] ❌ Failed to restart Telegram client
```

---

### Issue: Trades Not Executing

**Step 1:** Check if zone was detected:
```
Look for: [INFO] 🎯 ZONE DETECTED: BUY 2650.00-2652.00
```
- **If NO:** Parsing failed (see message debugging)

**Step 2:** Check if zone was stored as pending:
```
Look for: [INFO] 🧭 Pending zone stored: BUY 2650.00-2652.00
```
- **If NO:** Zone wasn't accepted

**Step 3:** Check zone monitoring:
```
Look for: [DEBUG] 🔍 Zone check: BUY | Zone: 2650.00-2652.00 | Bid: $2651.50...
```
- See if price is reaching the zone

**Step 4:** Check for execution:
```
Look for: [INFO] ✅ ZONE TRIGGER: BUY zone 2650.00-2652.00 - executing trade
Look for: [INFO] 🚀 Opening BUY position with lot size 0.01...
Look for: [INFO] ✅ Position opened successfully - Ticket: 12345
```

**Step 5:** Check for failures:
```
Look for: [ERROR] ❌ Order FAILED - Retcode: 10027
Look for: [ERROR] ❌ CRITICAL: AutoTrading is DISABLED
```

---

## 📋 What to Send for Help

When asking for help, provide:

### 1. **Last 100 lines of terminal output**
```powershell
# Get last 100 lines (if you redirected output to file)
Get-Content bot_output.log -Tail 100
```

### 2. **Specific time range around the issue**
```
Include logs from 5 minutes BEFORE the issue to 5 minutes AFTER
```

### 3. **Key information to include:**
- Last heartbeat number and time
- Connection status (MT5/Telegram ✅ or ❌)
- Number of messages received/processed
- Any ERROR or CRITICAL messages
- What you expected vs what happened

### 4. **For trade execution issues:**
- The EXACT message that was sent
- Zone detection logs
- Zone trigger logs
- Any failed order logs

---

## 🏃 Running the Bot with Full Logging

### Option 1: Direct Terminal Output
```powershell
cd src
python bot_zone.py
```
All logs print to terminal in real-time

### Option 2: Redirect to File (Recommended)
```powershell
cd src
python bot_zone.py 2>&1 | Tee-Object -FilePath "bot_output.log"
```
- Logs to terminal AND file
- Can review history later
- Use Ctrl+C to stop

### Option 3: Background with File Logging
```powershell
cd src
python bot_zone.py > bot_output.log 2>&1
```
- Runs in background
- All output to file only
- View file with: `Get-Content bot_output.log -Wait -Tail 50`

---

## ⚙️ Adjusting Log Verbosity

Edit `bot_zone.py` line ~50:
```python
LOG_LEVEL = "INFO"  # Options: "DEBUG" (verbose), "INFO" (normal), "WARN" (quiet)
```

- **DEBUG:** See everything (parsing details, every check, etc.)
- **INFO:** Normal operation (messages, trades, status)
- **WARN:** Only warnings and errors

**Recommended:** Start with INFO, switch to DEBUG if issues occur

---

## 🎯 Key Success Indicators

**Bot is healthy when you see:**
1. ✅ Regular heartbeats every 60 seconds
2. ✅ Connection status shows MT5=✅ and Telegram=✅
3. ✅ Monitors show Zone=✅ and Breakeven=✅
4. ✅ Messages being received (count increasing)
5. ✅ No ERROR or CRITICAL messages
6. ✅ Last message time < 10 minutes ago (during market hours)

**Bot needs attention when you see:**
1. ❌ No heartbeat for > 2 minutes
2. ❌ Connection shows ❌ instead of ✅
3. ❌ Monitors show ❌ instead of ✅
4. ❌ Connection losses > 0 and increasing
5. ❌ ERROR or CRITICAL messages appearing
6. ❌ Messages received not increasing (during active channel times)

---

## 🔧 Quick Troubleshooting Commands

### Check if bot is running:
```powershell
Get-Process python | Where-Object {$_.CommandLine -like "*bot_zone.py*"}
```

### View live logs:
```powershell
Get-Content bot_output.log -Wait -Tail 50
```

### Search for errors:
```powershell
Select-String -Path bot_output.log -Pattern "ERROR|CRITICAL" | Select-Object -Last 20
```

### Count messages received:
```powershell
Select-String -Path bot_output.log -Pattern "NEW MESSAGE RECEIVED" | Measure-Object
```

### Check last heartbeat:
```powershell
Select-String -Path bot_output.log -Pattern "HEARTBEAT" | Select-Object -Last 1
```

---

## 🆘 Emergency Recovery

If bot is completely stuck:

1. **Stop the bot:** Press Ctrl+C (or kill process)
2. **Check logs:** Look for last ERROR/CRITICAL message
3. **Clear any corrupt session files:**
   ```powershell
   Remove-Item trading_bot_session.session -ErrorAction SilentlyContinue
   ```
4. **Restart bot:** `python src/bot_zone.py`
5. **Watch startup logs** - should show all ✅ statuses

---

## 📞 Support Information Format

When reporting issues, use this template:

```
ISSUE: [Brief description]

BOT STATUS:
- Last Heartbeat: #XX at HH:MM:SS
- Uptime: X.XX hours
- Messages Received: XX
- Trades Executed: XX
- MT5 Connected: YES/NO
- Telegram Connected: YES/NO
- Monitors Alive: Zone=YES/NO, Breakeven=YES/NO

ERROR LOGS:
[Paste relevant ERROR/CRITICAL messages here]

EXPECTED BEHAVIOR:
[What should have happened]

ACTUAL BEHAVIOR:
[What actually happened]

MESSAGE THAT CAUSED ISSUE (if applicable):
[Exact message text]
```

---

## ✅ Testing the Improvements

Run this test to verify all features work:

1. **Start bot** - Should see initialization messages
2. **Wait 1 minute** - Should see first heartbeat
3. **Send test message** - Should see detailed parsing logs
4. **Wait 5 minutes** - Should see full status report
5. **Check metrics** - All counts should be updating

All green = bot is healthy! 🎉

---

## 🎓 Understanding Log Levels

| Level | Symbol | When Used | Example |
|-------|--------|-----------|---------|
| DEBUG | 🔍 | Detailed internal operations | "Parsing: side=(BUY)" |
| INFO | ℹ️ | Normal important events | "ZONE DETECTED" |
| WARN | ⚠️ | Potential issues, recoverable | "MT5 connection unhealthy" |
| ERROR | ❌ | Serious errors, failed operations | "Order FAILED" |
| CRITICAL | 🚨 | System-level failures | "Heartbeat monitor exception" |

**Rule of thumb:**
- DEBUG = For developers debugging issues
- INFO = Normal operations you want to see
- WARN = Something unusual but handled
- ERROR = Something failed but bot continues
- CRITICAL = Something failed that affects bot stability

---
