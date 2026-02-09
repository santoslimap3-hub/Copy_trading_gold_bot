from telethon import TelegramClient
from telethon.tl.types import Channel
from telethon.utils import get_peer_id
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
import asyncio

api_id = 34597981
api_hash = "2cd59609b6cacb56da261e43fdb897ea"

client = TelegramClient("zinra_session_telethon", api_id=api_id, api_hash=api_hash)
console = Console()

async def main():
    """Fetch and display all available Telegram channels"""
    await client.start()
    
    banner_panel = Panel(
        "[bold cyan]📡 TELEGRAM CHANNEL SCANNER[/bold cyan]",
        style="bold green on dark_green",
        padding=(1, 2),
    )
    console.print(banner_panel)
    
    table = Table(title="📊 Available Channels", style="bold cyan")
    table.add_column("Channel Name", style="magenta")
    table.add_column("Channel ID", style="green")
    table.add_column("Type", style="yellow")
    
    count = 0
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if isinstance(entity, Channel):
            channel_type = "Megagroup" if getattr(entity, "megagroup", False) else "Channel"
            channel_id = get_peer_id(entity)
            table.add_row(entity.title or "(no title)", str(channel_id), channel_type)
            count += 1
    
    console.print(table)
    console.print(f"\n✅ Found [bold green]{count}[/bold green] channel(s)\n")
    
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
