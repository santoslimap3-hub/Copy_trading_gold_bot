#!/usr/bin/env python3
"""
Quick Start - Copy Trading Gold Bot

Run this script to verify everything is set up correctly!
"""

import sys
from pathlib import Path

def check_requirements():
    """Check if all required packages are installed"""
    print("\n" + "="*60)
    print("🔍 CHECKING REQUIREMENTS")
    print("="*60 + "\n")
    
    packages = {
        "telethon": "Telegram client",
        "MetaTrader5": "Trading platform API",
        "rich": "Beautiful terminal UI",
    }
    
    failed = []
    
    for package, description in packages.items():
        try:
            __import__(package)
            print(f"✅ {package:20} - {description}")
        except ImportError:
            print(f"❌ {package:20} - {description} [MISSING]")
            failed.append(package)
    
    return failed

def check_files():
    """Check if all required files exist"""
    print("\n" + "="*60)
    print("📁 CHECKING FILES")
    print("="*60 + "\n")
    
    required_files = {
        "script.py": "Main trading bot",
        "find_channel.py": "Telegram channel scanner",
        "requirements.txt": "Python dependencies",
        "README.md": "Documentation",
    }
    
    failed = []
    base_path = Path(__file__).parent
    
    for filename, description in required_files.items():
        filepath = base_path / filename
        if filepath.exists():
            size = filepath.stat().st_size / 1024  # KB
            print(f"✅ {filename:20} - {description} ({size:.1f} KB)")
        else:
            print(f"❌ {filename:20} - {description} [MISSING]")
            failed.append(filename)
    
    return failed

def check_config():
    """Check if script.py has valid configuration"""
    print("\n" + "="*60)
    print("⚙️  CHECKING CONFIGURATION")
    print("="*60 + "\n")
    
    try:
        # Read script.py and check for API credentials
        script_path = Path(__file__).parent / "script.py"
        with open(script_path, 'r') as f:
            content = f.read()
        
        checks = {
            "api_id": "Telegram API ID configured",
            "api_hash": "Telegram API Hash configured",
            "CHANNEL": "Telegram Channel ID configured",
            "SYMBOL": "Trading Symbol configured",
            "RISK_PCT": "Risk Percentage configured",
        }
        
        failed = []
        for check, description in checks.items():
            if check in content:
                if "YOUR_" not in content.split(check)[1][:50]:  # Simple check
                    print(f"✅ {check:15} - {description}")
                else:
                    print(f"⚠️  {check:15} - {description} [NOT CONFIGURED]")
                    failed.append(check)
            else:
                print(f"❌ {check:15} - {description} [MISSING]")
                failed.append(check)
        
        return failed
    except Exception as e:
        print(f"❌ Error reading configuration: {e}")
        return ["config"]

def print_summary(missing_packages, missing_files, missing_config):
    """Print summary and instructions"""
    print("\n" + "="*60)
    print("📊 SUMMARY")
    print("="*60 + "\n")
    
    all_ok = not (missing_packages or missing_files or missing_config)
    
    if all_ok:
        print("✅ All checks passed! Your bot is ready to run.\n")
        print("🚀 NEXT STEPS:")
        print("   1. Edit script.py to configure your API credentials")
        print("   2. Run: python find_channel.py (to find your Telegram channel)")
        print("   3. Update the CHANNEL variable in script.py")
        print("   4. Configure MT5 settings (SYMBOL, RISK_PCT, etc.)")
        print("   5. Enable AutoTrading in MetaTrader 5")
        print("   6. Run: python script.py\n")
        return 0
    else:
        print("⚠️  Some issues found:\n")
        
        if missing_packages:
            print("📦 Missing packages:")
            for pkg in missing_packages:
                print(f"   - {pkg}")
            print("\n   Fix: pip install -r requirements.txt\n")
        
        if missing_files:
            print("📁 Missing files:")
            for f in missing_files:
                print(f"   - {f}")
            print()
        
        if missing_config:
            print("⚙️  Configuration issues:")
            for cfg in missing_config:
                print(f"   - {cfg}")
            print("\n   Edit script.py and update the configuration\n")
        
        return 1

def print_features():
    """Print bot features"""
    print("\n" + "="*60)
    print("✨ BOT FEATURES")
    print("="*60 + "\n")
    
    features = [
        ("📡", "Real-time Telegram signal monitoring"),
        ("💰", "Intelligent risk-based position sizing"),
        ("🛡️", "Hard monetary and percentage loss caps"),
        ("🔄", "Auto-reconnect on disconnect"),
        ("📊", "Beautiful colored terminal output"),
        ("⚡", "Smart retry logic for order rejections"),
        ("🔍", "Position tracking and management"),
        ("📈", "Real-time market snapshots"),
        ("🎯", "Support for BUY and SELL signals"),
        ("✅", "Comprehensive safety checks"),
    ]
    
    for emoji, feature in features:
        print(f"  {emoji} {feature}")
    print()

def main():
    print("\n" + "╔" + "="*58 + "╗")
    print("║" + " "*58 + "║")
    print("║" + "  🤖 COPY TRADING GOLD BOT - QUICK START".center(58) + "║")
    print("║" + " "*58 + "║")
    print("╚" + "="*58 + "╝")
    
    # Run checks
    missing_packages = check_requirements()
    missing_files = check_files()
    missing_config = check_config()
    
    # Print features
    print_features()
    
    # Print summary and return exit code
    return print_summary(missing_packages, missing_files, missing_config)

if __name__ == "__main__":
    sys.exit(main())
