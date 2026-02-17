# =============================================================================
# SPIDER VPS ROLLBACK SCRIPT
# Restores from backup snapshots created during deployment
# =============================================================================
param(
    [Parameter(Mandatory = $false)][string]$BackupID,
    [Parameter(Mandatory = $false)][string]$RemoteDir = "C:\Users\Administrator\ok"
)
$ErrorActionPreference = "Stop"

$LogFile = Join-Path $RemoteDir "rollback.log"

function Log-Msg([string]$msg, [string]$color = "White") {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $out = "[$ts] $msg"
    Write-Host $out -ForegroundColor $color
    try { Add-Content -Path $LogFile -Value $out -ErrorAction SilentlyContinue } catch {}
}

Log-Msg "--- STARTING ROLLBACK ---" "Cyan"

# 1) Find backup
$BackupsDir = Join-Path $RemoteDir "backups"
if (-not (Test-Path $BackupsDir)) {
    Log-Msg "CRITICAL: No backups directory found!" "Red"
    exit 1
}

$Backup = $null
if ($BackupID) {
    $Backup = Join-Path $BackupsDir $BackupID
    if (-not (Test-Path $Backup)) {
        Log-Msg "CRITICAL: Backup $BackupID not found!" "Red"
        exit 1
    }
} else {
    # Use latest backup
    $Backup = Get-ChildItem -Path $BackupsDir -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $Backup) {
        Log-Msg "CRITICAL: No backups found!" "Red"
        exit 1
    }
    $Backup = $Backup.FullName
    Log-Msg "Auto-selected latest backup: $(Split-Path $Backup -Leaf)" "Yellow"
}

Log-Msg "Using backup: $Backup" "Gray"

# 2) Stop current bot
Log-Msg "Stopping current bot instances..." "Yellow"
$Zombies = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*spider_trading_bot.py*" -or $_.CommandLine -like "*run_bot_vps.py*" }
foreach ($z in $Zombies) {
    Log-Msg "Killing PID: $($z.ProcessId)" "Red"
    Stop-Process -Id $z.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

# 3) Restore release
$ReleasesDir = Join-Path $RemoteDir "releases"
$BackupRelease = Join-Path $Backup "release"
$BackupEnv = Join-Path $Backup ".env"

if (-not (Test-Path $BackupRelease)) {
    Log-Msg "CRITICAL: Backup release directory not found!" "Red"
    exit 1
}

$RestoreID = Get-Date -Format "yyyyMMdd_HHmmss"
$RestoreDir = Join-Path $ReleasesDir "rollback_$RestoreID"
New-Item -ItemType Directory -Path $RestoreDir -Force | Out-Null

Log-Msg "Restoring release to: $RestoreDir" "Cyan"
Copy-Item -Path $BackupRelease -Destination $RestoreDir -Recurse -Force

# 4) Restore .env
if (Test-Path $BackupEnv) {
    Log-Msg "Restoring .env file..." "Cyan"
    Copy-Item -Path $BackupEnv -Destination (Join-Path $RemoteDir ".env") -Force
    Copy-Item -Path $BackupEnv -Destination (Join-Path $RestoreDir ".env") -Force
} else {
    Log-Msg "WARNING: Backup .env not found, keeping current .env" "Yellow"
}

# 5) Start bot from restored release
Log-Msg "Starting bot from restored release..." "Cyan"
$BotOut = "$RestoreDir\spider.out.log"
$BotErr = "$RestoreDir\spider.err.log"
$BotScript = "$RestoreDir\run_bot_vps.py"

if (-not (Test-Path $BotScript)) {
    Log-Msg "CRITICAL: run_bot_vps.py missing in restored release!" "Red"
    exit 2
}

$LauncherBat = "$RestoreDir\start_bot_strict.bat"
@"
@echo off
set ENV_TYPE=VPS
python -u run_bot_vps.py
"@ | Set-Content -Path $LauncherBat -Encoding ASCII

try {
    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", $LauncherBat) -WorkingDirectory $RestoreDir `
        -RedirectStandardOutput $BotOut -RedirectStandardError $BotErr -PassThru -WindowStyle Hidden
    $RestorePID = $proc.Id
    Log-Msg "Restored Bot PID: $RestorePID" "Green"
} catch {
    Log-Msg "FAILED to start restored bot: $_" "Red"
    exit 3
}

# 6) Health check
$deadline = (Get-Date).AddSeconds(30)
$passed = $false
Log-Msg "Waiting for startup verification (max 30s)..." "Cyan"
while ((Get-Date) -lt $deadline) {
    $p = Get-Process -Id $RestorePID -ErrorAction SilentlyContinue
    if (-not $p) { break }
    
    $outTail = @()
    if (Test-Path $BotOut) { $outTail = Get-Content $BotOut -Tail 50 -ErrorAction SilentlyContinue }
    
    if ($outTail -match "TOKEN LOCK: VPS" -or $outTail -match "TOKEN SELECTED: LIVE") {
        $passed = $true
        break
    }
    Start-Sleep -Seconds 2
}

$alive = Get-Process -Id $RestorePID -ErrorAction SilentlyContinue
if (-not $alive) {
    Log-Msg "CRITICAL: Restored bot process died!" "Red"
    if (Test-Path $BotErr) { Get-Content $BotErr -Tail 30 | Write-Host }
    exit 4
}

if ($passed) {
    Log-Msg "Rollback SUCCESS! Bot running from: $RestoreDir" "Green"
    Log-Msg "Backup used: $(Split-Path $Backup -Leaf)" "Gray"
    exit 0
} else {
    Log-Msg "WARNING: Rollback completed but health check inconclusive." "Yellow"
    Log-Msg "Bot PID: $RestorePID - Check logs manually." "Yellow"
    exit 0
}
