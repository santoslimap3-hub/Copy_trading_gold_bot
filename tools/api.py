from flask import Flask, jsonify, abort
import json
import os

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_TRADES_PATH = os.path.join(BASE_DIR, "data", "bot_trades.json")
SIGNAL_RECORDS_PATH = os.path.join(BASE_DIR, "data", "signal_records.json")


def _load_json(path: str):
    if not os.path.exists(path):
        abort(404, description=f"Data file not found: {os.path.basename(path)}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@app.route("/trade_history", methods=["GET"])
def trade_history():
    """Return the full bot_trades.json payload."""
    data = _load_json(BOT_TRADES_PATH)
    return jsonify(data)


@app.route("/signal_record", methods=["GET"])
def signal_record():
    """Return the full signal_records.json payload."""
    data = _load_json(SIGNAL_RECORDS_PATH)
    return jsonify(data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
