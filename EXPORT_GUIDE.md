# Channel Transcript Exporter - Usage Guide

## Overview

`export_channel_messages.py` is a tool that exports all messages from a Telegram channel from the last month (or custom timeframe) to a JSON file. This transcript can then be used as input to a prompt for AI-driven analysis to understand the message structure and adapt a bot to a different trading group.

## Features

- ✅ Exports messages from any Telegram channel you have access to
- ✅ Captures metadata: message ID, timestamp, edit info, media type
- ✅ Filters by date (last 30 days by default, configurable)
- ✅ Handles edited messages and marks them
- ✅ Preserves raw text and formatted text
- ✅ Generates summary statistics
- ✅ Outputs clean, well-formatted JSON

## Quick Start

### 1. Find Your Target Channel ID

First, identify the channel you want to export from. You can use the existing tool:

```powershell
python find_channel.py
```

This will list all channels you're in. Find your target channel and note its ID (looks like `-1003349563414`).

### 2. Update Configuration

Edit `export_channel_messages.py` and change:

```python
TARGET_CHANNEL_ID = -1003349563414  # Change this to your target channel
DAYS_BACK = 30                       # Change if you want different timeframe
OUTPUT_FILE = "channel_transcript.json"  # Change filename if desired
```

### 3. Run the Exporter

```powershell
cd src
python export_channel_messages.py
```

Or via command-line arguments:

```powershell
python export_channel_messages.py -1003349563414 30 my_transcript.json
```

### 4. Output JSON Structure

The generated `channel_transcript.json` contains:

```json
{
  "export_metadata": {
    "channel_id": -1003349563414,
    "export_date": "2026-02-10T21:15:30.123456",
    "days_back": 30,
    "cutoff_date": "2026-01-11T21:15:30.123456",
    "total_messages": 145
  },
  "messages": [
    {
      "id": 3110,
      "date": "2026-02-10T20:43:28",
      "text": "XAU USD BUY NOW\n\nTP1 5039\nTP2 5041\nTP3 5043\nSL 5028",
      "raw_text": "XAU USD BUY NOW\n\nTP1 5039\nTP2 5041\nTP3 5043\nSL 5028",
      "is_edited": true,
      "edit_date": "2026-02-10T20:44:02",
      "from_user_id": 123456789,
      "has_media": false,
      "media_type": null
    },
    ...
  ]
}
```

## Using the Transcript for Bot Adaptation

Once you have the transcript, you can use it in a prompt to an AI model:

### Example Prompt

```
I have a trading signal channel with the following message structure:

[PASTE CONTENT OF channel_transcript.json]

Based on these examples, create a parsing function that:
1. Identifies BUY/SELL signals
2. Extracts entry price, stop loss, and take profit levels
3. Handles emojis and formatting variations
4. Manages edited messages

Return the Python code for a parsing function compatible with this message format.
```

## File Structure

The exported JSON is highly structured:

| Field | Purpose |
|-------|---------|
| `id` | Unique message ID (for tracking) |
| `date` | Timestamp when message was sent |
| `text` | Clean text content |
| `raw_text` | Raw text as sent |
| `is_edited` | Whether message was edited |
| `edit_date` | Timestamp of last edit |
| `from_user_id` | User ID of sender (0 if from channel itself) |
| `has_media` | Whether message contains photos/documents |
| `media_type` | Type of media if present |

## Options & Configuration

### Change Date Range

Want last 60 days instead of 30?

```python
DAYS_BACK = 60
```

### Different Output File

```python
OUTPUT_FILE = "gold_trading_signals_feb.json"
```

### Via Command Line

```powershell
python export_channel_messages.py <channel_id> <days> <output_file>

# Example:
python export_channel_messages.py -1003349563414 60 my_signals.json
```

## Tips

1. **Session Management**: The exporter uses the same session file as `bot_v2.py`. If session is expired, just run it interactively (it will ask for 2FA code if needed).

2. **Large Exports**: For channels with 1000+ messages, this may take a minute or two. Progress is shown every 100 messages.

3. **Privacy**: The exported JSON contains all message content. Keep it secure if it contains trading strategy details.

4. **Media**: If messages contain photos/files, the exporter notes the media type but doesn't download the files (too large). Text extraction focuses on the message content.

5. **Edited Messages**: Messages that were edited are marked with `is_edited: true` and include the edit timestamp. This helps identify patterns of corrections/updates.

## Troubleshooting

**"Failed to get channel: ..."**
- Channel ID is wrong
- You don't have access to the channel
- Channel is private and you're not a member

**"Session password needed"**
- Your Telegram session expired
- Delete the session file and run again
- You'll be prompted for 2FA code if needed

**Empty messages list**
- Channel may be empty or no messages in the date range
- Check `cutoff_date` in output to ensure it's what you expected

## Example Workflow

```powershell
# 1. Find your target channel
python find_channel.py

# 2. Export last 30 days of signals
python export_channel_messages.py -1002345678901

# 3. Review the JSON
notepad channel_transcript.json

# 4. Use the JSON content in an AI prompt to generate adapted parsing code

# 5. Integrate the new parsing logic into your bot
```

## Use Cases

- **Bot Adaptation**: Adapt your bot to parse a different trading group's message format
- **Signal Analysis**: Analyze historical signal patterns
- **Documentation**: Create a record of all signals sent
- **Research**: Study how different groups structure trading signals
- **Training**: Use message patterns to train ML models

---

**Created for the Copy Trading Gold Bot**
