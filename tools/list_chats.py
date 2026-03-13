import asyncio
from telethon import TelegramClient

API_ID = 34597981
API_HASH = "2cd59609b6cacb56da261e43fdb897ea"
SESSION_FILE = "send_test_signal_session"


async def main():
    async with TelegramClient(SESSION_FILE, API_ID, API_HASH) as client:
        async for dialog in client.iter_dialogs():
            print(f"{dialog.id:>20}  {dialog.name}")


asyncio.run(main())
