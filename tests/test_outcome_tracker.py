#!/usr/bin/env python3
"""Quick test for TradeOutcomeTracker"""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trade_outcome_tracker import TradeOutcomeTracker

tmpdir = tempfile.mkdtemp()
t = TradeOutcomeTracker(magic=999, data_dir=tmpdir)

# Register a BUY trade
t.register_trade(1001, "BUY", 2650.0, 2640.0, {1: 2655, 2: 2660, 3: 2665, 4: 2670})
assert t.is_tracking(1001)

# Price hits TP1
hit = t.check_levels(1001, 2655.5)
assert "TP1" in hit, f"Expected TP1, got {hit}"

# Price hits TP2
hit = t.check_levels(1001, 2661.0)
assert "TP2" in hit

# Price comes back to entry (BE)
hit = t.check_levels(1001, 2649.0)
assert "BE" in hit

# Price hits SL
hit = t.check_levels(1001, 2639.0)
assert "SL" in hit

# Close the trade
t.close_trade(1001, -5.0, "SL HIT")

# Check stored outcome
assert len(t._outcomes) == 1
o = t._outcomes[0]
print(f"sequence_str: {o['sequence_str']}")
print(f"levels_hit: {o['levels_hit']}")
assert "TP1" in o["sequence_str"]
assert "TP2" in o["sequence_str"]
assert "BE" in o["sequence_str"]
assert "SL" in o["sequence_str"]

# Verify stats
stats = t.get_stats_summary()
assert stats["total_trades"] == 1
print(f"Stats: {stats}")

# Verify persistence
t2 = TradeOutcomeTracker(magic=999, data_dir=tmpdir)
assert len(t2._outcomes) == 1
print("Persistence OK")

# Test SELL trade
t3 = TradeOutcomeTracker(magic=999, data_dir=tmpdir)
t3.register_trade(2001, "SELL", 2700.0, 2710.0, {1: 2695, 2: 2690, 3: 2685, 4: 2680})
hit = t3.check_levels(2001, 2694.0)
assert "TP1" in hit
hit = t3.check_levels(2001, 2689.0)
assert "TP2" in hit
t3.close_trade(2001, 10.0, "TP2 HIT")
o2 = t3._outcomes[-1]
print(f"SELL sequence: {o2['sequence_str']}")

# Cleanup
shutil.rmtree(tmpdir)
print("ALL TESTS PASSED")
