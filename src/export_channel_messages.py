#!/usr/bin/env python3
"""
Channel Message Exporter - Telegram Signal History
Exports all messages from a trading signal channel from the last month to JSON.
Useful for analyzing message patterns and adapting bots to different trading groups.
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

# ===================== CONFIGURATION =====================
# Telegram
API_ID = 34597981
API_HASH = "2cd59609b6cacb56da261e43fdb897ea"
SESSION_FILE = "trading_bot_session"

# Channel to export (set this to your target channel ID)
# To find channel ID, run find_channel.py or use the web client
TARGET_CHANNEL_ID = -1003142865169  # Main trading channel - CHANGE THIS to your target

# Export settings
DAYS_BACK = 30  # Export messages from last 30 days
OUTPUT_FILE = "channel_transcript.json"

# ===================== MAIN =====================

async def export_channel_messages():
    """Export all messages from channel for the last N days to JSON"""
    
    print(f"\n{'='*70}")
    print("📊 TELEGRAM CHANNEL MESSAGE EXPORTER")
    print(f"{'='*70}")
    print(f"🎯 Target Channel ID: {TARGET_CHANNEL_ID}")
    print(f"📅 Exporting last {DAYS_BACK} days")
    print(f"💾 Output file: {OUTPUT_FILE}")
    print(f"{'='*70}\n")
    
    # Create client
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    
    try:
        # Connect to Telegram
        print("📡 Connecting to Telegram...")
        await client.start()
        print("✅ Connected!\n")
        
        # Get channel entity
        print(f"🔍 Fetching channel info (ID: {TARGET_CHANNEL_ID})...")
        try:
            channel = await client.get_entity(TARGET_CHANNEL_ID)
            print(f"✅ Channel found: {channel.title if hasattr(channel, 'title') else 'Unknown'}\n")
        except Exception as e:
            print(f"❌ Failed to get channel: {e}")
            print("   Tip: Make sure the channel ID is correct and you have access.")
            return
        
        # Calculate date cutoff (timezone-aware to match Telegram messages)
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
        print(f"📅 Collecting messages since: {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')} UTC\n")
        
        # Fetch all messages
        messages = []
        message_count = 0
        
        print("⏳ Fetching messages...")
        async for message in client.iter_messages(TARGET_CHANNEL_ID):
            # Stop if we've gone past the cutoff date
            if message.date and message.date < cutoff_date:
                print(f"   Reached cutoff date. Stopped at {message_count} messages.")
                break
            
            # Extract message data
            msg_data = {
                "id": message.id,
                "date": message.date.isoformat() if message.date else None,
                "text": message.text if message.text else "",
                "raw_text": message.raw_text if message.raw_text else "",
                "is_edited": message.edit_date is not None,
                "edit_date": message.edit_date.isoformat() if message.edit_date else None,
                "from_user_id": message.from_id.user_id if message.from_id else None,
                "has_media": message.media is not None,
                "media_type": type(message.media).__name__ if message.media else None,
            }
            
            messages.append(msg_data)
            message_count += 1
            
            # Progress indicator
            if message_count % 100 == 0:
                print(f"   ✓ Fetched {message_count} messages...")
        
        print(f"✅ Total messages fetched: {message_count}\n")
        
        # Save to JSON
        print(f"💾 Saving to {OUTPUT_FILE}...")
        output_data = {
            "export_metadata": {
                "channel_id": TARGET_CHANNEL_ID,
                "export_date": datetime.now(timezone.utc).isoformat(),
                "days_back": DAYS_BACK,
                "cutoff_date": cutoff_date.isoformat(),
                "total_messages": message_count,
            },
            "messages": messages
        }
        
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Saved {message_count} messages to {OUTPUT_FILE}\n")
        
        # Print summary statistics
        print(f"{'='*70}")
        print("📊 EXPORT SUMMARY")
        print(f"{'='*70}")
        print(f"Total messages: {message_count}")
        
        # Count edited messages
        edited_count = sum(1 for m in messages if m["is_edited"])
        print(f"Edited messages: {edited_count}")
        
        # Count media
        media_count = sum(1 for m in messages if m["has_media"])
        print(f"Messages with media: {media_count}")
        
        # Date range
        if messages:
            first_date = messages[0]["date"]
            last_date = messages[-1]["date"]
            print(f"Date range: {first_date[:10]} to {last_date[:10]}")
        
        print(f"\n💡 Use {OUTPUT_FILE} as input for your prompt analysis!")
        print(f"{'='*70}\n")
        
    except SessionPasswordNeededError:
        print("❌ Session password needed - interactive login required")
        print("   Delete the session file and run again: del trading_bot_session.session")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        await client.disconnect()
        print("✅ Disconnected from Telegram")


if __name__ == "__main__":
    # Parse command line arguments
    if len(sys.argv) > 1:
        TARGET_CHANNEL_ID = int(sys.argv[1])
        print(f"📝 Using channel ID from arguments: {TARGET_CHANNEL_ID}")
    
    if len(sys.argv) > 2:
        DAYS_BACK = int(sys.argv[2])
        print(f"📝 Using days_back from arguments: {DAYS_BACK}")
    
    if len(sys.argv) > 3:
        OUTPUT_FILE = sys.argv[3]
        print(f"📝 Using output file from arguments: {OUTPUT_FILE}")
    
    # Run the export
    asyncio.run(export_channel_messages())
