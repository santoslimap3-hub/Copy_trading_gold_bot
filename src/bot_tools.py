#!/usr/bin/env python3
"""
Bot Trade Tools - Quick launcher for all trade analysis tools
Usage: python bot_tools.py [command]
"""

import sys
import os
import subprocess
import argparse

COMMANDS = {
    "analyze": {
        "name": "CLI Analytics",
        "description": "View trade stats in terminal",
        "command": "python src/bot_trade_analyzer.py"
    },
    "analyze-mt5": {
        "name": "MT5 History Analytics",
        "description": "Analyze bot trades directly from MT5 history",
        "command": "python src/bot_trade_analyzer.py --mt5 --days 120 --magics 777,778 --symbol XAUUSD"
    },
    "dashboard": {
        "name": "Web Dashboard",
        "description": "Open interactive web dashboard",
        "command": "python src/bot_trade_dashboard.py --open"
    },
    "view": {
        "name": "View Raw Data",
        "description": "Display bot_trades.json",
        "command": "python -m json.tool src/bot_trades.json"
    },
    "test": {
        "name": "Run Tests",
        "description": "Test the logger with sample data",
        "command": "python src/test_logger.py"
    }
}


def print_header():
    """Print welcome header"""
    print("\n" + "=" * 70)
    print("  🤖 BOT TRADE TOOLS - Quick Launch")
    print("=" * 70)


def show_help():
    """Show available commands"""
    print_header()
    print("\nAvailable Commands:\n")
    
    for key, cmd in COMMANDS.items():
        print(f"  {key:12} | {cmd['name']:20} | {cmd['description']}")
    
    print("\nUsage (from project root):")
    print(f"  python src/bot_tools.py analyze       # {COMMANDS['analyze']['description']}")
    print(f"  python src/bot_tools.py analyze-mt5   # {COMMANDS['analyze-mt5']['description']}")
    print(f"  python src/bot_tools.py dashboard     # {COMMANDS['dashboard']['description']}")
    print(f"  python src/bot_tools.py view          # {COMMANDS['view']['description']}")
    print(f"  python src/bot_tools.py test          # {COMMANDS['test']['description']}")
    print(f"\nOr use interactive menu:")
    print(f"  python src/bot_tools.py              # Interactive menu")
    print()


def run_command(cmd_key):
    """Execute a command"""
    if cmd_key not in COMMANDS:
        print(f"❌ Unknown command: {cmd_key}")
        print("Use 'help' to see available commands")
        return False
    
    cmd = COMMANDS[cmd_key]
    print_header()
    print(f"\n📌 Launching: {cmd['name']}")
    print(f"   {cmd['description']}\n")
    
    try:
        subprocess.run(cmd['command'], shell=True, cwd=os.getcwd())
        return True
    except Exception as e:
        print(f"\n❌ Error running command: {e}")
        return False


def interactive_menu():
    """Show interactive menu"""
    print_header()
    print("\nSelect a tool:\n")
    
    for i, (key, cmd) in enumerate(COMMANDS.items(), 1):
        print(f"  {i}. {cmd['name']:20} - {cmd['description']}")
    
    print()
    try:
        choice = input("Enter number (1-4) or 'q' to quit: ").strip().lower()
        
        if choice == 'q':
            print("👋 Goodbye!\n")
            return False
        
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(COMMANDS):
                cmd_key = list(COMMANDS.keys())[idx]
                return run_command(cmd_key)
        except ValueError:
            pass
        
        print("❌ Invalid choice\n")
        return interactive_menu()
    
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!\n")
        return False


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Bot Trade Tools - Quick launcher for trade analysis',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python bot_tools.py              # Interactive menu
  python bot_tools.py analyze      # Show CLI analytics
  python bot_tools.py dashboard    # Open web dashboard
  python bot_tools.py test         # Run logger tests
        """
    )
    parser.add_argument('command', nargs='?', help='Command to run (analyze, dashboard, view, test)')
    parser.add_argument('--file', help='Data file to analyze (default: bot_trades.json)')
    
    args = parser.parse_args()
    
    # If no command, show interactive menu
    if not args.command:
        success = interactive_menu()
        sys.exit(0 if success else 1)
    
    # Run specified command
    if args.command == 'help':
        show_help()
        sys.exit(0)
    
    success = run_command(args.command)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
