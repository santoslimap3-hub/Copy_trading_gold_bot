"""Create bot_v2_test.py from bot_v2.py with minimal test-specific patches."""
import ast, sys

src_path = "src/bot_v2.py"
dst_path = "src/bot_v2_test.py"

with open(src_path, "r", encoding="utf-8") as f:
    src = f.read()

original = src

# 1. MAGIC number
assert src.count("MAGIC = 777") == 1, f"Expected 1 'MAGIC = 777', got {src.count('MAGIC = 777')}"
src = src.replace("MAGIC = 777", "MAGIC = 776", 1)

# 2. MT5 paths (two separate paths in initialize_mt5)
mt5_primary_old = r'r"C:\MT5_1\terminal64.exe"'
mt5_primary_new = r'r"C:\MTAccount2\terminal64.exe"'
assert src.count(mt5_primary_old) == 1, f"Expected 1 MT5_1 path, got {src.count(mt5_primary_old)}"
src = src.replace(mt5_primary_old, mt5_primary_new)

mt5_fallback_old = r'r"C:\Program Files\MetaTrader 5\terminal64.exe"'
mt5_fallback_new = r'r"C:\Program Files\MTAccount2\terminal64.exe"'
assert src.count(mt5_fallback_old) == 1, f"Expected 1 fallback MT5 path, got {src.count(mt5_fallback_old)}"
src = src.replace(mt5_fallback_old, mt5_fallback_new)

# 3. Add TEST_CHANNEL_ID after CHANNEL_ID line
channel_line = "CHANNEL_ID = -1003349563414  # Main trading channel (EGN GOLD)"
assert src.count(channel_line) == 1, f"CHANNEL_ID line not found uniquely"
src = src.replace(
    channel_line,
    channel_line + "\nTEST_CHANNEL_ID = -1003817819872  # Test channel for manual signals"
)

# 4. ALLOWED_CHAT_IDS
old_allowed = "ALLOWED_CHAT_IDS = {CHANNEL_ID}"
new_allowed = "ALLOWED_CHAT_IDS = {CHANNEL_ID, TEST_CHANNEL_ID}"
assert src.count(old_allowed) == 1, f"Expected 1 ALLOWED_CHAT_IDS, got {src.count(old_allowed)}"
src = src.replace(old_allowed, new_allowed)

# 5. Add TEST_CHANNEL_ID log line after the CHANNEL_ID log line in main()
old_log = 'log(f"  Main channel ID: {CHANNEL_ID}", "INFO")'
new_log = 'log(f"  Main channel ID: {CHANNEL_ID}", "INFO")\n    log(f"  Test channel ID: {TEST_CHANNEL_ID}", "INFO")'
assert src.count(old_log) == 1, f"Expected 1 channel log line, got {src.count(old_log)}"
src = src.replace(old_log, new_log)

# 6. channel_name assignments — set to [TEST] for test channel, [MAIN] otherwise
old_channel_name = 'channel_name = "[MAIN]"'
new_channel_name = 'channel_name = "[TEST]" if event.chat_id == TEST_CHANNEL_ID else "[MAIN]"'
count = src.count(old_channel_name)
assert count == 3, f"Expected 3 channel_name assignments, got {count}"
src = src.replace(old_channel_name, new_channel_name)

# Verify parse
try:
    ast.parse(src)
    print("AST parse: OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR: {e}")
    sys.exit(1)

# Spot checks
checks = [
    ("MAGIC = 776", 1),
    ("MAGIC = 777", 0),
    ("MTAccount2", 2),
    ("MT5_1", 0),
    ("MetaTrader 5", 0),
    ("TEST_CHANNEL_ID", 6),  # definition + ALLOWED + log + 3x channel_name
    ("[TEST]", 3),
]
for pattern, expected in checks:
    actual = src.count(pattern)
    status = "OK" if actual == expected else f"FAIL (expected {expected})"
    print(f"  '{pattern}': {actual} occurrences — {status}")

with open(dst_path, "w", encoding="utf-8") as f:
    f.write(src)

print(f"\nWritten to {dst_path} ({len(src)} chars)")
