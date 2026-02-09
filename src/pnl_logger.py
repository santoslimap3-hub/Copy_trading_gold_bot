import json
import os
import time
from typing import Optional

DEFAULT_FILE = "pnl.json"

def now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(path: str, data: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


class PnlLogger:
    def __init__(self, path: str = DEFAULT_FILE):
        self.path = path
        self._data = _load(self.path) or {}
        if "entries" not in self._data:
            self._data["entries"] = []

    def init(self, initial_balance: Optional[float] = None):
        # If file exists but has no initial_balance, set it.
        if initial_balance is not None and not self._data.get("initial_balance"):
            try:
                self._data["initial_balance"] = float(initial_balance)
            except Exception:
                self._data["initial_balance"] = initial_balance
            _save(self.path, self._data)

    def record(self, balance: float, equity: float, ts: Optional[str] = None):
        d = _load(self.path) or {}
        if "entries" not in d:
            d["entries"] = []

        if "initial_balance" not in d:
            # Use balance as fallback initial balance
            try:
                d["initial_balance"] = float(balance)
            except Exception:
                d["initial_balance"] = balance

        try:
            b = float(balance)
            e = float(equity)
        except Exception:
            b = balance
            e = equity

        initial = float(d.get("initial_balance", b))
        pnl = e - initial

        entry = {
            "ts": ts or now_ts(),
            "balance": b,
            "equity": e,
            "pnl": pnl,
        }
        d["entries"].append(entry)
        _save(self.path, d)

    def read(self) -> dict:
        return _load(self.path) or {}


# default singleton
logger = PnlLogger()
