$ErrorActionPreference = "Stop"
Write-Host "🚨 HARD RESET INITIATED 🚨" -ForegroundColor Magenta

# 1. Kill All Python
Write-Host "[1/3] Terminating all Python processes..." -ForegroundColor Cyan
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

# 2. Verify Kill
$survivors = Get-Process python -ErrorAction SilentlyContinue
if ($survivors) {
    Write-Host "CRITICAL: Failed to kill processes!" -ForegroundColor Red
    exit 1
}
Write-Host "    All processes terminated." -ForegroundColor Green

# 3. Start Runner from Specific Release
$ReleaseDir = "C:\Users\Administrator\ok\releases\20260215_194228"
$Runner = "$ReleaseDir\run_bot_vps.py"

if (-not (Test-Path $Runner)) {
    Write-Host "CRITICAL: Runner not found at $Runner" -ForegroundColor Red
    exit 1
}

Write-Host "[2/3] Starting Runner from: $ReleaseDir" -ForegroundColor Cyan
# Start in background with redirection to debug immediately if it fails
$OutLog = "$ReleaseDir\manual_restart.log"
Start-Process python -ArgumentList "-u", "`"$Runner`"" -WorkingDirectory $ReleaseDir -WindowStyle Hidden -RedirectStandardOutput $OutLog -RedirectStandardError $OutLog

Write-Host "[3/3] Runner launched. Waiting 5s for stability..." -ForegroundColor Cyan
Start-Sleep -Seconds 5

# 4. Check Process
$newProcs = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' }
if ($newProcs) {
    Write-Host "✅ SUCCESS. Active Processes:" -ForegroundColor Green
    foreach ($p in $newProcs) {
        Write-Host "    PID: $($p.ProcessId) | Cmd: $($p.CommandLine)" -ForegroundColor Gray
    }
} else {
    Write-Host "❌ FATAL: Process died immediately! Checking logs..." -ForegroundColor Red
    if (Test-Path $OutLog) {
        Get-Content $OutLog -Tail 20 | Write-Host -ForegroundColor Yellow
    }
}
