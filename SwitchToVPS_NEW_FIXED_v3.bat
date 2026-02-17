@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

REM ============================================================================
REM  SwitchToVPS_NEW.bat  (v2.2 - Packaging fix + loud-on-failure)
REM  - Builds deploy_package.zip via PowerShell Compress-Archive (reliable)
REM  - Stops local bot (if running)
REM  - Uploads deploy_package.zip + deploy_vps.ps1 + vps_generated.env
REM  - Triggers remote deploy (blue/green)
REM  - Syncs VPS logs back to local
REM ============================================================================

cd /d "%~dp0"

echo ==========================================
echo    SwitchToVPS (BLUE/GREEN SYSTEM) v2.2
echo ==========================================

REM -----------------------------
REM [1/6] Load VPS Config
REM -----------------------------
echo [1/6] Loading vps_config.env...
if not exist "vps_config.env" (
  echo [FATAL] Missing vps_config.env in %cd%
  exit /b 2
)

for /f "usebackq tokens=1,* delims==" %%A in ("vps_config.env") do (
  set "K=%%A"
  set "V=%%B"
  if not "!K!"=="" set "!K!=!V!"
)

if "%VPS_HOST%"=="" (
  echo [FATAL] VPS_HOST missing in vps_config.env
  exit /b 2
)
if "%VPS_USER%"=="" (
  echo [FATAL] VPS_USER missing in vps_config.env
  exit /b 2
)

if "%VPS_REMOTE_DIR%"=="" set "VPS_REMOTE_DIR=C:\Users\Administrator\ok"
if "%VPS_SSH_PORT%"=="" set "VPS_SSH_PORT=22"

REM -----------------------------
REM [1.5/6] Stop local bot
REM -----------------------------
echo [1.5/6] Stopping Local Bot Surgically...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*spider_trading_bot.py*' }; " ^
  "foreach($p in $procs){ try{ Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch{} }" >nul 2>nul

REM -----------------------------
REM [2/6] Build Deployment ZIP (reliable)
REM -----------------------------
echo [2/6] Creating Deployment Zip (Excluding folders)...
set "ZIP_NAME=deploy_package.zip"
if exist "%ZIP_NAME%" del /f /q "%ZIP_NAME%" >nul 2>nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = (Resolve-Path '.').Path; " ^
  "$excludeDirs = @('.venv','__pycache__','logs','VPS','R','Everything','Password','.git'); " ^
  "$excludeFiles = @('trades.db','trading_bot.db','bot.log','bot_error_vps.log','bot_output_vps.log','ok.zip','ok.rar','ok3.zip','ok4.zip','deploy.zip','deploy_package.zip'); " ^
  "$files = Get-ChildItem -Path $root -Recurse -File | Where-Object { " ^
  "  $rel = $_.FullName.Substring($root.Length).TrimStart('\'); " ^
  "  ($excludeDirs | ForEach-Object { $rel -like ($_ + '\*') }) -notcontains $true -and " ^
  "  ($excludeFiles -contains $_.Name) -eq $false -and " ^
  "  ( $_.Extension -in @('.py','.md','.txt','.json','.yml','.yaml','.ini') -or $_.Name -in @('requirements.txt','.env.example','vps_config.env','deploy_vps.ps1','SwitchToVPS_NEW.bat','SwitchToLOCAL_NEW.bat','prepare_vps_env.py') ) " ^
  "}; " ^
  "if(-not $files){ Write-Host '[FATAL] No files selected for packaging.'; exit 3 } " ^
  "Compress-Archive -Path $files.FullName -DestinationPath (Join-Path $root '%ZIP_NAME%') -Force" ^
  >nul

if errorlevel 1 (
  echo [FATAL] Packaging failed. ZIP was not created.
  exit /b 3
)

for %%I in ("%ZIP_NAME%") do set "ZIP_SIZE=%%~zI"
echo [OK] ZIP created: %ZIP_NAME% (%ZIP_SIZE% bytes)

REM -----------------------------
REM [2.5/6] Prepare VPS env (optional)
REM -----------------------------
echo [2.5/6] Preparing VPS Configuration...
if exist "prepare_vps_env.py" (
  python prepare_vps_env.py >nul 2>nul
)

if not exist "vps_generated.env" (
  REM Not fatal; deploy script can preserve existing .env
  echo [WARN] vps_generated.env not found. Will preserve existing VPS .env.
)

REM -----------------------------
REM [3/6] Upload to VPS
REM -----------------------------
echo [3/6] Uploading to VPS (%VPS_HOST%)...
if not exist "pscp.exe" (
  echo [FATAL] pscp.exe not found in %cd%
  exit /b 4
)
if not exist "plink.exe" (
  echo [FATAL] plink.exe not found in %cd%
  exit /b 4
)

pscp.exe -P %VPS_SSH_PORT% -batch "%ZIP_NAME%" "%VPS_USER%@%VPS_HOST%:%VPS_REMOTE_DIR%\%ZIP_NAME%"
if errorlevel 1 (
  echo [FATAL] Upload failed for %ZIP_NAME%
  exit /b 5
)

pscp.exe -P %VPS_SSH_PORT% -batch "deploy_vps.ps1" "%VPS_USER%@%VPS_HOST%:%VPS_REMOTE_DIR%\deploy_vps.ps1"
if errorlevel 1 (
  echo [FATAL] Upload failed for deploy_vps.ps1
  exit /b 5
)

if exist "vps_generated.env" (
  pscp.exe -P %VPS_SSH_PORT% -batch "vps_generated.env" "%VPS_USER%@%VPS_HOST%:%VPS_REMOTE_DIR%\vps_generated.env"
)

REM -----------------------------
REM [4/6] Trigger Remote Deploy
REM -----------------------------
echo [4/6] Remote Deployment Triggered...
echo ------------------------------------------
plink.exe -P %VPS_SSH_PORT% -batch "%VPS_USER%@%VPS_HOST%" ^
  "powershell -NoProfile -ExecutionPolicy Bypass -File \"%VPS_REMOTE_DIR%\deploy_vps.ps1\" -ZipFile \"%VPS_REMOTE_DIR%\%ZIP_NAME%\" -RemoteDir \"%VPS_REMOTE_DIR%\""
set "REMOTE_EXIT=%ERRORLEVEL%"
echo ------------------------------------------

if not "%REMOTE_EXIT%"=="0" (
  echo ==========================================
  echo   FAILED: REMOTE DEPLOY (EXIT=%REMOTE_EXIT%)
  echo ==========================================
  echo [ERROR] Check VPS deploy.log and synced logs for details.
) else (
  echo [OK] Remote deploy reported SUCCESS.
)

REM -----------------------------
REM [5/6] Sync VPS logs to local
REM -----------------------------
echo [5/6] Syncing VPS logs to local...
if not exist "logs\vps_latest" mkdir "logs\vps_latest" >nul 2>nul

pscp.exe -P %VPS_SSH_PORT% -batch -r "%VPS_USER%@%VPS_HOST%:%VPS_REMOTE_DIR%\deploy.log" "logs\vps_latest\deploy.log" >nul 2>nul
pscp.exe -P %VPS_SSH_PORT% -batch -r "%VPS_USER%@%VPS_HOST%:%VPS_REMOTE_DIR%\releases\*\spider.out.log" "logs\vps_latest\" >nul 2>nul
pscp.exe -P %VPS_SSH_PORT% -batch -r "%VPS_USER%@%VPS_HOST%:%VPS_REMOTE_DIR%\releases\*\spider.err.log" "logs\vps_latest\" >nul 2>nul

REM -----------------------------
REM [6/6] Finalize
REM -----------------------------
echo [6/6] Final cleanup...
echo.
echo ==========================================
echo   SWITCH COMPLETE
echo   NOTE: Use /where and /dash to verify
echo ==========================================
exit /b %REMOTE_EXIT%
