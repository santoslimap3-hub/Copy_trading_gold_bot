# 🎨 Color Palette & Terminal Guide

## Color Scheme Used

### Status Indicators
```
✅ - Success/Positive (Green)
❌ - Error/Failure (Red)  
⏳ - Waiting/Retry (Yellow)
🚫 - Blocked/Warning (Red/Bold)
⚠️ - Caution (Yellow/Bold)
🔄 - Reconnect/Retry (Yellow)
```

### Emoji Guide
```
📡 - Telegram (Blue)
🤖 - Bot/System (Green)
🎯 - Signal/Target (Yellow)
💰 - Account/Money (Green)
📊 - Data/Positions (Cyan)
📈 - Market/Price (Green)
📉 - Decline/Loss (Red)
🔹 - Market Snapshot (Green)
⚙️ - Symbol Specs (Cyan)
💳 - Margin/Collateral (Cyan)
📏 - Measurement/Distance (Cyan)
🔧 - Adjustment (Yellow)
📤 - Order Send (Cyan)
📝 - Edit/Message (Cyan)
🔍 - Search/Find (Yellow)
🔄 - Loading/Retry (Yellow)
```

## Terminal Color Codes (Rich Library)

### Primary Colors
```
"green"      - ✅ Success, positive operations
"red"        - ❌ Errors, failures
"yellow"     - ⚠️ Warnings, info
"cyan"       - 🔵 Debug info, market data
"magenta"    - 🟣 Field labels, headers
```

### Enhanced Styles
```
"bold green"       - 🟢 Important success (connected, executed)
"bold red"         - 🔴 Critical errors (trade blocked)
"bold yellow"      - 🟡 Important warnings (risk warning)
"bold cyan"        - 🔵 Important info (signal received)
"bold magenta"     - 🟣 Major events (new message)
"white"            - Raw telegram message content
"bright_white"     - Bot startup message
"bright_cyan"      - Important parameters
```

### Background Styles
```
"bold green on dark_green"     - Success banners (MT5 Connected)
"bold yellow on dark_blue"     - Signal banners (New Signal)
"bold red on dark_red"         - Error situations (if needed)
"bold cyan on dark_cyan"       - Edit banners
"bold magenta on dark_magenta" - Telegram messages
```

## Output Examples

### Connection Success
```
╭────────────────────────────────────────╮
│          ✅ MT5 CONNECTED              │
╰────────────────────────────────────────╯
```
Style: `"bold green on dark_green"` Panel

### New Trading Signal
```
╭────────────────────────────────────────╮
│        🎯 NEW SIGNAL RECEIVED          │
│                 BUY                    │
╰────────────────────────────────────────╯
```
Style: `"bold yellow on dark_blue"` Panel

### Account Information Table
```
╭────────────── 💰 ACCOUNT INFO ─────────────╮
│ Field        │ Value                      │
├──────────────┼────────────────────────────┤
│ Balance      │ $10,500.00 (green text)    │
│ Equity       │ $10,450.25 (green text)    │
│ Free Margin  │ $8,350.25 (green text)     │
╰──────────────┴────────────────────────────╯
```

### Success Message
```
[2025-01-30 14:25:45] Entry price: $2050.50
```
Style: `"green"` - Timestamp green, price highlighted

### Error Message
```
❌ ENTRY FAILED: retcode=10009
```
Style: `"bold red"` - Red background with emoji

### Warning Message
```
⚠️ WARNING: 2 position(s) already open on XAUUSD
```
Style: `"bold yellow"` - Yellow for caution

## Rich Library Features Used

### Panels
- Beautiful borders
- Title display
- Padding and alignment
- Color styling

### Tables
- Column headers with colors
- Multiple styles per column
- Expandable rows
- Professional formatting

### Text Styling
- Color names (green, red, yellow, cyan, magenta)
- Text attributes (bold, underline, italic)
- Background colors (on dark_green, on dark_red, etc.)
- Combinations (bold green, bright_white, etc.)

## Terminal Requirements

- **Minimum**: 80 characters wide (shows all content)
- **Recommended**: 120+ characters wide (better table display)
- **Font**: Any monospace font (Courier New, Consolas, Monospace)
- **Support**: Any modern terminal that supports ANSI colors

## Supported Terminals

✅ Works great on:
- Windows Terminal (Microsoft)
- VSCode Integrated Terminal
- PowerShell
- CMD (with ANSI color support)
- macOS Terminal
- Linux/Unix terminals
- Git Bash

## Customization

To change colors, edit the `style` parameter in:

```python
dbg(f"message", style="green")              # Change "green" to any color
banner("TITLE", style="bold green on dark_green")  # Change panel color
```

Available style names in Rich:
```
Colors: black, red, green, yellow, blue, magenta, cyan, white
Brightness: dark_, bright_
Attributes: bold, underline, italic, reverse, conceal, strike
Background: on <color> (on dark_green, on bright_red, etc.)
Combinations: bold bright_cyan, underline magenta, etc.
```

## Performance

Rich output has minimal performance impact:
- Color rendering is fast
- Panel/Table building is optimized
- No blocking operations
- Suitable for real-time trading

---

**Enjoy the beautiful terminal experience!** 🎨✨
