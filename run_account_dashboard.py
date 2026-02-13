#!/usr/bin/env python3
"""
Account Dashboard Launcher
Exports all MT5 trades and opens the account-wide dashboard in the browser.
"""

import http.server
import socketserver
import os
import sys
import webbrowser
import subprocess
from pathlib import Path

# Fix Unicode output on Windows (cp1252 can't handle emoji)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

PORT = 8001  # Different port from bot dashboard (8000)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.path = '/account_dashboard.html'
        return super().do_GET()

    def log_message(self, format, *args):
        # Quieter logging
        pass


def export_trades():
    """Run the account trades exporter to refresh data."""
    exporter_path = os.path.join(SCRIPT_DIR, "src", "account_trades_exporter.py")
    if not os.path.exists(exporter_path):
        print(f"❌ Exporter not found: {exporter_path}")
        return False

    print("📊 Exporting MT5 account trades...")
    result = subprocess.run(
        [sys.executable, exporter_path],
        cwd=SCRIPT_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"⚠️  Export had issues:\n{result.stderr}")
        # Don't fail — the JSON may still exist from a previous run
    else:
        print("✅ Export complete")
    return True


def main():
    os.chdir(SCRIPT_DIR)

    # Step 1: refresh the JSON data
    export_trades()

    json_path = os.path.join(SCRIPT_DIR, "account_trades.json")
    if not os.path.exists(json_path):
        print("❌ account_trades.json not found. Make sure MT5 is running and try again.")
        return 1

    # Step 2: serve the dashboard
    handler = DashboardHandler
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        url = f"http://localhost:{PORT}"
        print(f"\n🚀 Account Dashboard running at {url}")
        print(f"   Press Ctrl+C to stop\n")
        try:
            webbrowser.open(url)
        except Exception:
            print(f"   Open {url} in your browser manually")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 Server stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
