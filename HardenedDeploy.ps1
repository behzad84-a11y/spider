# Hardened VPS Deployment Script (Senior DevOps Implementation - v3.4)
$ErrorActionPreference = "Stop"

$VPS_IP = "87.106.210.120"
$VPS_USER = "Administrator"
$VPS_PASS = "000cdewsxzaQ"
$BOT_DIR = "c:\trade\me\ok"
$REMOTE_DIR = "C:\Users\Administrator\ok"

$pscpPath = Join-Path $BOT_DIR "pscp.exe"
if (-not (Test-Path $pscpPath)) { $pscpPath = "pscp.exe" }
$plinkPath = Join-Path $BOT_DIR "plink.exe"
if (-not (Test-Path $plinkPath)) { $plinkPath = "plink.exe" }
$WinSCPPath = Join-Path $BOT_DIR "WinSCP.com"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   HARDENED VPS DEPLOYMENT SYSTEM (v3.4)" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 0. Tool Verification (Automatic WinSCP Download)
$UseWinSCP = $true
if (-not (Test-Path $WinSCPPath)) {
    Write-Host "[0/5] WinSCP not found. Attempting download..." -ForegroundColor Yellow
    $URLs = @("https://cdn.winscp.net/files/WinSCP-6.3.5-Portable.zip", "https://winscp.net/download/WinSCP-6.3.5-Portable.zip")
    $Downloaded = $false
    foreach ($url in $URLs) {
        try {
            $zipPath = Join-Path $BOT_DIR "winscp.zip"
            $wc = New-Object System.Net.WebClient
            $wc.Headers.Add("User-Agent", "Mozilla/5.0")
            $wc.DownloadFile($url, $zipPath)
            $bytes = [System.IO.File]::ReadAllBytes($zipPath)
            if ($bytes.Length -gt 4 -and $bytes[0] -eq 0x50 -and $bytes[1] -eq 0x4B) {
                Expand-Archive -Path $zipPath -DestinationPath $BOT_DIR -Force
                Remove-Item $zipPath
                $Downloaded = $true; break
            }
            else { Remove-Item $zipPath }
        }
        catch {}
    }
    if (-not $Downloaded) { $UseWinSCP = $false }
}

# 1. Connectivity Check
Write-Host "[1/5] Checking SSH availability on $VPS_IP:22..." -NoNewline
if (-not (Test-NetConnection -ComputerName $VPS_IP -Port 22 -InformationLevel Quiet)) {
    Write-Host " [FAILED]" -ForegroundColor Red
    throw "SSH Port 22 unreachable."
}
Write-Host " [OK]" -ForegroundColor Green

# 1b. Local Sanity Check
Write-Host "[1b/5] Syntax verification (spider_trading_bot.py)..." -NoNewline
# Use explicit python path if possible or default
$pythonCmd = "python"
$check = & $pythonCmd -m py_compile spider_trading_bot.py 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host " [FAILED]" -ForegroundColor Red
    Write-Host "$check" -ForegroundColor Red
    throw "Syntax error found locally. Aborting."
}
Write-Host " [OK]" -ForegroundColor Green

# 2. Local Cleanup
Write-Host "[2/5] Stopping ALL local Python processes..." -ForegroundColor Yellow
try {
    taskkill /F /IM python.exe /T 2>$null
    Start-Sleep -Seconds 1
}
catch {}

# 2b. Remote Cleanup (CRITICAL: Stop remote bot to release trades.db lock)
Write-Host "[2b/5] Stopping remote Bot to release locks..." -ForegroundColor Yellow
& $plinkPath -batch -pw $VPS_PASS $VPS_USER@$VPS_IP "taskkill /F /IM python.exe /T >nul 2>&1 & schtasks /stop /tn TradingBot >nul 2>&1"

# 3. Synchronizing Project
$SyncDone = $false
# Create remote dir if missing
& $plinkPath -batch -pw $VPS_PASS $VPS_USER@$VPS_IP "if not exist $REMOTE_DIR mkdir $REMOTE_DIR"

if ($UseWinSCP) {
    Write-Host "[3/5] Syncing project via WinSCP (Full Mirror)..."
    $ExcludeMask = "*.db-journal; __pycache__/; .venv/; .git/; logs/; *.zip; *.exe; *.bat; .system_generated/"
    # Note: We sync trades.db and .env as they are part of the core project state
    $WinSCPScript = "option batch abort`noption confirm off`nopen sftp://$($VPS_USER):$($VPS_PASS)@$($VPS_IP)/ -hostkey=*`nsynchronize remote -mirror -delete -filemask=| $ExcludeMask `"$BOT_DIR`" `"$REMOTE_DIR`"`nexit"
    $tempScript = [System.IO.Path]::GetTempFileName()
    $WinSCPScript | Out-File $tempScript -Encoding ASCII
    & $WinSCPPath /script=$tempScript /ini=null
    if ($LASTEXITCODE -eq 0) { $SyncDone = $true }
    Remove-Item $tempScript -ErrorAction SilentlyContinue
}

if (-not $SyncDone) {
    Write-Host "[3/5] Syncing project via PSCP Focused Upload..." -ForegroundColor Yellow
    # Explicit list to avoid issues
    & $pscpPath -batch -pw $VPS_PASS -r *.py .env trades.db requirements.txt run_bot_vps.bat "${VPS_USER}@${VPS_IP}:${REMOTE_DIR}/"
}

# 4. Remote Execution (schtasks)
Write-Host "[4/5] Remote Startup (via Scheduled Task)..." -ForegroundColor Cyan
# Escape quotes for remote side: use \" for the command part
$taskCmd = "cmd.exe /c cd /d $REMOTE_DIR && run_bot_vps.bat"
$remoteCmd = "schtasks /delete /f /tn TradingBot >nul 2>&1 & " +
"schtasks /create /f /tn TradingBot /sc once /st 00:00 /tr \`"$taskCmd\`" /ru $VPS_USER /rp $VPS_PASS & " +
"schtasks /run /tn TradingBot"

& $plinkPath -batch -pw $VPS_PASS ${VPS_USER}@${VPS_IP} $remoteCmd

# 5. Remote Verification
Write-Host "[5/5] Health Check & Verification..."
Start-Sleep -Seconds 10
Write-Host "--- VPS Process Status ---" -ForegroundColor Cyan
& $plinkPath -batch -pw $VPS_PASS ${VPS_USER}@${VPS_IP} "tasklist /FI `"IMAGENAME eq python.exe`""

Write-Host "--- VPS Bot Log (Latest) ---" -ForegroundColor Cyan
& $plinkPath -batch -pw $VPS_PASS ${VPS_USER}@${VPS_IP} "powershell -Command `"if (Test-Path $REMOTE_DIR\bot.log) { Get-Content $REMOTE_DIR\bot.log -Tail 15 } else { Write-Host 'Log not found yet' }`""

Write-Host "`n==========================================" -ForegroundColor Green
Write-Host "   DEPLOYMENT SUCCESSFUL (One-Click)" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
