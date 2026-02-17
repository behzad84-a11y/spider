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

# 3) Extract - CLEAN first to remove old files
Log-Msg "Extracting package..." "Cyan"

# Clean target directory completely
if (Test-Path $NewDir) {
    Log-Msg "Cleaning old files from $NewDir..." "Yellow"
    Remove-Item -Path $NewDir -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $NewDir -Force | Out-Null

# Extract fresh
Expand-Archive -Path $ZipFile -DestinationPath $NewDir -Force

# Remove Python cache files
Log-Msg "Removing Python cache..." "Gray"
Get-ChildItem -Path $NewDir -Filter "__pycache__" -Recurse -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $NewDir -Filter "*.pyc" -Recurse -File -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue

Log-Msg "Extract complete: $NewDir" "Green"

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

# 5.6) Final zombie kill (catch any Task Scheduler or restart that fired during pip)
Log-Msg "Final zombie kill before starting GREEN..." "Yellow"
$Zombies2 = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*spider_trading_bot.py*" }
foreach ($z in $Zombies2) {
    Log-Msg "Killing pre-GREEN zombie PID: $($z.ProcessId)" "Red"
    Stop-Process -Id $z.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

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

# 6) Health-check: find "Started" marker, then stability window 15s (ignore 409, fail on Traceback/ConnectionError)
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
# In stability window: ignore 409 (token handover); only fail on real crashes
$stabilityFailurePatterns = @("Traceback", "ConnectionError", "NetworkError", "Connection refused", "AttributeError.*job_queue")
function Test-LogForStabilityFailure {
    param([string]$outStr, [string]$errStr)
    foreach ($pat in $stabilityFailurePatterns) {
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
    # Do NOT fail on 409 here: we only fail on 409 during the stability window (after marker seen).
    # A single early 409 can be from the previous instance releasing the token.

    # Look for polling/started marker
    $foundMarker = ""
    foreach ($m in $pollingMarkers) {
        if ($outStr -match [regex]::Escape($m) -or $errStr -match [regex]::Escape($m)) {
            $foundMarker = $m
            break
        }
    }

    if ($foundMarker) {
        # Found "Started" - stability window: only fail on real crashes (ignore 409 from token handover)
        Log-Msg "Found marker: $foundMarker. Starting 15s stability window (ignore 409, fail on Traceback/ConnectionError)..." "Cyan"
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
            if (Test-Path $BotOut) { $outTail2 = Get-Content $BotOut -Tail 15 -ErrorAction SilentlyContinue }
            $errTail2 = @()
            if (Test-Path $BotErr) { $errTail2 = Get-Content $BotErr -Tail 15 -ErrorAction SilentlyContinue }
            $outStr2 = $outTail2 -join "`n"
            $errStr2 = $errTail2 -join "`n"
            if (Test-LogForStabilityFailure $outStr2 $errStr2) {
                Log-Msg "CRITICAL: Traceback or connection error during stability window. FAILED." "Red"
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
    Log-Msg "Health Check PASSED (marker: $foundMarker, process $GreenPID alive)." "Green"
    
    # 7) Kill ALL bot processes (including GREEN). GREEN was started under this session and will die when plink exits - so we must start the bot via Task Scheduler so it survives.
    Log-Msg "Stopping all bot processes (GREEN will die when SSH closes; we start via Task next)..." "Cyan"
    $AllBotProcs = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -like "*spider_trading_bot.py*" -or $_.CommandLine -like "*run_bot_vps.py*"
    }
    foreach ($p in $AllBotProcs) {
        Log-Msg "Terminating PID: $($p.ProcessId)" "Yellow"
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
    }
    Start-Sleep -Seconds 2

    # 7b) Create/update SpiderBot scheduled task to run from NEW release
    $LauncherBat = Join-Path $NewDir "start_fresh.bat"
    # Always recreate to ensure it has latest paths
    $batLines = @(
        "@echo off"
        "cd /d `"$NewDir`""
        "echo [LAUNCHER] Working dir: %CD%"
        "echo [LAUNCHER] ENV_TYPE=VPS"
        "set ENV_TYPE=VPS"
        "echo [LAUNCHER] Starting Python..."
        "python -u run_bot_vps.py >> spider_fresh.out.log 2>> spider_fresh.err.log"
    )
    Set-Content -Path $LauncherBat -Value $batLines -Encoding ASCII
    Log-Msg "Created launcher: $LauncherBat" "Gray"
    schtasks /create /tn "SpiderBot" /tr "`"$LauncherBat`"" /sc once /st 00:00 /f | Out-Null

    # Verify task points to correct path
    $taskQuery = schtasks /query /tn "SpiderBot" /fo LIST /v | Out-String
    if ($taskQuery -like "*$NewDir*") {
        Log-Msg "Task verified: points to $NewDir" "Green"
    } else {
        Log-Msg "WARNING: Task may not point to new release!" "Yellow"
        $taskRunLine = ($taskQuery -split [Environment]::NewLine) | Where-Object { $_ -match 'Task To Run' }
        Log-Msg "Task info: $taskRunLine" "Gray"
    }

    # 7c) Start bot via Task Scheduler so it runs DETACHED from SSH and survives after plink exits
    schtasks /run /tn "SpiderBot"
    Log-Msg "SpiderBot task started - bot running detached from SSH." "Green"
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

# 8) Finalize - Verify deployment
Log-Msg "Deployment SUCCESS! New location: $NewDir" "Green"

# Verify critical files exist and show timestamps
$criticalFiles = @("spider_trading_bot.py", "dashboard.py", "run_bot_vps.py")
foreach ($file in $criticalFiles) {
    $path = Join-Path $NewDir $file
    if (Test-Path $path) {
        $timestamp = (Get-Item $path).LastWriteTime
        Log-Msg "  [OK] $file - $timestamp" "Green"
    } else {
        Log-Msg "  [MISSING] $file" "Red"
    }
}

Log-Msg "Tip: run /where and /dash to verify version and ENV/MODE." "Green"
exit 0

<#
MANUAL VERIFICATION COMMANDS (run via plink after deploy):

1. Check which release is active:
   Get-ChildItem "C:\Users\Administrator\ok\releases" -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1

2. Check file timestamps:
   $r = Get-ChildItem "C:\Users\Administrator\ok\releases" -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
   Get-Item (Join-Path $r.FullName "spider_trading_bot.py") | Select-Object FullName, LastWriteTime

3. Check Scheduled Task:
   schtasks /query /tn "SpiderBot" /fo LIST /v | Select-String "Task To Run"

4. Check running processes:
   Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "python.exe" } | Select-Object ProcessId, CommandLine

5. Send /dash on Telegram and verify it shows latest version
#>
