<#
    Start ContractLens automatically when you sign in to Windows, so alerts and reminders keep
    arriving without you opening the app. It starts quietly (no browser window).

      .\Install-Autostart.ps1            install
      .\Install-Autostart.ps1 -Remove    undo
#>
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$Root     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Startup  = [Environment]::GetFolderPath("Startup")
$Shortcut = Join-Path $Startup "ContractLens.lnk"

if ($Remove) {
    if (Test-Path $Shortcut) { Remove-Item $Shortcut -Force; Write-Host "Autostart removed." } else { Write-Host "Autostart was not installed." }
    return
}

$shell = New-Object -ComObject WScript.Shell
$link  = $shell.CreateShortcut($Shortcut)
$link.TargetPath       = "powershell.exe"
$link.Arguments        = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$Root\start-contractlens.ps1`" -DatabaseMode auto -NoBrowser"
$link.WorkingDirectory = $Root
$link.WindowStyle      = 7
$link.Description      = "ContractLens: deadline alerts and reminders"
$link.Save()

Write-Host "Done. ContractLens will start quietly when you sign in to Windows."
Write-Host "Docker Desktop must also start at sign-in (Docker Desktop > Settings > General > Start Docker Desktop when you sign in)."
Write-Host "Undo any time with: .\Install-Autostart.ps1 -Remove"
