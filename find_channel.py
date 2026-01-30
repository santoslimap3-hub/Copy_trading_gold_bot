from telethon import TelegramClient

api_id = 34597981
api_hash = "2cd59609b6cacb56da261e43fdb897ea"

client = TelegramClient("zinra_session", api_id, api_hash)

async def main():
    async for d in client.iter_dialogs():
        if d.is_channel:
            print(f"{d.name} -> {d.entity.id}")

with client:
    client.loop.run_until_complete(main())
