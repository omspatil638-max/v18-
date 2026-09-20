@echo off
rem Creates the keys browser notifications need. Start-ContractLens.cmd also does this automatically.
cd /d "%~dp0backend"
".venv\Scripts\python.exe" scripts\setup_notifications.py
echo.
pause
