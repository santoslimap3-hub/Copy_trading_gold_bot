#!/usr/bin/env python3
"""
Download the latest video from the Gold Scalping Telegram channel.
Uses chunked download with progress tracking and retry logic.
"""

import asyncio
import os
import sys
import time
from telethon import TelegramClient

# Telegram credentials (same as bot_zone.py)
API_ID = 34597981
API_HASH = "2cd59609b6cacb56da261e43fdb897ea"
CHANNEL_ID = -1003142865169  # Gold Scalping - Analysis & Zones
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "video_downloader_session")

# Where to save videos
SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")

MAX_RETRIES = 3


def progress_callback(received, total):
    if total:
        pct = received / total * 100
        mb_recv = received / (1024 * 1024)
        mb_total = total / (1024 * 1024)
        sys.stdout.write(f"\r  Progress: {mb_recv:.1f}/{mb_total:.1f} MB ({pct:.0f}%)")
        sys.stdout.flush()


async def main():
    os.makedirs(SAVE_DIR, exist_ok=True)

    client = TelegramClient(
        SESSION_FILE, API_ID, API_HASH,
        timeout=300,           # 5 min general timeout
        request_retries=5,     # retry individual requests
        connection_retries=5,
        retry_delay=2,
    )
    await client.start()
    print("Connected to Telegram")

    print(f"Searching for the latest video in channel {CHANNEL_ID}...")

    video_msg = None
    async for msg in client.iter_messages(CHANNEL_ID, limit=100):
        if msg.video or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video/")):
            video_msg = msg
            break

    if video_msg is None:
        print("No video found in the last 100 messages.")
        await client.disconnect()
        return

    # Build filename
    date_str = video_msg.date.strftime("%Y%m%d_%H%M%S")
    if video_msg.file and video_msg.file.name:
        filename = f"{date_str}_{video_msg.file.name}"
    else:
        ext = ".mp4"
        if video_msg.document and video_msg.document.mime_type:
            mime = video_msg.document.mime_type
            if "webm" in mime:
                ext = ".webm"
            elif "avi" in mime:
                ext = ".avi"
            elif "mkv" in mime:
                ext = ".mkv"
        filename = f"video_{date_str}{ext}"

    save_path = os.path.join(SAVE_DIR, filename)

    # Show video info
    file_size = video_msg.file.size if video_msg.file else None
    size_info = f" ({file_size / 1024 / 1024:.1f} MB)" if file_size else ""
    print(f"Found video (msg ID: {video_msg.id}, date: {video_msg.date}){size_info}")
    if video_msg.message:
        print(f"  Caption: {video_msg.message[:150]}")

    # Download with retries
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"Downloading (attempt {attempt}/{MAX_RETRIES})...")
            await client.download_media(
                video_msg,
                file=save_path,
                progress_callback=progress_callback,
            )
            print()  # newline after progress bar
            break
        except (asyncio.CancelledError, ConnectionError, TimeoutError) as e:
            print(f"\n  Attempt {attempt} failed: {type(e).__name__}: {e}")
            if attempt < MAX_RETRIES:
                wait = 5 * attempt
                print(f"  Retrying in {wait}s...")
                await asyncio.sleep(wait)
                if not client.is_connected():
                    await client.connect()
            else:
                print("All retries exhausted. Download failed.")
                await client.disconnect()
                return

    file_size_mb = os.path.getsize(save_path) / (1024 * 1024)
    print(f"Saved to: {save_path} ({file_size_mb:.1f} MB)")

    await client.disconnect()
    print("Done!")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        loop.close()
