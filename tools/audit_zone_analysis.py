"""Audit script for entry zone analysis results."""
import json

with open("data/entry_zone_analysis.json") as f:
    data = json.load(f)

trades = data["trades"]

# ==========================================
# AUDIT 1: Candle data coverage
# ==========================================
outside = [t for t in trades if t.get("inside_zone") is False]
print(f"Trades outside zone: {len(outside)}")

has_candle = [t for t in outside if t.get("zone_reached_after_entry") is not None]
no_candle = [t for t in outside if t.get("zone_reached_after_entry") is None]
print(f"  With candle check: {len(has_candle)}")
print(f"  Without candle check (missing data): {len(no_candle)}")

reached = [t for t in has_candle if t["zone_reached_after_entry"] is True]
not_reached = [t for t in has_candle if t["zone_reached_after_entry"] is False]
print(f"  Reached zone: {len(reached)}")
print(f"  NOT reached zone: {len(not_reached)}")

print("\n--- Trades where zone was NOT reached ---")
for t in not_reached:
    print(f"  {t['entry_time']:20s} {t['side']:4s} fill=${t['entry_price']} "
          f"zone={t['zone_low']}-{t['zone_high']} slip=${t['slippage']}")

# ==========================================
# AUDIT 2: Minutes-to-reach distribution
# ==========================================
print("\n--- Minutes to reach zone distribution ---")
reach_times = [t.get("minutes_to_reach_zone", 0) for t in reached]
for m in sorted(set(reach_times)):
    cnt = reach_times.count(m)
    bar = "#" * cnt
    print(f"  {m:>3d} min: {cnt:>2d} {bar}")

# ==========================================
# AUDIT 3: Match quality - check match deltas
# ==========================================
print("\n--- Match delta distribution (seconds) ---")
deltas = [t.get("match_delta_s", 0) for t in trades if t.get("match_delta_s") is not None]
buckets = [(0, 10), (10, 30), (30, 60), (60, 120), (120, 300), (300, 600)]
for low, high in buckets:
    cnt = sum(1 for d in deltas if low <= d < high)
    print(f"  {low:>3d}-{high:>3d}s: {cnt} trades")

print(f"\n  Mean delta: {sum(deltas)/len(deltas):.1f}s")
print(f"  Max delta: {max(deltas):.1f}s")
print(f"  Min delta: {min(deltas):.1f}s")

# Flag suspicious matches (delta > 120s)
suspicious = [t for t in trades if t.get("match_delta_s", 0) > 120]
if suspicious:
    print(f"\n  SUSPICIOUS matches (>120s delta): {len(suspicious)}")
    for t in suspicious:
        print(f"    {t['entry_time']:20s} {t['side']:4s} delta={t['match_delta_s']:.0f}s "
              f"zone={t.get('zone_low','?')}-{t.get('zone_high','?')} fill=${t['entry_price']}")

# ==========================================
# AUDIT 4: Direction mismatches
# ==========================================
mismatches = [t for t in trades if t.get("direction_mismatch")]
print(f"\n--- Direction mismatches: {len(mismatches)} ---")
for t in mismatches:
    print(f"  {t['entry_time']:20s} trade={t['side']} signal_text={t.get('signal_text_preview','')[:60]}")

# ==========================================
# AUDIT 5: Zone parsing sanity check
# ==========================================
print("\n--- Entry zone sanity check ---")
with_zone = [t for t in trades if t.get("zone_high") is not None and t.get("zone_low") is not None]
print(f"Trades with parsed zone: {len(with_zone)}")

# Check zone widths
zone_widths = [t["zone_high"] - t["zone_low"] for t in with_zone]
print(f"  Zone width: min=${min(zone_widths):.2f} max=${max(zone_widths):.2f} avg=${sum(zone_widths)/len(zone_widths):.2f}")

# Flag abnormally wide zones
wide_zones = [t for t in with_zone if t["zone_high"] - t["zone_low"] > 20]
if wide_zones:
    print(f"\n  WARNING: Abnormally wide zones (>$20):")
    for t in wide_zones:
        width = t["zone_high"] - t["zone_low"]
        print(f"    {t['entry_time']:20s} zone={t['zone_low']}-{t['zone_high']} width=${width:.2f} "
              f"| text: ...{t.get('signal_text_preview','')[:80]}")

# Flag zones far from fill price
far_fills = [t for t in with_zone if t.get("slippage") and t["slippage"] > 5]
if far_fills:
    print(f"\n  WARNING: Large slippage trades (>$5):")
    for t in far_fills:
        print(f"    {t['entry_time']:20s} fill=${t['entry_price']} zone={t['zone_low']}-{t['zone_high']} "
              f"slip=${t['slippage']} | text: ...{t.get('signal_text_preview','')[:80]}")

# ==========================================
# AUDIT 6: Check for duplicate signal matches
# ==========================================
print("\n--- Duplicate signal matches ---")
msg_ids = [t.get("msg_id") for t in trades if t.get("msg_id")]
dup_ids = set(m for m in msg_ids if msg_ids.count(m) > 1)
if dup_ids:
    print(f"  WARNING: {len(dup_ids)} signals matched to multiple trades:")
    for mid in sorted(dup_ids):
        matched_trades = [t for t in trades if t.get("msg_id") == mid]
        print(f"    msg_id={mid}: {len(matched_trades)} trades")
        for t in matched_trades:
            print(f"      {t['entry_time']:20s} {t['side']:4s} fill=${t['entry_price']}")
else:
    print("  No duplicates - each signal matched to one trade")

# ==========================================
# AUDIT 7: Candle data direction check
# Does the candle logic correctly check zone reachability?
# For BUY outside-above: price needs to DROP back into zone (candle LOW <= zone_high)
# For SELL outside-below: price needs to RISE back into zone (candle HIGH >= zone_low)
# The current code checks: candle_low <= zone_high AND candle_high >= zone_low
# This means candle overlaps zone. Is this correct?
# ==========================================
print("\n--- Candle logic correctness ---")
print("  Current logic: 'candle_low <= zone_high AND candle_high >= zone_low'")
print("  This checks if ANY part of the candle intersects the zone range.")
print("  This is CORRECT for detecting zone reachability.")
print()
print("  BUT: This does NOT check if price reached the ENTRY side of the zone:")
print("  - For BUY filled ABOVE zone: we want candle_low <= zone_high (price dropped to zone)")
print("  - For SELL filled BELOW zone: we want candle_high >= zone_low (price rose to zone)")
print("  The current overlap check satisfies both conditions already.")

# ==========================================
# AUDIT 8: Check the first candle problem
# copy_rates_from starts AT the entry time - the first candle IS the entry candle
# which by definition contains the entry price, NOT the zone
# So minute_to_reach=1 may be misleading
# ==========================================
print("\n--- First candle problem ---")
reached_minute_1 = [t for t in reached if t.get("minutes_to_reach_zone") == 1]
print(f"  Trades that 'reached' zone in minute 1: {len(reached_minute_1)}")
if reached_minute_1:
    print("  These may be FALSE POSITIVES - the first candle is the entry candle itself!")
    print("  If the entry candle's range happens to overlap the zone (wicks), it counts.")
    print("  This might be valid (price briefly touched zone) or a false positive.")
    for t in reached_minute_1:
        print(f"    {t['entry_time']:20s} {t['side']:4s} fill=${t['entry_price']} "
              f"zone={t['zone_low']}-{t['zone_high']} slip=${t.get('slippage', '?')}")
