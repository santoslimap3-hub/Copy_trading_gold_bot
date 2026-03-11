
from flask import Flask, request, jsonify
from flask_cors import CORS

import os
import json

app = Flask(__name__)
CORS(app) 



@app.route("/")
def home():
    return "Home"

@app.route("/trade_history")
def trade_history():
    # Example: return contents of data/bot_trades.json
    json_path = os.path.join(os.path.dirname(__file__), '../data/bot_trades.json')
    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/entry_stats")
def entry_stats():
    # Example: return contents of data/entry_stats.json
    json_path = os.path.join(os.path.dirname(__file__), '../data/entry_stats_779.json')
    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route("/trade_outcomes")
def trade_outcomes():
    # Example: return contents of data/trade_outcomes.json
    json_path = os.path.join(os.path.dirname(__file__), '../data/trade_outcomes_779.json')
    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0',debug=True)