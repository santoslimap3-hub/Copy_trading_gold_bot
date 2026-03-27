from flask import Flask, jsonify, abort
import json
import os
import subprocess
import sys
from flask_cors import CORS


app = Flask(__name__)
CORS(app)
TOOLS_DIR  = os.path.dirname(os.path.abspath(__file__))
BASE_DIR   = os.path.dirname(TOOLS_DIR)

BOT_TRADES_PATH       = os.path.join(BASE_DIR, "data", "synthetic_bot_trades.json")
BOT_TRADES_MOMS_PATH  = os.path.join(BASE_DIR, "data", "synthetic_bot_trades.json")
SIGNAL_RECORDS_PATH   = os.path.join(BASE_DIR, "data", "synthetic_signal_records.json")

ERROR_RECORDS_PATH    = os.path.join(BASE_DIR, "data", "errors_780.json")
EXPORTER_MAIN = os.path.join(TOOLS_DIR, "bot_trades_exporter.py")
EXPORTER_MOMS = os.path.join(TOOLS_DIR, "bot_trades_exporter_moms_account.py")


def _run_exporter(script_path: str):
    """Run a trades exporter script and suppress its stdout/stderr."""
    try:
        subprocess.run(
            [sys.executable, script_path],
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        pass  # Return stale data rather than hanging the request
    except Exception:
        pass


def _load_json(path: str):
    if not os.path.exists(path):
        abort(404, description=f"Data file not found: {os.path.basename(path)}")
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            abort(500, description=f"Malformed JSON in {os.path.basename(path)}: {e}")


@app.route("/trade_history", methods=["GET"])
def trade_history():
    """Refresh bot_trades.json from MT5, then return it."""
    data = _load_json(BOT_TRADES_PATH)
    return jsonify(data)


@app.route("/signal_records", methods=["GET"])
def signal_record():
    """Return the full signal_records.json payload."""
    data = _load_json(SIGNAL_RECORDS_PATH)
    return jsonify(data)

@app.route("/error_records", methods=["GET"])
def error_records():
    """Return the full error_records.json payload."""
    data = _load_json(ERROR_RECORDS_PATH)
    return jsonify(data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
