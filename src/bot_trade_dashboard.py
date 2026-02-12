#!/usr/bin/env python3
"""Bot Trade Dashboard - Web-based trade analytics"""

import json
import os
import argparse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for dashboard"""
    trades_file = 'src/bot_trades.json'
    
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(self.get_html().encode('utf-8'))
        elif self.path == '/api/trades':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            try:
                with open(self.trades_file, 'r') as f:
                    data = json.load(f)
                self.wfile.write(json.dumps(data).encode('utf-8'))
            except Exception as e:
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass
    
    def get_html(self):
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bot Trade Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1600px; margin: 0 auto; }
        .header {
            background: rgba(255, 255, 255, 0.98);
            border-radius: 16px;
            padding: 30px;
            margin-bottom: 20px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.15);
        }
        h1 { color: #1a1a1a; margin-bottom: 25px; font-size: 24px; font-weight: 700; }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }
        .stat-card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
        }
        .stat-label { font-size: 11px; opacity: 0.85; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }
        .stat-value { font-size: 24px; font-weight: 700; margin-top: 8px; }
        .stat-value.profit { color: #10b981; }
        .stat-value.loss { color: #fbbf24; }
        .stat-value.neutral { color: #e0e7ff; }
        .charts-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .chart-container {
            background: rgba(255, 255, 255, 0.98);
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.15);
        }
        .chart-title { color: #1a1a1a; font-size: 14px; font-weight: 700; margin-bottom: 20px; }
        canvas { max-height: 300px; }
        .trades-table {
            background: rgba(255, 255, 255, 0.98);
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 8px 32px rgba(0,0,0,0.15);
        }
        table { width: 100%; border-collapse: collapse; }
        th {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 14px 16px;
            text-align: left;
            font-weight: 700;
            font-size: 11px;
            text-transform: uppercase;
        }
        td { padding: 14px 16px; border-bottom: 1px solid #f0f0f0; font-size: 12px; }
        tr:hover { background: #f8f9ff; }
        .pnl.profit { color: #10b981; font-weight: 700; }
        .pnl.loss { color: #f59e0b; font-weight: 700; }
        .badge { display: inline-block; padding: 4px 10px; border-radius: 6px; font-size: 10px; font-weight: 600; background: #dbeafe; color: #1e40af; }
        @media (max-width: 768px) { .charts-grid { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 Bot Trade Dashboard</h1>
            <div class="stats-grid" id="statsGrid">
                <div style="text-align: center; color: #999;">Loading...</div>
            </div>
        </div>
        <div class="charts-grid">
            <div class="chart-container">
                <div class="chart-title">📈 Cumulative P&L</div>
                <canvas id="cumulativePnlChart"></canvas>
            </div>
            <div class="chart-container">
                <div class="chart-title">📊 Win/Loss</div>
                <canvas id="winLossChart"></canvas>
            </div>
            <div class="chart-container">
                <div class="chart-title">🏆 TP Hits</div>
                <canvas id="tpHitChart"></canvas>
            </div>
            <div class="chart-container">
                <div class="chart-title">🔚 Close Reasons</div>
                <canvas id="closeReasonChart"></canvas>
            </div>
        </div>
        <div class="trades-table">
            <table>
                <thead>
                    <tr>
                        <th>Ticket</th>
                        <th>Side</th>
                        <th>Entry</th>
                        <th>Close</th>
                        <th>Lot</th>
                        <th>P&L</th>
                        <th>Status</th>
                        <th>Reason</th>
                        <th>R:R</th>
                    </tr>
                </thead>
                <tbody id="tradesBody">
                    <tr><td colspan="9" style="text-align: center; padding: 20px;">Loading...</td></tr>
                </tbody>
            </table>
        </div>
    </div>
    <script>
        let charts = {};
        async function load() {
            try {
                const r = await fetch('/api/trades');
                const d = await r.json();
                if (d.error) { console.error(d.error); return; }
                const s = d.summary || {};
                const t = d.trades || [];
                updateStats(s);
                updateCharts(t);
                updateTable(t);
            } catch(e) { console.error(e); }
        }
        function updateStats(s) {
            const html = `
                <div class="stat-card"><div class="stat-label">Total P&L</div><div class="stat-value ${s.total_pnl >= 0 ? 'profit' : 'loss'}">$${(s.total_pnl || 0).toFixed(2)}</div></div>
                <div class="stat-card"><div class="stat-label">Win Rate</div><div class="stat-value">${(s.win_rate_percent || 0).toFixed(1)}%</div></div>
                <div class="stat-card"><div class="stat-label">Trades</div><div class="stat-value neutral">${s.total_trades || 0}</div></div>
                <div class="stat-card"><div class="stat-label">Wins</div><div class="stat-value profit">${s.total_wins || 0}</div></div>
                <div class="stat-card"><div class="stat-label">Losses</div><div class="stat-value loss">${s.total_losses || 0}</div></div>
                <div class="stat-card"><div class="stat-label">Avg Win</div><div class="stat-value profit">$${(s.avg_win || 0).toFixed(2)}</div></div>
            `;
            document.getElementById('statsGrid').innerHTML = html;
        }
        function updateCharts(t) {
            const c = t.filter(x => x.status === 'CLOSED');
            if (c.length === 0) return;
            let cum = 0;
            const pnls = c.map(x => cum += (x.pnl || 0));
            if (charts.p) charts.p.destroy();
            charts.p = new Chart(document.getElementById('cumulativePnlChart'), {
                type: 'line',
                data: { labels: c.map((_, i) => i + 1), datasets: [{ data: pnls, borderColor: '#667eea', backgroundColor: 'rgba(102, 126, 234, 0.1)', tension: 0.3, fill: true, borderWidth: 2 }] },
                options: { responsive: true, maintainAspectRatio: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
            });
            const w = c.filter(x => (x.pnl || 0) > 0).length;
            const l = c.filter(x => (x.pnl || 0) < 0).length;
            if (charts.wl) charts.wl.destroy();
            charts.wl = new Chart(document.getElementById('winLossChart'), {
                type: 'doughnut',
                data: { labels: ['Wins', 'Losses'], datasets: [{ data: [w, l], backgroundColor: ['#10b981', '#f59e0b'] }] },
                options: { responsive: true, maintainAspectRatio: true, plugins: { legend: { position: 'bottom' } } }
            });
            const tp = {};
            c.forEach(x => { if (x.tp_hit) tp['TP' + x.tp_hit] = (tp['TP' + x.tp_hit] || 0) + 1; });
            if (charts.tp) charts.tp.destroy();
            charts.tp = new Chart(document.getElementById('tpHitChart'), {
                type: 'bar',
                data: { labels: Object.keys(tp), datasets: [{ data: Object.values(tp), backgroundColor: '#667eea' }] },
                options: { responsive: true, maintainAspectRatio: true, indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true } } }
            });
            const r = {};
            c.forEach(x => { const k = x.close_reason || 'UNKNOWN'; r[k] = (r[k] || 0) + 1; });
            if (charts.cr) charts.cr.destroy();
            charts.cr = new Chart(document.getElementById('closeReasonChart'), {
                type: 'bar',
                data: { labels: Object.keys(r), datasets: [{ data: Object.values(r), backgroundColor: '#764ba2' }] },
                options: { responsive: true, maintainAspectRatio: true, indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true } } }
            });
        }
        function updateTable(t) {
            const c = t.filter(x => x.status === 'CLOSED').reverse();
            if (!c.length) { document.getElementById('tradesBody').innerHTML = '<tr><td colspan=9 style="text-align:center;color:#999">No trades</td></tr>'; return; }
            document.getElementById('tradesBody').innerHTML = c.map(x => '<tr><td><strong>' + x.ticket + '</strong></td><td>' + x.side + '</td><td>$' + (x.entry_price || 0).toFixed(5) + '</td><td>$' + (x.close_price || 0).toFixed(5) + '</td><td>' + (x.lot_size || 0).toFixed(4) + '</td><td class="pnl ' + ((x.pnl || 0) >= 0 ? 'profit' : 'loss') + '">$' + (x.pnl || 0).toFixed(2) + '</td><td><span class="badge">' + x.status + '</span></td><td>' + (x.tp_hit ? 'TP' + x.tp_hit : x.close_reason || 'N/A') + '</td><td>' + (x.risk_reward || 0).toFixed(2) + '</td></tr>').join('');
        }
        load();
        setInterval(load, 5000);
    </script>
</body>
</html>"""


def find_trades_file():
    possible_paths = [
        "src/bot_trades.json",
        os.path.join(os.path.dirname(__file__), "bot_trades.json"),
        "bot_trades.json",
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return "src/bot_trades.json"


def run(trades_file=None, port=8764, auto_open=False):
    if trades_file:
        DashboardHandler.trades_file = trades_file
    else:
        DashboardHandler.trades_file = find_trades_file()
    
    server = HTTPServer(('', port), DashboardHandler)
    url = f'http://localhost:{port}'
    
    print(f"\n{'='*70}")
    print(f"🚀 BOT TRADE DASHBOARD")
    print(f"{'='*70}")
    print(f"📊 Dashboard: {url}")
    print(f"📁 Data: {DashboardHandler.trades_file}")
    print(f"✋ Press Ctrl+C to stop\n")
    
    if auto_open:
        webbrowser.open(url)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n✓ Dashboard stopped")
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Bot Trade Dashboard')
    parser.add_argument('--file', default=None, help='Path to bot_trades.json')
    parser.add_argument('--port', type=int, default=8764, help='Port (default: 8764)')
    parser.add_argument('--open', action='store_true', help='Auto-open browser')
    
    args = parser.parse_args()
    run(args.file, args.port, args.open)
