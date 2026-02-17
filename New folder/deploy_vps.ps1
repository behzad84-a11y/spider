# =============================================================================
# SPIDER VPS DEPLOYMENT SCRIPT (HARDENED) v2.2
# Blue/Green Deployment with deterministic health-check + env merge
# =============================================================================
param(
    [Parameter(Mandatory = $false)][string]$ZipFile,
    [Parameter(Mandatory = $false)][string]$RemoteDir,
    [Parameter(Mandatory = $false)][string]$EnvFile
)
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrEmpty($RemoteDir)) { $RemoteDir = "C:\Users\Administrator\ok" }
# Env source: explicit -EnvFile (e.g. C:\Users\Administrator\ok\vps_generated.env) or fallback to same dir
$EnvSourcePath = if ($EnvFile) { $EnvFile } else { Join-Path $RemoteDir "vps_generated.env" }
$LogFile = Join-Path $RemoteDir "deploy.log"

function Log-Msg([string]$msg, [string]$color = "White") {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $out = "[$ts] $msg"
    Write-Host $out -ForegroundColor $color
    try { Add-Content -Path $LogFile -Value $out -ErrorAction SilentlyContinue } catch {}
}

Log-Msg "--- STARTING DEPLOYMENT ---" "Cyan"

# 1) Validate ZIP (auto-discover)
if ([string]::IsNullOrEmpty($ZipFile) -or -not (Test-Path $ZipFile)) {
    $Potential = Get-ChildItem -Path $RemoteDir -Filter "*.zip" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($Potential) {
        $ZipFile = $Potential.FullName
        Log-Msg "Auto-detected newest zip: $ZipFile" "Yellow"
    }
    else {
        Log-Msg "CRITICAL: No zip file found." "Red"
        exit 1
    }
}

# 2) BACKUP: Create snapshot before deploy
$BackupsDir = Join-Path $RemoteDir "backups"
$BackupID = Get-Date -Format "yyyyMMdd_HHmmss"
$BackupDir = Join-Path $BackupsDir $BackupID
New-Item -ItemType Directory -Path $BackupsDir -Force | Out-Null

# Backup current release if exists
$CurrentRelease = Get-ChildItem -Path (Join-Path $RemoteDir "releases") -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ErrorAction SilentlyContinue
if ($CurrentRelease) {
    Log-Msg "Creating backup snapshot: $BackupID" "Yellow"
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    Copy-Item -Path $CurrentRelease.FullName -Destination (Join-Path $BackupDir "release") -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item -Path (Join-Path $RemoteDir ".env") -Destination (Join-Path $BackupDir ".env") -Force -ErrorAction SilentlyContinue
    
    # Keep only last 3 backups
    $OldBackups = Get-ChildItem -Path $BackupsDir -Directory | Sort-Object LastWriteTime -Descending | Select-Object -Skip 3
    foreach ($old in $OldBackups) {
        Remove-Item -Path $old.FullName -Recurse -Force -ErrorAction SilentlyContinue
        Log-Msg "Removed old backup: $($old.Name)" "Gray"
    }
}

# 2b) Prepare dirs
$BuildID = Get-Date -Format "yyyyMMdd_HHmmss"
$ReleasesDir = Join-Path $RemoteDir "releases"
$NewDir = Join-Path $ReleasesDir $BuildID
$BaseEnv = Join-Path $RemoteDir ".env"

Log-Msg "Build ID: $BuildID" "Gray"
Log-Msg "Target: $NewDir" "Gray"
New-Item -ItemType Directory -Path $NewDir -Force | Out-Null
New-Item -ItemType Directory -Path $ReleasesDir -Force | Out-Null

# 3) Extract
Log-Msg "Extracting package..." "Cyan"
Expand-Archive -Path $ZipFile -DestinationPath $NewDir -Force

# 4) ENV: ALWAYS use vps_generated.env as source; never preserve old .env that may have ENV_TYPE=LOCAL
if (-not (Test-Path $EnvSourcePath)) {
    Log-Msg "CRITICAL: vps_generated.env not found at $EnvSourcePath. Cannot deploy." "Red"
    exit 1
}

$TargetEnv = Join-Path $NewDir ".env"
# Backup any existing .env in release dir, then overwrite with vps_generated.env
if (Test-Path $TargetEnv) {
    Copy-Item -Path $TargetEnv -Destination "$TargetEnv.bak" -Force -ErrorAction SilentlyContinue
    Log-Msg "Backed up existing release .env to .env.bak" "Gray"
}
Log-Msg "Applying ENV to GREEN: $TargetEnv (source: $EnvSourcePath)" "Cyan"
Copy-Item -Path $EnvSourcePath -Destination $TargetEnv -Force -ErrorAction Stop
# Enforce ENV_TYPE=VPS in release .env
$content = Get-Content $TargetEnv -Raw
if ($content -notmatch "ENV_TYPE=VPS") {
    Add-Content -Path $TargetEnv -Value "`nENV_TYPE=VPS"
    Log-Msg "Enforced ENV_TYPE=VPS in release .env" "Yellow"
}
# Update base .env from same source so future runs use correct env
Copy-Item -Path $EnvSourcePath -Destination $BaseEnv -Force -ErrorAction SilentlyContinue

if (-not (Test-Path $TargetEnv)) {
    Log-Msg "FATAL: .env missing in release after copy!" "Red"
    exit 1
}


# 5) PRE-START: ZOMBIE KILLER (STRICT)
Log-Msg "Executing Zombie Killer Protocol..." "Yellow"
$Zombies = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*spider_trading_bot.py*" }
foreach ($z in $Zombies) {
    Log-Msg "Killing Zombie PID: $($z.ProcessId)" "Red"
    Stop-Process -Id $z.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

# 5.5) Dep Install (Critical for updates)
Log-Msg "Installing/Updating Dependencies..." "Cyan"
Start-Process -FilePath "python" -ArgumentList "-m pip install -r requirements.txt --no-cache-dir --upgrade" -WorkingDirectory $NewDir -NoNewWindow -Wait


# 6) Start GREEN
Log-Msg "Starting GREEN instance..." "Cyan"
$BotOut = "$NewDir\spider.out.log"
$BotErr = "$NewDir\spider.err.log"
# Use the robust runner script instead of the bot directly (Wait, user said verify runner loop? 
# User said "Deterministic restart: kill old python spider_trading_bot instances then start exactly ONE instance."
# If we run run_bot_vps.py, it manages the loop. If we kill it, we kill the loop.
# Let's run run_bot_vps.py which runs spider.
$BotScript = "$NewDir\run_bot_vps.py"

if (-not (Test-Path $BotScript)) {
    Log-Msg "CRITICAL: run_bot_vps.py missing in release package!" "Red"
    exit 2
}

# Create Strict Launcher Batch
$LauncherBat = "$NewDir\start_bot_strict.bat"
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

try {
    # SEPARATE STDOUT/STDERR
    # Execute the BATCH file instead of python direct
    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", $LauncherBat) -WorkingDirectory $NewDir `
        -RedirectStandardOutput $BotOut -RedirectStandardError $BotErr -PassThru -WindowStyle Hidden
    $GreenPID = $proc.Id
    Log-Msg "Green PID: $GreenPID" "Green"
}
catch {
    Log-Msg "FAILED to start process: $_" "Red"
    exit 3
}

# 6) Health-check: find "Started" marker, then stability window 15s — fail if 409/Conflict/connection errors appear
$deadline = (Get-Date).AddSeconds(60)
$pollingMarkers = @("Bot starting polling", "Bot is polling", "Application started successfully", "Application started", "run_polling", "Instance lock acquired")
$passed = $false
$foundMarker = ""

# Patterns that indicate zombie / dead connection (process up but not receiving updates)
$connectionFailurePatterns = @(
    "409 Conflict",
    "Conflict:",
    "terminated by other getUpdates",
    "getUpdates request",
    "telegram\.error\.Conflict",
    "Conflict \(",
    "AttributeError.*job_queue",
    "Traceback",
    "ConnectionError",
    "NetworkError",
    "Connection refused"
)

function Test-LogForConnectionFailure {
    param([string]$outStr, [string]$errStr)
    foreach ($pat in $connectionFailurePatterns) {
        if ($outStr -match $pat -or $errStr -match $pat) { return $true }
    }
    return $false
}

Log-Msg "Waiting for startup verification (max 60s)..." "Cyan"
while ((Get-Date) -lt $deadline) {
    $p = Get-Process -Id $GreenPID -ErrorAction SilentlyContinue
    if (-not $p) {
        Log-Msg "Process $GreenPID is not running. Checking logs..." "Yellow"
        break
    }

    $outTail = @()
    if (Test-Path $BotOut) { $outTail = Get-Content $BotOut -Tail 300 -ErrorAction SilentlyContinue }
    $errTail = @()
    if (Test-Path $BotErr) { $errTail = Get-Content $BotErr -Tail 150 -ErrorAction SilentlyContinue }
    $outStr = $outTail -join "`n"
    $errStr = $errTail -join "`n"

    # Fail: TOKEN LOCK or wrong env
    if ($outStr -match "TOKEN LOCK FAILED|SECURITY TOKEN LOCK" -or $errStr -match "TOKEN LOCK FAILED|SECURITY TOKEN LOCK") {
        Log-Msg "CRITICAL: TOKEN LOCK FAILED in logs. Health check FAILED." "Red"
        $passed = $false
        break
    }
    if ($outStr -match "TOKEN LOCK: LOCAL" -or $errStr -match "TOKEN LOCK: LOCAL") {
        Log-Msg "CRITICAL: Bot started in LOCAL mode on VPS! Aborting." "Red"
        $passed = $false
        break
    }
    # Fail: connection/409 errors already in logs before we see "Started"
    if (Test-LogForConnectionFailure $outStr $errStr) {
        Log-Msg "CRITICAL: Connection/409 error in logs. Health check FAILED." "Red"
        $passed = $false
        break
    }

    # Look for polling/started marker
    $foundMarker = ""
    foreach ($m in $pollingMarkers) {
        if ($outStr -match [regex]::Escape($m) -or $errStr -match [regex]::Escape($m)) {
            $foundMarker = $m
            break
        }
    }

    if ($foundMarker) {
        # Found "Started" — now stability window: monitor 15s for connection failures (zombie detection)
        Log-Msg "Found marker: $foundMarker. Starting 15s stability window (check for 409/Conflict)..." "Cyan"
        $stabilitySeconds = 15
        $stabilityDeadline = (Get-Date).AddSeconds($stabilitySeconds)
        $connectionErrorSeen = $false
        while ((Get-Date) -lt $stabilityDeadline) {
            $p2 = Get-Process -Id $GreenPID -ErrorAction SilentlyContinue
            if (-not $p2) {
                Log-Msg "CRITICAL: Process died during stability window. FAILED." "Red"
                $passed = $false
                $connectionErrorSeen = $true
                break
            }
            $outTail2 = @()
            if (Test-Path $BotOut) { $outTail2 = Get-Content $BotOut -Tail 200 -ErrorAction SilentlyContinue }
            $errTail2 = @()
            if (Test-Path $BotErr) { $errTail2 = Get-Content $BotErr -Tail 150 -ErrorAction SilentlyContinue }
            $outStr2 = $outTail2 -join "`n"
            $errStr2 = $errTail2 -join "`n"
            if (Test-LogForConnectionFailure $outStr2 $errStr2) {
                Log-Msg "CRITICAL: 409/Conflict or connection error detected during stability window. Bot is zombie. FAILED." "Red"
                $passed = $false
                $connectionErrorSeen = $true
                if (Test-Path $BotErr) { Get-Content $BotErr -Tail 40 | Write-Host }
                break
            }
            Start-Sleep -Seconds 2
        }
        if (-not $connectionErrorSeen) {
            $passed = $true
        }
        break
    }

    Start-Sleep -Seconds 2
}

$alive = Get-Process -Id $GreenPID -ErrorAction SilentlyContinue
if (-not $alive) {
    Log-Msg "CRITICAL: Green process died during startup (or exit code 12)." "Red"
    if (Test-Path $BotErr) { Get-Content $BotErr -Tail 60 | Write-Host }
    if (Test-Path $BotOut) { Get-Content $BotOut -Tail 60 | Write-Host }
    exit 4
}

if ($passed) {
    Log-Msg "Health Check PASSED (marker: $foundMarker, process $GreenPID alive, no 409 in 15s window)." "Green"
    
    # 7) Kill BLUE / old instances (avoid Telegram 409) - ONLY after confirmed GREEN is stable
    Log-Msg "Stopping old instances..." "Cyan"
    $OldProcs = Get-CimInstance Win32_Process | Where-Object {
        ($_.CommandLine -like "*spider_trading_bot.py*" -or $_.CommandLine -like "*run_bot_vps.py*") -and $_.ProcessId -ne $GreenPID
    }
    foreach ($p in $OldProcs) {
        Log-Msg "Terminating old PID: $($p.ProcessId)" "Yellow"
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
    }

    # 7b) Stop GREEN process (started by us for health-check only; survives only until plink exits)
    Log-Msg "Stopping health-check process (GREEN) so Task Scheduler can own the bot..." "Cyan"
    try { Stop-Process -Id $GreenPID -Force -ErrorAction SilentlyContinue } catch {}

    # 7c) Create/update SpiderBot scheduled task to run from NEW release (detached from SSH)
    $LauncherBat = Join-Path $NewDir "start_fresh.bat"
    if (-not (Test-Path $LauncherBat)) {
        @"
@echo off
cd /d "$NewDir"
set ENV_TYPE=VPS
python -u run_bot_vps.py >> spider_fresh.out.log 2>> spider_fresh.err.log
"@ | Set-Content -Path $LauncherBat -Encoding ASCII
        Log-Msg "Created $LauncherBat" "Gray"
    }
    schtasks /create /tn "SpiderBot" /tr "`"$LauncherBat`"" /sc once /st 00:00 /f | Out-Null
    Log-Msg "Scheduled task SpiderBot updated to: $NewDir" "Green"

    # 7d) Run SpiderBot so bot runs detached from plink session
    schtasks /run /tn "SpiderBot"
    Log-Msg "SpiderBot task started (bot running detached from SSH)." "Green"
}
else {
    Log-Msg "CRITICAL: Health verification FAILED. Rolling back..." "Red"
    Log-Msg "--- START SPIDER.OUT.LOG TAIL ---" "Cyan"
    if (Test-Path $BotOut) { Get-Content $BotOut -Tail 100 | Write-Host }
    Log-Msg "--- END SPIDER.OUT.LOG TAIL ---" "Cyan"
    
    Log-Msg "--- START SPIDER.ERR.LOG TAIL ---" "Red"
    if (Test-Path $BotErr) { Get-Content $BotErr -Tail 100 | Write-Host }
    Log-Msg "--- END SPIDER.ERR.LOG TAIL ---" "Red"

    Log-Msg "Terminating failed Green instance ($GreenPID)..." "Red"
    try { Stop-Process -Id $GreenPID -Force -ErrorAction SilentlyContinue } catch {}
    
    Log-Msg "Blue instance preserved. Deployment aborted." "Red"
    exit 5
}

# 8) Finalize
Log-Msg "Deployment SUCCESS! New location: $NewDir" "Green"
Log-Msg "Tip: run /where and /dash to verify version + ENV/MODE." "Green"
exit 0
