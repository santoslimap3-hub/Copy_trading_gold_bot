#!/usr/bin/env python3
"""
Multi-Account Launcher
Runs bot_v2.py on two MT5 accounts simultaneously using separate processes.

SETUP:
  1. Install MT5 into two separate folders (portable mode).
     - Right-click MT5 installer > Run, choose a custom path like:
       C:\\MT5_Account1 and C:\\MT5_Account2
     - Or copy your existing MT5 folder to a second location.
  2. Log in to a different trading account in each MT5 terminal.
  3. Update ACCOUNT_CONFIGS below with the correct terminal paths.
  4. Run this script: python run_multi_account.py

Each account runs in its own process with its own MT5 connection.
"""

import multiprocessing
import os
import shutil
import sys
import time

# ===================== ACCOUNT CONFIGURATION =====================
# Update these paths to point to each MT5 terminal's terminal64.exe
# Each terminal should already be logged into the desired account.

ACCOUNT_CONFIGS = [
    {
        "name": "Account 1",
        "mt5_path": r"C:\Program Files\MetaTrader 5",  # <-- UPDATE THIS
        "magic": 777,           # Unique magic number for this account
        "risk_pct": 0.05,       # Risk % per trade (5%)
    },
    {
        "name": "Account 2",
        "mt5_path": r"C:\Program Files\MTAccount2",                # <-- UPDATE THIS
        "magic": 779,           # Different magic number to distinguish
        "risk_pct": 0.05,       # Risk % per trade (5%)
    },
]
# =================================================================


def resolve_mt5_path(mt5_path: str) -> str:
    """Resolve MT5 path - accept folder or direct exe path."""
    if os.path.isfile(mt5_path):
        return mt5_path
    # If it's a directory, look for terminal64.exe inside
    exe_path = os.path.join(mt5_path, "terminal64.exe")
    if os.path.isfile(exe_path):
        return exe_path
    return mt5_path  # Return as-is, let mt5.initialize() report the error


def run_bot_for_account(config: dict):
    """Run bot_v2 in a subprocess targeting a specific MT5 terminal."""
    account_name = config["name"]
    print(f"[{account_name}] Starting bot process (PID {os.getpid()})...")

    # Resolve MT5 path (folder or exe)
    mt5_resolved = resolve_mt5_path(config["mt5_path"])

    # Set environment variables so bot_v2 can pick them up
    os.environ["MT5_TERMINAL_PATH"] = mt5_resolved
    os.environ["BOT_MAGIC_NUMBER"] = str(config["magic"])
    os.environ["BOT_RISK_PCT"] = str(config["risk_pct"])
    os.environ["BOT_ACCOUNT_NAME"] = account_name

    # Change to src directory and run bot_v2
    src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    sys.path.insert(0, src_dir)
    os.chdir(src_dir)

    # Import and patch bot_v2 before running
    import bot_v2

    # Override config from environment
    bot_v2.MAGIC = config["magic"]
    bot_v2.RISK_PCT = config["risk_pct"]

    # Patch ensure_mt5_connection to use the specific terminal path
    original_ensure = bot_v2.ensure_mt5_connection

    def patched_ensure_mt5():
        import MetaTrader5 as mt5
        mt5_path = os.environ.get("MT5_TERMINAL_PATH", "")
        print(f"[{account_name}] Initializing MT5 at: {mt5_path}")

        if mt5_path:
            if not mt5.initialize(mt5_path):
                print(f"[{account_name}] MT5 initialization FAILED for path: {mt5_path}")
                print(f"[{account_name}] Error: {mt5.last_error()}")
                sys.exit(1)
        else:
            if not mt5.initialize():
                print(f"[{account_name}] MT5 initialization FAILED (default)")
                sys.exit(1)

        bot_v2.mt5_connected = True
        account = mt5.account_info()
        if account:
            print(f"[{account_name}] Connected: Login={account.login} | Server={account.server} | Balance=${account.balance:.2f}")
        else:
            print(f"[{account_name}] WARNING: Could not retrieve account info")

        bot_v2.check_autotrading_status()

    bot_v2.ensure_mt5_connection = patched_ensure_mt5

    # Patch recover_mt5_connection similarly
    def patched_recover():
        import MetaTrader5 as mt5
        mt5_path = os.environ.get("MT5_TERMINAL_PATH", "")
        print(f"[{account_name}] Attempting MT5 reconnection...")
        try:
            mt5.shutdown()
            time.sleep(2)
            if mt5_path:
                success = mt5.initialize(mt5_path)
            else:
                success = mt5.initialize()
            if success:
                print(f"[{account_name}] MT5 reconnection successful")
                return True
            else:
                print(f"[{account_name}] MT5 reconnection failed")
                return False
        except Exception as e:
            print(f"[{account_name}] MT5 reconnection exception: {e}")
            return False

    bot_v2.recover_mt5_connection = patched_recover

    # Use a unique session file per account to avoid Telegram session conflicts
    safe_name = account_name.replace(" ", "_").lower()
    bot_v2.SESSION_FILE = f"trading_bot_session_{safe_name}"

    import asyncio
    asyncio.run(bot_v2.main())


def main():
    print("=" * 70)
    print("  MULTI-ACCOUNT GOLD TRADING BOT LAUNCHER")
    print("=" * 70)
    print(f"  Launching {len(ACCOUNT_CONFIGS)} account(s)...")
    for cfg in ACCOUNT_CONFIGS:
        print(f"    - {cfg['name']}: {cfg['mt5_path']}")
        print(f"      Magic: {cfg['magic']} | Risk: {cfg['risk_pct']*100:.1f}%")
    print("=" * 70)
    print()

    # ── Pre-flight: copy authenticated Telegram session for each account ──
    # Both accounts share the same Telegram login but need separate session
    # files (SQLite can't be shared across processes). Copy the working
    # session file so each subprocess is already authenticated.
    src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    original_session = os.path.join(src_dir, "trading_bot_session.session")

    if not os.path.isfile(original_session):
        # Also check root directory
        root_session = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading_bot_session.session")
        if os.path.isfile(root_session):
            original_session = root_session

    if os.path.isfile(original_session):
        for cfg in ACCOUNT_CONFIGS:
            safe_name = cfg["name"].replace(" ", "_").lower()
            dest_session = os.path.join(src_dir, f"trading_bot_session_{safe_name}.session")
            try:
                shutil.copy2(original_session, dest_session)
                print(f"  Copied Telegram session for {cfg['name']}")
            except Exception as e:
                print(f"  WARNING: Could not copy session for {cfg['name']}: {e}")
    else:
        print("  WARNING: No authenticated Telegram session found!")
        print("  Run bot_v2.py directly first to log in to Telegram, then use this launcher.")
        sys.exit(1)

    # ── Pre-flight: verify MT5 paths exist ──
    all_paths_ok = True
    for cfg in ACCOUNT_CONFIGS:
        mt5_exe = resolve_mt5_path(cfg["mt5_path"])
        if not os.path.isfile(mt5_exe):
            print(f"  ERROR: MT5 not found for {cfg['name']} at: {cfg['mt5_path']}")
            print(f"         (looked for: {mt5_exe})")
            all_paths_ok = False
        else:
            print(f"  MT5 verified for {cfg['name']}: {mt5_exe}")

    if not all_paths_ok:
        print("\n  Fix the mt5_path values in ACCOUNT_CONFIGS and try again.")
        sys.exit(1)

    print()

    processes = []
    for config in ACCOUNT_CONFIGS:
        p = multiprocessing.Process(
            target=run_bot_for_account,
            args=(config,),
            name=config["name"],
        )
        p.start()
        processes.append((config["name"], p))
        print(f"[{config['name']}] Process started (PID {p.pid})")
        time.sleep(2)  # Stagger starts to avoid race conditions

    print(f"\nAll {len(processes)} bot processes running. Press Ctrl+C to stop all.\n")

    try:
        while True:
            for name, p in processes:
                if not p.is_alive():
                    print(f"[{name}] Process died unexpectedly! Restarting...")
                    # Find the config and restart
                    cfg = next(c for c in ACCOUNT_CONFIGS if c["name"] == name)
                    new_p = multiprocessing.Process(
                        target=run_bot_for_account,
                        args=(cfg,),
                        name=name,
                    )
                    new_p.start()
                    processes = [(n, new_p if n == name else proc) for n, proc in processes]
                    print(f"[{name}] Restarted (new PID {new_p.pid})")
            time.sleep(10)  # Check every 10 seconds
    except KeyboardInterrupt:
        print("\nShutting down all bot processes...")
        for name, p in processes:
            p.terminate()
            p.join(timeout=10)
            print(f"[{name}] Stopped")
        print("All processes stopped.")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
