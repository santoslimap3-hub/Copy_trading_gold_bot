from telethon import TelegramClient
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

api_id = 34597981
api_hash = "2cd59609b6cacb56da261e43fdb897ea"

client = TelegramClient("zinra_session", api_id, api_hash)
console = Console()

async def main():
    """Fetch and display all available Telegram channels"""
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
        if dialog.is_channel:
            channel_type = "Channel" if not dialog.entity.megagroup else "Megagroup"
            table.add_row(dialog.name, str(dialog.entity.id), channel_type)
            count += 1
    
    console.print(table)
    console.print(f"\n✅ Found [bold green]{count}[/bold green] channel(s)\n")

with client:
    client.loop.run_until_complete(main())
