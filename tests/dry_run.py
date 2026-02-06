import sys
import os

# Ensure the project root is on sys.path so we can import `script` reliably
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from bot import (
    parse_all_tp_levels,
    parse_tp1_sl,
    parse_tp_sl_update,
    parse_entry_side,
    get_target_tp_for_signal,
    MIN_REASONABLE_PRICE_DEFAULT,
)

samples = [
    "TP1,2,3,4 HIT",
    "TP1 4890",
    "🥇TP1 4890\n🥈TP2 4888\n🥉TP3 4886\n🏅TP4 4884\n      TP5 4870\n\n🚫SL 4901",
    "SL 4901",
    "XAU USD SELL NOW 4993 - 4997",
    "4993 - 4997",
    "4822.5 - 4818.5\n\n🥇TP1 4825\n🥈TP2 4927\n🥉TP3 4929\n🏅TP4 4831\n      TP5 4844\n\n🚫SL 4914.50",
    "TP1,2,3,4 HIT\nTP2 HIT",
]

print(f"MIN_REASONABLE_PRICE_DEFAULT={MIN_REASONABLE_PRICE_DEFAULT}\n")

for s in samples:
    print("---")
    print("RAW:\n", s)
    tp1, sl = parse_tp1_sl(s)
    parsed_all = parse_all_tp_levels(s)
    tp_sl_type, tp_sl_value = parse_tp_sl_update(s)
    entry_side = parse_entry_side(s)
    target_tp = get_target_tp_for_signal(parsed_all) if parsed_all else None

    print(f"parse_tp1_sl -> TP1={tp1} | SL={sl}")
    print(f"parse_all_tp_levels -> {parsed_all}")
    print(f"get_target_tp_for_signal -> {target_tp}")
    print(f"parse_tp_sl_update -> {tp_sl_type}, {tp_sl_value}")
    print(f"parse_entry_side -> {entry_side}")
    print()

print('Dry-run complete')
