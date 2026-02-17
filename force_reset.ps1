Write-Host "🚨 STARTING RUNTIME RESET 🚨" -ForegroundColor Magenta

# 1. Force Kill All Python Processes
Write-Host "[1/3] Killing all Python processes..." -ForegroundColor Cyan
$procs = Get-Process python -ErrorAction SilentlyContinue
if ($procs) {
    foreach ($p in $procs) {
        Write-Host "    Killing PID $($p.Id) (StartTime: $($p.StartTime))" -ForegroundColor Yellow
        Stop-Process -Id $p.Id -Force
    }
}
else {
    Write-Host "    No running Python processes found." -ForegroundColor Green
}

# 2. Clear __pycache__
Write-Host "[2/3] Cleaning __pycache__ directories..." -ForegroundColor Cyan
Get-ChildItem -Path . -Include "__pycache__" -Recurse -Directory | ForEach-Object {
    Write-Host "    Removing: $($_.FullName)" -ForegroundColor DarkGray
    Remove-Item $_.FullName -Force -Recurse
}

# 3. Verify Execution Path
Write-Host "[3/3] Verifying working directory..." -ForegroundColor Cyan
$CurrentDir = Get-Location
Write-Host "    Current Directory: $CurrentDir" -ForegroundColor White
if (Test-Path "$CurrentDir\spider_trading_bot.py") {
    Write-Host "    ✅ spider_trading_bot.py found in current directory." -ForegroundColor Green
}
else {
    Write-Host "    ❌ spider_trading_bot.py NOT FOUND here!" -ForegroundColor Red
}

Write-Host "✅ RESET COMPLETE. You can now start the bot cleanly." -ForegroundColor Green
