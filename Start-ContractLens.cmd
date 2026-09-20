@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-contractlens.ps1" -DatabaseMode auto
if errorlevel 1 (
  echo.
  echo ContractLens could not be started. Review the error above.
  pause
)
