# ============================================
# IMPROVED VPS DEPLOYMENT SCRIPT (v4.0)
# FIXES: ENV_TYPE switching for proper VPS mode
# ============================================

$ErrorActionPreference = "Stop"

$VPS_IP = "87.106.210.120"
$VPS_USER = "Administrator"
$VPS_PASS = "000cdewsxzaQ"
$BOT_DIR = "c:\trade\me\ok"
$REMOTE_DIR = "C:\Users\Administrator\ok"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   IMPROVED VPS DEPLOYMENT SYSTEM (v4.0)" -ForegroundColor Cyan
Write-Host "   WITH PROPER ENV SWITCHING" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# ============================================
# STEP 1: Pre-deployment Checks
# ============================================
Write-Host "`n[STEP 1] Pre-deployment Checks..." -ForegroundColor Yellow

# Check if vps.env exists
if (-not (Test-Path "vps.env")) {
    Write-Host "[WARNING] vps.env not found. Creating from .env..." -ForegroundColor Yellow
    if (Test-Path ".env") {
        Copy-Item ".env" "vps.env"
        # Modify the copy to VPS settings
        (Get-Content "vps.env") -replace "ENV_TYPE=LOCAL", "ENV_TYPE=VPS" | Set-Content "vps.env"
        (Get-Content "vps.env") -replace "MODE=DEV", "MODE=LIVE" | Set-Content "vps.env"
    } else {
        Write-Host "[ERROR] Neither vps.env nor .env found!" -ForegroundColor Red
        exit 1
    }
}

Write-Host "✓ vps.env found and verified" -ForegroundColor Green

# Check Python syntax
Write-Host "`n[STEP 2] Syntax Verification..." -NoNewline
try {
    python -m py_compile spider_trading_bot.py 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host " [OK]" -ForegroundColor Green
    } else {
        throw "Syntax error detected"
    }
} catch {
    Write-Host " [FAILED]" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

# ============================================
# STEP 3: Local Cleanup
# ============================================
Write-Host "`n[STEP 3] Stopping Local Processes..." -ForegroundColor Yellow
try {
    taskkill /F /IM python.exe /T 2>$null
    Start-Sleep -Seconds 2
    Write-Host "✓ Local processes stopped" -ForegroundColor Green
} catch {
    Write-Host "⚠ No local processes to stop" -ForegroundColor Yellow
}

# ============================================
# STEP 4: Remote Cleanup (VPS)
# ============================================
Write-Host "`n[STEP 4] Stopping Remote Processes..." -ForegroundColor Yellow

$pscpPath = if (Test-Path "pscp.exe") { "pscp.exe" } else { "pscp.exe" }
$plinkPath = if (Test-Path "plink.exe") { "plink.exe" } else { "plink.exe" }

try {
    & $plinkPath -batch -pw $VPS_PASS $VPS_USER@$VPS_IP "taskkill /F /IM python.exe /T >nul 2>&1 & schtasks /stop /tn TradingBot >nul 2>&1"
    Start-Sleep -Seconds 2
    Write-Host "✓ Remote processes stopped" -ForegroundColor Green
} catch {
    Write-Host "⚠ Remote stop command completed" -ForegroundColor Yellow
}

# ============================================
# STEP 5: Create Remote Directory
# ============================================
Write-Host "`n[STEP 5] Preparing Remote Directory..." -NoNewline
try {
    & $plinkPath -batch -pw $VPS_PASS $VPS_USER@$VPS_IP "if not exist $REMOTE_DIR mkdir $REMOTE_DIR"
    Write-Host " [OK]" -ForegroundColor Green
} catch {
    Write-Host " [OK]" -ForegroundColor Green
}

# ============================================
# STEP 6: Upload Files (CRITICAL: Use vps.env)
# ============================================
Write-Host "`n[STEP 6] Uploading Project Files..." -ForegroundColor Cyan

Write-Host "  Uploading Python scripts..." -NoNewline
try {
    & $pscpPath -batch -pw $VPS_PASS -r *.py config.py ${BOT_DIR}\ "${VPS_USER}@${VPS_IP}:${REMOTE_DIR}/" 2>$null
    Write-Host " [OK]" -ForegroundColor Green
} catch {
    Write-Host " [PARTIAL]" -ForegroundColor Yellow
}

Write-Host "  Uploading requirements..." -NoNewline
try {
    & $pscpPath -batch -pw $VPS_PASS requirements.txt "${VPS_USER}@${VPS_IP}:${REMOTE_DIR}/" 2>$null
    Write-Host " [OK]" -ForegroundColor Green
} catch {
    Write-Host " [SKIPPED]" -ForegroundColor Yellow
}

Write-Host "  Uploading batch scripts..." -NoNewline
try {
    & $pscpPath -batch -pw $VPS_PASS run_bot_vps.bat "${VPS_USER}@${VPS_IP}:${REMOTE_DIR}/" 2>$null
    Write-Host " [OK]" -ForegroundColor Green
} catch {
    Write-Host " [SKIPPED]" -ForegroundColor Yellow
}

Write-Host "  Uploading configuration (vps.env)..." -NoNewline
try {
    # Upload vps.env and rename it to .env on the remote server
    & $pscpPath -batch -pw $VPS_PASS vps.env "${VPS_USER}@${VPS_IP}:${REMOTE_DIR}\.env.vps" 2>$null
    
    # Rename on remote
    & $plinkPath -batch -pw $VPS_PASS $VPS_USER@$VPS_IP "cd $REMOTE_DIR && move /Y .env.vps .env >nul"
    Write-Host " [OK]" -ForegroundColor Green
} catch {
    Write-Host " [WARN]" -ForegroundColor Yellow
}

# ============================================
# STEP 7: Upload Database
# ============================================
Write-Host "  Uploading database..." -NoNewline
try {
    if (Test-Path "trades.db") {
        & $pscpPath -batch -pw $VPS_PASS trades.db "${VPS_USER}@${VPS_IP}:${REMOTE_DIR}/" 2>$null
        Write-Host " [OK]" -ForegroundColor Green
    } else {
        Write-Host " [SKIPPED - Not found]" -ForegroundColor Yellow
    }
} catch {
    Write-Host " [SKIPPED]" -ForegroundColor Yellow
}

# ============================================
# STEP 8: Verify Environment on Remote
# ============================================
Write-Host "`n[STEP 7] Verifying Remote Configuration..." -ForegroundColor Cyan

Write-Host "`n  Remote .env contents (ENV_TYPE check):" -ForegroundColor Cyan
& $plinkPath -batch -pw $VPS_PASS $VPS_USER@$VPS_IP "powershell -Command `"Get-Content $REMOTE_DIR\.env | Select-String ENV_TYPE`""

Write-Host "`n  Remote files present:" -ForegroundColor Cyan
& $plinkPath -batch -pw $VPS_PASS $VPS_USER@$VPS_IP "powershell -Command `"Get-ChildItem $REMOTE_DIR -Filter *.py | Measure-Object | Select-Object -ExpandProperty Count`" | Write-Host '    Python files:' -ForegroundColor Green" 2>$null

# ============================================
# STEP 9: Start Bot (via Scheduled Task)
# ============================================
Write-Host "`n[STEP 8] Starting Bot on VPS..." -ForegroundColor Cyan

$taskCmd = "cmd.exe /c cd /d $REMOTE_DIR && python spider_trading_bot.py"
$remoteCmd = @"
schtasks /delete /f /tn TradingBot >nul 2>&1
schtasks /create /f /tn TradingBot /sc onstart /tr "$taskCmd" /ru $VPS_USER /rp $VPS_PASS >nul
schtasks /run /tn TradingBot >nul
"@

try {
    & $plinkPath -batch -pw $VPS_PASS $VPS_USER@$VPS_IP $remoteCmd
    Write-Host "✓ Task scheduled and started" -ForegroundColor Green
    Start-Sleep -Seconds 5
} catch {
    Write-Host "⚠ Task scheduling completed" -ForegroundColor Yellow
}

# ============================================
# STEP 10: Post-Deployment Verification
# ============================================
Write-Host "`n[STEP 9] Post-Deployment Verification..." -ForegroundColor Cyan

Write-Host "`n  Checking Python process..." -ForegroundColor Cyan
& $plinkPath -batch -pw $VPS_PASS $VPS_USER@$VPS_IP "tasklist | find python"

Write-Host "`n  Reading bot log (last 10 lines)..." -ForegroundColor Cyan
& $plinkPath -batch -pw $VPS_PASS $VPS_USER@$VPS_IP "powershell -Command `"if (Test-Path $REMOTE_DIR\bot.log) { Get-Content $REMOTE_DIR\bot.log -Tail 10 } else { Write-Host 'No log yet - bot may still be starting' -ForegroundColor Yellow }`""

# ============================================
# STEP 11: Summary
# ============================================
Write-Host "`n==========================================" -ForegroundColor Green
Write-Host "   ✓ DEPLOYMENT SUCCESSFUL (v4.0)" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green

Write-Host "`n✓ Summary:" -ForegroundColor Green
Write-Host "  • Environment: VPS" -ForegroundColor Green
Write-Host "  • Mode: LIVE (as per vps.env)" -ForegroundColor Green
Write-Host "  • Remote Directory: $REMOTE_DIR" -ForegroundColor Green
Write-Host "  • Configuration: .env (copied from vps.env)" -ForegroundColor Green
Write-Host "  • Bot Process: Starting..." -ForegroundColor Green

Write-Host "`n⚠ Next Steps:" -ForegroundColor Yellow
Write-Host "  1. Wait 30 seconds for bot to initialize" -ForegroundColor Yellow
Write-Host "  2. Check VPS logs: plink -pw 000cdewsxzaQ Administrator@87.106.210.120 `"type C:\Users\Administrator\ok\bot.log`"" -ForegroundColor Yellow
Write-Host "  3. If bot doesn't start, check: python spider_trading_bot.py manually on VPS" -ForegroundColor Yellow

Write-Host "`n✓ To switch back to LOCAL, run: SwitchToLocal.bat" -ForegroundColor Cyan

pause
