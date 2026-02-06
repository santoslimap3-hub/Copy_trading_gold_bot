@echo off
REM One-click launcher to view pnl.json on Windows
SET SCRIPT_DIR=%~dp0
REM Use the repository pnl.json by default
python "%SCRIPT_DIR%pnl_viewer.py" --file "%SCRIPT_DIR%pnl.json" --port 8765 --open
EXIT /B %ERRORLEVEL%
