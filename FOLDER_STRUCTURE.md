# 📁 Repository Organization Guide

## Project Structure

The repository has been reorganized into a clean, logical folder structure:

```
Copy_trading_gold_bot/
│
├── 📂 src/                          # Bot Source Code
│   ├── bot_v2.py                   # Main trading bot (production)
│   ├── pnl_logger.py                # P&L tracking and logging
│   ├── pnl_viewer.py                # P&L visualization
│   ├── trade_history_analyzer.py    # Trade analysis tools
│   ├── find_channel.py              # Telegram channel discovery
│   ├── generate_test_signal.py      # Test signal generator
│   ├── quickstart.py                # Quick start script
│   └── random_bot.py                # Random trading bot (testing)
│
├── 📂 docs/                         # Documentation & Guides
│   ├── README.md                    # Main project documentation
│   ├── START_HERE.md                # Quick start guide
│   ├── QUICKSTART.md                # Detailed quickstart
│   ├── TEST_MODE_GUIDE.md           # Test mode instructions
│   ├── STRATEGY_TP3_BREAKEVEN.md    # TP3 breakeven strategy
│   ├── TP3_BREAKEVEN_QUICKSTART.md  # Strategy quickstart
│   ├── TP3_HIT_SAFETY_EXIT.md       # TP3 safety exit feature
│   ├── SAFETY_CHECKLIST.md          # Safety guidelines
│   ├── BEAUTY_GUIDE.md              # Code styling guide
│   ├── COLOR_PALETTE.md             # Terminal color scheme
│   ├── CHANGES.md                   # Changelog
│   ├── FINAL_SUMMARY.md             # Project summary
│   ├── IMPLEMENTATION_SUMMARY.md    # Implementation notes
│   └── [More documentation...]
│
├── 📂 data/                         # Data Files
│   └── trade_history.json           # Historical trade data
│
├── 📂 sessions/                     # Telegram Session Files
│   ├── zinra_session.session        # Telegram session cache
│   ├── zinra_session_telethon.session
│   └── zinra_test_session_telethon.session
│
├── 📂 scripts/                      # Utility Scripts
│   └── view_pnl.bat                 # P&L viewer batch script
│
├── 📂 config/                       # Configuration Files
│   └── (Reserved for future configs)
│
├── 📂 tests/                        # Test Files
│   └── dry_run.py                   # Dry run testing
│
├── 📄 requirements.txt              # Python dependencies
├── 📄 README.md                     # Project overview (root level)
└── 📄 .git/                         # Git repository

```

## Folder Purposes

### `src/` - Source Code
All Python bot source code and utilities:
- **bot_v2.py** - Main production bot with all trading logic
- **pnl_logger.py** - Tracks profit/loss
- **pnl_viewer.py** - Views P&L statistics
- **trade_history_analyzer.py** - Analyzes past trades
- **Utility scripts** - Testing and setup tools

### `docs/` - Documentation
Complete documentation and guides:
- Setup and quick start guides
- Feature documentation
- Strategy guides
- Safety guidelines
- Implementation notes
- Styling and color palette guides

### `data/` - Data Files
Trading data and analytics:
- JSON files with trade history
- P&L records
- Analysis results

### `sessions/` - Telegram Sessions
Telegram client session files:
- Cached authentication sessions
- Allows bot to remain logged in
- Non-portable (machine-specific)

### `scripts/` - Batch Scripts
Utility batch files:
- P&L viewer launcher
- Other Windows automation scripts

### `config/` - Configuration
Reserved for future configuration files:
- API credentials (not checked in)
- Settings files
- Environment config

### `tests/` - Test Files
Testing and validation:
- Dry run tests
- Integration tests
- Manual testing scripts

## Key Files (Root Level)

- **README.md** - Main project documentation
- **requirements.txt** - All Python dependencies (install with `pip install -r requirements.txt`)
- **.gitignore** - Files excluded from git (credentials, sessions, etc.)

## Quick Navigation

### To Run the Bot
```bash
cd src
python bot_v2.py
```

### To Check P&L
```bash
cd src
python pnl_viewer.py
```

### To View Documentation
```bash
cat docs/START_HERE.md
```

### To Install Dependencies
```bash
pip install -r requirements.txt
```

## Benefits of This Organization

✅ **Clear Structure** - Easy to find what you need
✅ **Separated Concerns** - Code, docs, data, and config in separate folders
✅ **Git Friendly** - Sessions and data files in separate folders (can be gitignored)
✅ **Scalable** - Easy to add new modules and features
✅ **Professional** - Follows standard Python project practices
✅ **Maintenance** - Much easier to maintain and update

## Notes

- Session files in `sessions/` should be added to `.gitignore` (they're user-specific)
- Data files in `data/` can be gitignored to keep repo size small
- The `config/` folder is ready for sensitive configurations (API keys, secrets)
- All Python imports from the root should use relative imports from `src/`

## Future Enhancements

As the project grows, consider:
- Adding `logs/` folder for bot logs
- Adding `backtests/` for strategy backtesting
- Adding `utils/` for shared utility modules
- Adding `migrations/` for database schema changes
