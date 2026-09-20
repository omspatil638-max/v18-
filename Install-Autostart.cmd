@echo off
rem Starts ContractLens quietly at Windows sign-in so alerts keep arriving. Undo: Install-Autostart.ps1 -Remove
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Autostart.ps1"
echo.
pause
