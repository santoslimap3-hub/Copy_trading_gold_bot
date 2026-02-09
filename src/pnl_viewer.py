"""
Simple HTTP viewer for pnl.json. Run: python pnl_viewer.py --file pnl.json --port 8765
Opens a small page with an interactive chart (Chart.js from CDN).
"""
import argparse
import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import webbrowser


HTML_TMPL = """<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>PnL Viewer</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>body{font-family:Arial,sans-serif;margin:20px}</style>
</head>
<body>
  <h2>PnL Over Time</h2>
  <canvas id="chart" width="900" height="400"></canvas>
  <script>
    const data = {data_json};
    const labels = data.entries.map(e => e.ts);
    const pnl = data.entries.map(e => e.pnl);

    const ctx = document.getElementById('chart').getContext('2d');
    new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: 'PnL',
          data: pnl,
          borderColor: 'rgba(75, 192, 192, 1)',
          backgroundColor: 'rgba(75, 192, 192, 0.2)',
          fill: true,
        }]
      },
      options: {
        responsive: true,
        scales: { y: { beginAtZero: false } }
      }
    });
  </script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def __init__(self, file_path, *args, **kwargs):
        self.file_path = file_path
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if not os.path.exists(self.server.file_path):
            data = {"initial_balance":0.0, "entries": []}
        else:
            with open(self.server.file_path, 'r', encoding='utf-8') as f:
                try:
                    data = json.load(f)
                except Exception:
                    data = {"initial_balance":0.0, "entries": []}

        html = HTML_TMPL.replace('{data_json}', json.dumps(data))
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))


def make_handler(file_path):
    def _handler(*args, **kwargs):
        Handler(file_path, *args, **kwargs)
    return _handler


def run(file_path: str, port: int = 8765):
    server = HTTPServer(('127.0.0.1', port), make_handler(file_path))
    # attach file_path to server for handler
    server.file_path = file_path
    url = f"http://127.0.0.1:{port}"
    print(f"Serving {file_path} on {url}")
    # Try to open default browser (caller may pass --open to request this)
    try:
        if getattr(run, 'auto_open', False):
            webbrowser.open(url)
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping server")
        server.server_close()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--file', '-f', default='pnl.json')
    p.add_argument('--port', '-p', type=int, default=8765)
    p.add_argument('--open', action='store_true', help='Open the viewer in the default browser')
    args = p.parse_args()
    if args.open:
        setattr(run, 'auto_open', True)
    run(args.file, args.port)
