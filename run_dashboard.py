#!/usr/bin/env python3
"""
Bot Trades Dashboard Server
Simple HTTP server to display bot trades dashboard
"""

import http.server
import socketserver
import os
import webbrowser
from pathlib import Path

PORT = 8000
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # Serve index/dashboard at root
        if self.path == '/':
            self.path = '/bot_trades_dashboard.html'
        return super().do_GET()

def main():
    os.chdir(SCRIPT_DIR)
    
    handler = DashboardHandler
    
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        url = f"http://localhost:{PORT}"
        print(f"🚀 Bot Trades Dashboard Server Started")
        print(f"📊 Open your browser: {url}")
        print(f"💾 Serving from: {SCRIPT_DIR}")
        print(f"📁 Make sure 'bot_trades.json' is in the same directory")
        print(f"\n⚠️  Press Ctrl+C to stop the server\n")
        
        # Try to open browser automatically
        try:
            webbrowser.open(url)
            print(f"✅ Browser opened automatically")
        except:
            print(f"ℹ️  Could not auto-open browser, please visit {url} manually")
        
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n👋 Server stopped")
            return 0

if __name__ == "__main__":
    main()
