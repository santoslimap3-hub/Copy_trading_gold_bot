#!/usr/bin/env python3
"""
Weekly Trade Signal Analyzer
============================
Fetches last week's messages from the main zone channel,
tracks each zone signal and its outcome, then simulates
profitability assuming:
  - Bot targets TP3
  - TP1 hit → SL moves to breakeven
  - TP3 and SL have 1:1 R:R
  - Trades that only hit TP1 or TP2 (but not TP3) close at breakeven (0R)
  - TP3 hit = +1R
  - SL hit (no target reached) = -1R
"""

import asyncio
import re
import sys
import os
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from typing import Optional, List, Tuple, Dict

# ── Telegram credentials (same as bot_zone.py) ──
API_ID = 34597981
API_HASH = "2cd59609b6cacb56da261e43fdb897ea"
CHANNEL_ID = -1003142865169  # Main channel (Gold Scalping - Analysis & Zones)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SESSION_FILE = os.path.join(PROJECT_ROOT, "src", "trading_bot_session_zone")

# ── Date range: last full trading week (Monday → Friday) ──
# Today is Saturday Feb 14 2026, so last week is Feb 9 – Feb 13
TODAY = datetime.now(timezone.utc).date()
# Find last Monday: go back to the most recent Monday that starts a *completed* week
days_since_monday = TODAY.weekday()  # 0=Mon, 5=Sat, 6=Sun
if days_since_monday == 0:
    # It's Monday — "last week" means the week before
    last_monday = TODAY - timedelta(days=7)
else:
    last_monday = TODAY - timedelta(days=days_since_monday)
    # If today is Tue-Fri the current week isn't done yet; go back one more week
    if days_since_monday <= 4:
        last_monday = last_monday - timedelta(days=7)

WEEK_START = datetime(last_monday.year, last_monday.month, last_monday.day, 0, 0, 0, tzinfo=timezone.utc)
WEEK_END   = WEEK_START + timedelta(days=5)  # Friday 00:00 → captures all of Fri

print(f"Analyzing week: {WEEK_START.strftime('%A %Y-%m-%d')} → {(WEEK_END - timedelta(seconds=1)).strftime('%A %Y-%m-%d')}")
print()

# ===================== PARSING (reused from bot_zone.py) =====================

def is_tp1_hit(text: str) -> bool:
    t = (text or "").upper()
    return bool(re.search(r"\bTP\s*1\s+HIT\b|\bTARGET\s*1\b", t))

def is_tp2_hit(text: str) -> bool:
    t = (text or "").upper()
    return bool(re.search(r"\bTP\s*2\s+HIT\b|\bTARGET\s*2\b", t))

def is_tp3_hit(text: str) -> bool:
    t = (text or "").upper()
    return bool(re.search(r"\bTP\s*3\s+HIT\b|\bTARGET\s*3\b", t))

def is_cancel_or_failed(text: str) -> bool:
    t = (text or "").lower()
    keywords = ["cancel", "zone failed", "failed", "missed", "missed by", "stopped", "stop loss", "sl hit"]
    return any(k in t for k in keywords)

def is_informational(text: str) -> bool:
    t = (text or "").lower()
    keywords = [
        "closed was noted", "noted as", "due to the speed",
        "was closed", "high risk buy zone wich was closed",
        "high risk sell zone wich was closed",
    ]
    return any(k in t for k in keywords)

def is_high_risk(text: str) -> bool:
    t = (text or "").lower()
    return "high risk" in t and not is_informational(text)

def parse_zone_side(text: str) -> Optional[str]:
    t = (text or "").lower()
    if "buy zone" in t:
        return "BUY"
    if "sell zone" in t:
        return "SELL"
    return None

def parse_zone_range(text: str) -> Optional[Tuple[float, float]]:
    pattern = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|\u2013)\s*(\d+(?:\.\d+)?)")
    match = pattern.search(text or "")
    if not match:
        return None
    p1, p2 = float(match.group(1)), float(match.group(2))
    return (min(p1, p2), max(p1, p2))

def parse_targets(text: str) -> List[float]:
    t = text or ""
    idx = re.search(r"targets?", t, re.IGNORECASE)
    if not idx:
        return []
    segment = t[idx.end():]
    nums = re.findall(r"\b\d+(?:\.\d+)?\b", segment)
    return [float(n) for n in nums if float(n) >= 100]

def parse_sl(text: str) -> Optional[float]:
    t = text or ""
    patterns = [
        r"SL\s*:\s*([0-9]+(?:\.[0-9]+)?)",
        r"\b(?:invalid|sl)\b\s+([0-9]+(?:\.[0-9]+)?)",
        r"(?:invalid|sl)\s*/?\s*(?:sl\s*)?([0-9]+(?:\.[0-9]+)?)",
    ]
    for p in patterns:
        m = re.search(p, t, re.IGNORECASE)
        if m:
            price = float(m.group(1))
            if price >= 100:
                return price
    return None

def extract_text(msg) -> str:
    if hasattr(msg, "raw_text") and msg.raw_text:
        return msg.raw_text
    if hasattr(msg, "message") and msg.message:
        return msg.message
    if hasattr(msg, "text") and msg.text:
        return msg.text
    return ""

# ===================== TRADE TRACKING =====================

class Trade:
    """Represents a single zone trade signal and its outcome."""
    def __init__(self, side: str, zone_range: Tuple[float, float],
                 targets: List[float], sl: Optional[float],
                 msg_id: int, timestamp: datetime, raw_text: str):
        self.side = side
        self.zone_low, self.zone_high = zone_range
        self.targets = targets
        self.sl = sl
        self.msg_id = msg_id
        self.timestamp = timestamp
        self.raw_text = raw_text

        # Outcome tracking
        self.tp1_hit = False
        self.tp2_hit = False
        self.tp3_hit = False
        self.failed = False          # explicit fail / cancel
        self.superseded = False      # next zone arrived without any outcome
        self.outcome_text = ""       # the message that resolved this trade
        self.outcome_time: Optional[datetime] = None

    @property
    def resolved(self) -> bool:
        return self.tp3_hit or self.failed or self.superseded

    @property
    def highest_target(self) -> int:
        if self.tp3_hit:
            return 3
        if self.tp2_hit:
            return 2
        if self.tp1_hit:
            return 1
        return 0

    @property
    def result_R(self) -> float:
        """
        Return the R-multiple for this trade under the user's strategy:
          Bot targets TP3. TP1 hit → SL to breakeven.
          TP3:SL = 1:1 R:R.

        Outcomes:
          - TP3 hit           → +1R  (full win)
          - TP1 or TP2 only   → 0R   (breakeven, SL was moved to entry)
          - SL hit (no target)→ -1R  (full loss)
        """
        if self.tp3_hit:
            return 1.0
        if self.tp1_hit or self.tp2_hit:
            return 0.0   # breakeven
        return -1.0       # stop loss

    @property
    def result_label(self) -> str:
        if self.tp3_hit:
            return "TP3 HIT (+1R)"
        if self.tp2_hit:
            return "TP2 only (BE 0R)"
        if self.tp1_hit:
            return "TP1 only (BE 0R)"
        if self.failed:
            return "FAILED / SL (-1R)"
        if self.superseded:
            return "NO OUTCOME / SL (-1R)"
        return "UNRESOLVED"

    def __repr__(self):
        ts = self.timestamp.strftime("%a %m/%d %H:%M")
        return f"[{ts}] {self.side} {self.zone_low}-{self.zone_high} → {self.result_label}"


async def fetch_and_analyze():
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()
    print("Connected to Telegram\n")

    # ── Fetch all messages from the week ──
    print(f"Fetching messages from channel {CHANNEL_ID} ...")
    all_messages = []

    # Telethon iter_messages with offset_date returns messages BEFORE that date, newest first
    # We'll fetch in batches going backwards from WEEK_END
    async for msg in client.iter_messages(CHANNEL_ID, offset_date=WEEK_END, reverse=False, limit=2000):
        if msg.date < WEEK_START:
            break
        all_messages.append(msg)

    # Reverse so they're in chronological order
    all_messages.reverse()
    print(f"Fetched {len(all_messages)} messages from the week\n")

    if not all_messages:
        print("No messages found for the specified week. Check CHANNEL_ID and date range.")
        await client.disconnect()
        return

    # ── Walk through messages and build trades ──
    trades: List[Trade] = []
    current_trade: Optional[Trade] = None

    for msg in all_messages:
        text = extract_text(msg).strip()
        if not text:
            continue

        # Skip informational / high‑risk
        if is_informational(text):
            continue
        if is_high_risk(text):
            continue

        # ── Check for target‑hit messages ──
        if is_tp1_hit(text):
            if current_trade and not current_trade.resolved:
                current_trade.tp1_hit = True
                current_trade.outcome_text = text[:120]
                current_trade.outcome_time = msg.date
            continue

        if is_tp2_hit(text):
            if current_trade and not current_trade.resolved:
                current_trade.tp2_hit = True
                current_trade.outcome_text = text[:120]
                current_trade.outcome_time = msg.date
            continue

        if is_tp3_hit(text):
            if current_trade and not current_trade.resolved:
                current_trade.tp3_hit = True
                current_trade.outcome_text = text[:120]
                current_trade.outcome_time = msg.date
            continue

        # ── Check for cancel / failed ──
        if is_cancel_or_failed(text):
            if current_trade and not current_trade.resolved:
                current_trade.failed = True
                current_trade.outcome_text = text[:120]
                current_trade.outcome_time = msg.date
            continue

        # ── Check for new zone signal ──
        side = parse_zone_side(text)
        zone_range = parse_zone_range(text)

        if side and zone_range:
            # If there's an unresolved trade, it was superseded (→ SL hit)
            if current_trade and not current_trade.resolved:
                current_trade.superseded = True
                current_trade.outcome_text = "(Superseded by next zone)"
                current_trade.outcome_time = msg.date

            targets = parse_targets(text)
            sl = parse_sl(text)

            current_trade = Trade(
                side=side,
                zone_range=zone_range,
                targets=targets,
                sl=sl,
                msg_id=msg.id,
                timestamp=msg.date,
                raw_text=text[:200],
            )
            trades.append(current_trade)
            continue

        # Immediate entry (side but no range) — treat as a zone trade
        if side and not zone_range:
            if current_trade and not current_trade.resolved:
                current_trade.superseded = True
                current_trade.outcome_text = "(Superseded by next zone)"
                current_trade.outcome_time = msg.date

            current_trade = Trade(
                side=side,
                zone_range=(0, 0),
                targets=parse_targets(text),
                sl=parse_sl(text),
                msg_id=msg.id,
                timestamp=msg.date,
                raw_text=text[:200],
            )
            trades.append(current_trade)
            continue

    # If the last trade is still unresolved at end of week, mark as superseded (SL)
    if current_trade and not current_trade.resolved:
        current_trade.superseded = True
        current_trade.outcome_text = "(End of week — no outcome)"

    await client.disconnect()

    # ===================== RESULTS =====================
    if not trades:
        print("No zone trade signals found during the week.")
        return

    # ── Detailed trade log ──
    print("=" * 80)
    print("  TRADE-BY-TRADE LOG")
    print("=" * 80)
    for i, t in enumerate(trades, 1):
        ts = t.timestamp.strftime("%a %m/%d %H:%M UTC")
        zone_str = f"{t.zone_low:.0f}-{t.zone_high:.0f}" if t.zone_low else "IMMEDIATE"
        tgt_str = ", ".join(f"{p:.0f}" for p in t.targets) if t.targets else "N/A"
        sl_str = f"{t.sl:.0f}" if t.sl else "N/A"

        print(f"\n  Trade #{i}  |  {ts}")
        print(f"    Signal   : {t.side} zone {zone_str}")
        print(f"    Targets  : {tgt_str}")
        print(f"    SL       : {sl_str}")
        print(f"    TP1 hit  : {'Yes' if t.tp1_hit else 'No'}")
        print(f"    TP2 hit  : {'Yes' if t.tp2_hit else 'No'}")
        print(f"    TP3 hit  : {'Yes' if t.tp3_hit else 'No'}")
        print(f"    Result   : {t.result_label}")
        if t.outcome_text:
            print(f"    Outcome  : {t.outcome_text[:100]}")

    # ── Summary statistics ──
    total = len(trades)
    tp3_wins   = sum(1 for t in trades if t.tp3_hit)
    tp2_only   = sum(1 for t in trades if t.tp2_hit and not t.tp3_hit)
    tp1_only   = sum(1 for t in trades if t.tp1_hit and not t.tp2_hit and not t.tp3_hit)
    breakeven  = tp1_only + tp2_only
    sl_losses  = sum(1 for t in trades if t.result_R == -1.0)
    total_R    = sum(t.result_R for t in trades)
    win_rate   = (tp3_wins / total * 100) if total > 0 else 0
    be_rate    = (breakeven / total * 100) if total > 0 else 0
    loss_rate  = (sl_losses / total * 100) if total > 0 else 0

    print()
    print("=" * 80)
    print("  WEEKLY PERFORMANCE SUMMARY")
    print("=" * 80)
    print(f"  Period           : {WEEK_START.strftime('%a %b %d')} → {(WEEK_END - timedelta(seconds=1)).strftime('%a %b %d, %Y')}")
    print(f"  Total signals    : {total}")
    print()
    print(f"  TP3 Wins (+1R)   : {tp3_wins:3d}  ({win_rate:.1f}%)")
    print(f"  TP2 only (BE)    : {tp2_only:3d}  (breakeven)")
    print(f"  TP1 only (BE)    : {tp1_only:3d}  (breakeven)")
    print(f"  Breakeven total  : {breakeven:3d}  ({be_rate:.1f}%)")
    print(f"  SL Losses (-1R)  : {sl_losses:3d}  ({loss_rate:.1f}%)")
    print()
    print(f"  Net R            : {total_R:+.1f}R")
    print()

    if total_R > 0:
        print(f"  VERDICT: PROFITABLE week! Net {total_R:+.1f}R")
        print(f"           With 1:1 R:R at 5% risk per trade → ~{total_R * 5:.1f}% account growth")
    elif total_R == 0:
        print(f"  VERDICT: BREAKEVEN week. No net gain or loss.")
    else:
        print(f"  VERDICT: LOSING week. Net {total_R:+.1f}R")
        print(f"           With 1:1 R:R at 5% risk per trade → ~{abs(total_R) * 5:.1f}% account drawdown")

    print()
    print("  Strategy assumptions:")
    print("    - Bot enters every signal (no high-risk skips)")
    print("    - Bot targets TP3 with 1:1 R:R (SL = same distance as TP3)")
    print("    - TP1 hit → SL moves to breakeven")
    print("    - Trades reaching only TP1 or TP2 close at breakeven (0R)")
    print("    - Trades with no target hit before next zone = SL hit (-1R)")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(fetch_and_analyze())
