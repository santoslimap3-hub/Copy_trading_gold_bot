#!/usr/bin/env python3
"""
Bot Trade Dashboard - Interactive web viewer for bot_trades.json
Run: python bot_trade_dashboard.py --file bot_trades.json --port 8764
Opens a web dashboard with charts and statistics
"""

import argparse
import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import webbrowser
import urllib.parse


HTML_TEMPLATE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bot Trade Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ 
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: #333;
      padding: 20px;
      min-height: 100vh;
    }}
    .container {{ max-width: 1400px; margin: 0 auto; }}
    .header {{
      background: white;
      padding: 30px;
      border-radius: 12px;
      box-shadow: 0 4px 15px rgba(0,0,0,0.1);
      margin-bottom: 30px;
    }}
    h1 {{ color: #667eea; margin-bottom: 10px; }}
    .metadata {{ color: #999; font-size: 14px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 30px; }}
    .card {{
      background: white;
      padding: 25px;
      border-radius: 12px;
      box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    }}
    .metric {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 15px;
      padding-bottom: 15px;
      border-bottom: 1px solid #f0f0f0;
    }}
    .metric:last-child {{ margin-bottom: 0; border-bottom: none; }}
    .metric-label {{ font-size: 14px; color: #999; }}
    .metric-value {{ font-size: 24px; font-weight: bold; color: #667eea; }}
    .metric-value.positive {{ color: #10b981; }}
    .metric-value.negative {{ color: #ef4444; }}
    .metric-value.neutral {{ color: #f59e0b; }}
    .chart-container {{
      background: white;
      padding: 25px;
      border-radius: 12px;
      box-shadow: 0 4px 15px rgba(0,0,0,0.1);
      margin-bottom: 30px;
      position: relative;
      height: 400px;
    }}
    .chart-title {{ font-size: 18px; font-weight: bold; margin-bottom: 15px; color: #333; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    }}
    th {{
      background: #667eea;
      color: white;
      padding: 15px;
      text-align: left;
      font-weight: 600;
    }}
    td {{
      padding: 12px 15px;
      border-bottom: 1px solid #f0f0f0;
    }}
    tr:hover {{ background: #f9f9f9; }}
    .win {{ color: #10b981; font-weight: bold; }}
    .loss {{ color: #ef4444; font-weight: bold; }}
    .breakeven {{ color: #f59e0b; font-weight: bold; }}
    .status-open {{ background: #dcfce7; color: #166534; padding: 4px 8px; border-radius: 4px; }}
    .status-closed {{ background: #fecaca; color: #991b1b; padding: 4px 8px; border-radius: 4px; }}
    .section-title {{
      font-size: 20px;
      font-weight: bold;
      margin-top: 40px;
      margin-bottom: 20px;
      color: white;
      padding: 15px;
      background: rgba(0,0,0,0.1);
      border-radius: 8px;
    }}
    .no-data {{ color: #999; text-align: center; padding: 20px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>🤖 Bot Trade Dashboard</h1>
      <div class="metadata">Last updated: <span id="lastUpdate">{last_updated}</span></div>
      <div class="metadata">Symbol: XAUUSD | Total Trades: <span id="totalTrades">{total_trades}</span></div>
    </div>

    <div class="grid">
      <div class="card">
        <div class="metric">
          <span class="metric-label">Total P&L</span>
          <span class="metric-value {pnl_class}">{total_pnl}</span>
        </div>
        <div class="metric">
          <span class="metric-label">Win Rate</span>
          <span class="metric-value neutral">{win_rate}%</span>
        </div>
        <div class="metric">
          <span class="metric-label">Total Wins / Losses</span>
          <span class="metric-value neutral">{wins} / {losses}</span>
        </div>
      </div>

      <div class="card">
        <div class="metric">
          <span class="metric-label">Avg Win</span>
          <span class="metric-value positive">{avg_win}</span>
        </div>
        <div class="metric">
          <span class="metric-label">Avg Loss</span>
          <span class="metric-value negative">{avg_loss}</span>
        </div>
        <div class="metric">
          <span class="metric-label">Max Drawdown</span>
          <span class="metric-value negative">{max_drawdown}</span>
        </div>
      </div>

      <div class="card">
        <div class="metric">
          <span class="metric-label">Largest Win</span>
          <span class="metric-value positive">{largest_win}</span>
        </div>
        <div class="metric">
          <span class="metric-label">Largest Loss</span>
          <span class="metric-value negative">{largest_loss}</span>
        </div>
        <div class="metric">
          <span class="metric-label">Pending Trades</span>
          <span class="metric-value neutral">{pending}</span>
        </div>
      </div>
    </div>

    <div class="chart-container">
      <div class="chart-title">📈 Cumulative P&L Over Time</div>
      <canvas id="pnlChart"></canvas>
    </div>

    <div class="chart-container">
      <div class="chart-title">📊 Trade Distribution (Win/Loss)</div>
      <canvas id="distributionChart"></canvas>
    </div>

    <div class="chart-container">
      <div class="chart-title">🎯 TP Hit Distribution</div>
      <canvas id="tpChart"></canvas>
    </div>

    <div class="chart-container">
      <div class="chart-title">📍 Close Reason Distribution</div>
      <canvas id="reasonChart"></canvas>
    </div>

    <div class="section-title">📋 Recent Closed Trades</div>
    {closed_trades_table}

    <div class="section-title">🟡 Open Trades</div>
    {open_trades_table}
  </div>

  <script>
    const data = {data_json};
    
    // Cumulative P&L Chart
    const closedTrades = data.trades.filter(t => t.status === 'CLOSED' && t.pnl !== null);
    let cumulative = 0;
    const cumulativePnL = closedTrades.map(t => {{ cumulative += t.pnl; return cumulative; }});
    
    const pnlCtx = document.getElementById('pnlChart').getContext('2d');
    new Chart(pnlCtx, {{
      type: 'line',
      data: {{
        labels: closedTrades.map((t, i) => i + 1),
        datasets: [{{
          label: 'Cumulative P&L',
          data: cumulativePnL,
          borderColor: '#667eea',
          backgroundColor: 'rgba(102, 126, 234, 0.1)',
          fill: true,
          tension: 0.4,
          pointRadius: 3,
          pointHoverRadius: 5,
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{ y: {{ beginAtZero: false, grid: {{ color: 'rgba(0,0,0,0.05)' }} }} }},
        plugins: {{ legend: {{ display: false }} }}
      }}
    }});

    // Win/Loss Distribution
    const wins = closedTrades.filter(t => t.pnl > 0).length;
    const losses = closedTrades.filter(t => t.pnl < 0).length;
    
    const distCtx = document.getElementById('distributionChart').getContext('2d');
    new Chart(distCtx, {{
      type: 'doughnut',
      data: {{
        labels: ['Wins', 'Losses'],
        datasets: [{{
          data: [wins, losses],
          backgroundColor: ['#10b981', '#ef4444'],
          borderColor: ['#059669', '#dc2626'],
          borderWidth: 2
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
          legend: {{ position: 'bottom' }},
          tooltip: {{ callbacks: {{ label: ctx => ctx.label + ': ' + ctx.parsed + ' trades' }} }}
        }}
      }}
    }});

    // TP Hit Distribution
    const tpHits = {{}};
    closedTrades.forEach(t => {{
      if (t.tp_hit) {{
        const tp = 'TP' + t.tp_hit;
        tpHits[tp] = (tpHits[tp] || 0) + 1;
      }}
    }});
    
    const tpCtx = document.getElementById('tpChart').getContext('2d');
    new Chart(tpCtx, {{
      type: 'bar',
      data: {{
        labels: Object.keys(tpHits).sort(),
        datasets: [{{
          label: 'TP Hits',
          data: Object.values(tpHits),
          backgroundColor: '#667eea',
          borderRadius: 6,
          borderSkipped: false
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: 'y',
        plugins: {{ legend: {{ display: false }} }},
        scales: {{ x: {{ beginAtZero: true }} }}
      }}
    }});

    // Close Reason Distribution
    const closeReasons = {{}};
    closedTrades.forEach(t => {{
      const reason = t.close_reason || 'UNKNOWN';
      closeReasons[reason] = (closeReasons[reason] || 0) + 1;
    }});
    
    const reasonCtx = document.getElementById('reasonChart').getContext('2d');
    new Chart(reasonCtx, {{
      type: 'bar',
      data: {{
        labels: Object.keys(closeReasons).sort(),
        datasets: [{{
          label: 'Close Reasons',
          data: Object.values(closeReasons),
          backgroundColor: '#f59e0b',
          borderRadius: 6,
          borderSkipped: false
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: 'y',
        plugins: {{ legend: {{ display: false }} }},
        scales: {{ x: {{ beginAtZero: true }} }}
      }}
    }});
  </script>
</body>
</html>"""


def format_currency(value: float) -> str:
    """Format value as currency"""
    sign = "$" if value >= 0 else "-$"
    return f"{sign}{abs(value):.2f}"


def format_price(value) -> str:
  if value is None:
    return "N/A"
  return f"${value:.5f}"


def format_lot(value) -> str:
  if value is None:
    return "N/A"
  return f"{value:.4f}"


def build_closed_trades_table(data: dict) -> str:
    """Build HTML table for closed trades"""
    trades = data.get("trades", [])
    closed_trades = sorted(
        [t for t in trades if t.get("status") == "CLOSED"],
        key=lambda x: x.get("closed_at", ""),
        reverse=True
    )[:20]  # Last 20
    
    if not closed_trades:
        return '<div class="card"><div class="no-data">No closed trades yet</div></div>'
    
    rows = []
    for trade in closed_trades:
        pnl = trade.get("pnl", 0)
        pnl_class = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"
        
        entry_price = trade.get("entry_price")
        close_price = trade.get("close_price")
        row = f"""
        <tr>
          <td><strong>{trade['ticket']}</strong></td>
          <td>{trade['side']}</td>
          <td>{format_price(entry_price)}</td>
          <td>{format_price(close_price)}</td>
          <td class="{pnl_class}">{format_currency(pnl)}</td>
          <td>{trade.get('tp_hit', '-')}</td>
          <td>{trade.get('close_reason', '-')}</td>
          <td class="status-closed">{trade.get('status', '-')}</td>
        </tr>
        """
        rows.append(row)
    
    return f"""
    <table>
      <thead>
        <tr>
          <th>Ticket</th>
          <th>Side</th>
          <th>Entry</th>
          <th>Close</th>
          <th>P&L</th>
          <th>TP</th>
          <th>Reason</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    """


def build_open_trades_table(data: dict) -> str:
    """Build HTML table for open trades"""
    trades = data.get("trades", [])
    open_trades = [t for t in trades if t.get("status") == "OPEN"]
    
    if not open_trades:
        return '<div class="card"><div class="no-data">No open trades</div></div>'
    
    rows = []
    for trade in open_trades:
        entry_price = trade.get("entry_price")
        stop_loss = trade.get("stop_loss")
        lot_size = trade.get("lot_size")
        row = f"""
        <tr>
          <td><strong>{trade['ticket']}</strong></td>
          <td>{trade['side']}</td>
          <td>{format_price(entry_price)}</td>
          <td>{format_price(stop_loss)}</td>
          <td>{format_lot(lot_size)}</td>
          <td class="status-open">{trade.get('status', '-')}</td>
        </tr>
        """
        rows.append(row)
    
    return f"""
    <table>
      <thead>
        <tr>
          <th>Ticket</th>
          <th>Side</th>
          <th>Entry</th>
          <th>SL</th>
          <th>Lot Size</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    """


class Handler(BaseHTTPRequestHandler):
    def __init__(self, file_path, *args, **kwargs):
        self.file_path = file_path
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if not os.path.exists(self.server.file_path):
            data = {
                "metadata": {"created_at": "", "bot_version": "v2", "symbol": "XAUUSD"},
                "summary": {
                    "total_trades": 0, "total_pnl": 0, "total_wins": 0, "total_losses": 0,
                    "win_rate_percent": 0, "avg_win": 0, "avg_loss": 0, "max_drawdown": 0,
                    "largest_win": 0, "largest_loss": 0, "pending_trades": 0
                },
                "trades": []
            }
        else:
            with open(self.server.file_path, 'r', encoding='utf-8') as f:
                try:
                    data = json.load(f)
                except Exception:
                    data = {"trades": [], "summary": {}}

        summary = data.get("summary", {})
        total_pnl = summary.get("total_pnl", 0)
        pnl_class = "positive" if total_pnl >= 0 else "negative"
        
        html = HTML_TEMPLATE.format(
            data_json=json.dumps(data),
            last_updated=summary.get("last_updated", "N/A"),
            total_trades=summary.get("total_trades", 0),
            total_pnl=format_currency(total_pnl),
            pnl_class=pnl_class,
            win_rate=f"{summary.get('win_rate_percent', 0):.2f}",
            wins=summary.get("total_wins", 0),
            losses=summary.get("total_losses", 0),
            avg_win=format_currency(summary.get("avg_win", 0)),
            avg_loss=format_currency(summary.get("avg_loss", 0)),
            max_drawdown=format_currency(summary.get("max_drawdown", 0)),
            largest_win=format_currency(summary.get("largest_win", 0)),
            largest_loss=format_currency(summary.get("largest_loss", 0)),
            pending=summary.get("pending_trades", 0),
            closed_trades_table=build_closed_trades_table(data),
            open_trades_table=build_open_trades_table(data)
        )
        
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))
    
    def log_message(self, format, *args):
        """Suppress default logging"""
        pass


def make_handler(file_path):
    def _handler(*args, **kwargs):
        Handler(file_path, *args, **kwargs)
    return _handler


def run(file_path: str, port: int = 8764, auto_open: bool = False):
    """Start the HTTP server"""
    server = HTTPServer(('127.0.0.1', port), make_handler(file_path))
    server.file_path = file_path
    url = f"http://127.0.0.1:{port}"
    print(f"🚀 Serving bot trades dashboard on {url}")
    print(f"📊 File: {file_path}")
    
    if auto_open:
        try:
            webbrowser.open(url)
        except Exception as e:
            print(f"⚠️ Could not open browser: {e}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n⛔ Stopping server")
        server.server_close()


def find_trades_file():
    """Find bot_trades.json in common locations"""
    possible_paths = [
        "src/bot_trades.json",  # Project root structure (priority)
        os.path.join(os.path.dirname(__file__), "bot_trades.json"),  # Script directory
        "bot_trades.json",  # Current directory
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
    
    return "src/bot_trades.json"  # Default to src/


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Bot Trade Dashboard')
    parser.add_argument('--file', default=None, help='Path to bot_trades.json')
    parser.add_argument('--port', type=int, default=8764, help='Port to serve on')
    parser.add_argument('--open', action='store_true', help='Auto-open browser')
    
    args = parser.parse_args()
    file_path = args.file if args.file else find_trades_file()
    run(file_path, args.port, args.open)
