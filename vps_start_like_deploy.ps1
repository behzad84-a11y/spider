# Start bot exactly like deploy_vps.ps1: cmd + launcher with stdout/stderr redirect
$NewDir = (Get-ChildItem 'C:\Users\Administrator\ok\releases' -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
if (-not $NewDir) { Write-Host 'No release dir'; exit 1 }
$BotOut = Join-Path $NewDir 'spider.out.log'
$BotErr = Join-Path $NewDir 'spider.err.log'
$LauncherBat = Join-Path $NewDir 'start_bot_strict.bat'
if (-not (Test-Path $LauncherBat)) {
    @"
@echo off
echo [DEBUG] Current Directory: %CD%
echo [DEBUG] ENV_TYPE BEFORE: %ENV_TYPE%
set ENV_TYPE=VPS
echo [DEBUG] ENV_TYPE AFTER: %ENV_TYPE%
echo [DEBUG] Content of .env:
type .env
echo [DEBUG] Launching Python...
python -u run_bot_vps.py
"@ | Set-Content -Path $LauncherBat -Encoding ASCII
}
$proc = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $LauncherBat) -WorkingDirectory $NewDir `
    -RedirectStandardOutput $BotOut -RedirectStandardError $BotErr -PassThru -WindowStyle Hidden
Write-Host "Started PID: $($proc.Id) from $NewDir"
Write-Host "Output -> $BotOut"
Write-Host "Errors -> $BotErr"
