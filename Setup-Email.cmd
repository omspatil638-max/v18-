@echo off
rem Connects ContractLens to your email (Gmail: use an App Password). Run once, then restart ContractLens.
cd /d "%~dp0backend"
".venv\Scripts\python.exe" scripts\setup_email.py
echo.
pause
