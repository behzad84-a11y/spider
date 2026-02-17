@echo off
setlocal enabledelayedexpansion
pushd "%~dp0"

:: Define unique switch log
set "BUILD_ID=%date:~10,4%%date:~4,2%%date:~7,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "BUILD_ID=%BUILD_ID: =0%"
set "LOG_FILE=logs\switch_vps_%BUILD_ID%.log"
if not exist logs mkdir logs

:: If called with _main, just run the logic (for internal use by PowerShell Tee)
if "%~1"=="_main" goto :_main

echo [LOG START: %date% %time%] > "%LOG_FILE%"

:: Run the script logic through PowerShell to get real-time screen output AND file logging
powershell -NoProfile -ExecutionPolicy Bypass -Command "& { & '%~f0' _main 2>&1 | Tee-Object -FilePath '%LOG_FILE%'; exit $LASTEXITCODE }"
set "EXIT_CODE=%errorlevel%"

:: Banner Logic
echo.
if %EXIT_CODE% equ 0 (
    echo ------------------------------------------
    echo [SYNC] Downloading VPS logs to local...
    if not exist logs\vps_latest mkdir logs\vps_latest
    :: Attempt to download paper, live, and dev logs from the new data directory
    "%~dp0pscp.exe" -P %VPS_PORT% -pw %VPS_PASS% %VPS_USER%@%VPS_HOST%:C:\bot\data\logs\*.log logs\vps_latest\ >nul 2>&1
    
    echo [6/6] Final cleanup...
    if exist deploy_package.zip del deploy_package.zip >nul 2>&1
    if exist deploy_package_new.zip del deploy_package_new.zip >nul 2>&1

    echo.
    echo ==========================================
    echo    SUCCESS: BLUE/GREEN DEPLOY COMPLETE
    echo    ROLE: Transitioning on VPS
    echo    LOGS: Synced to logs\vps_latest\
    echo ==========================================
    echo [OK] Monitor /where for Master status.
) else (
    echo ==========================================
    echo    FAILED: DEPLOYMENT CRASHED [!EXIT_CODE!]
    echo ==========================================
    echo [ERROR] Please check %LOG_FILE% for details.
)

popd
echo.
exit /b %EXIT_CODE%

:_main
echo ==========================================
echo    SwitchToVPS (BLUE/GREEN SYSTEM)
echo ==========================================

:: [1/6] Load Config
echo [1/6] Loading vps_config.env...
if not exist vps_config.env (
    echo [خطا] فایل vps_config.env یافت نشد.
    exit /b 1
)
for /f "usebackq tokens=1,2 delims==" %%a in ("vps_config.env") do (
    set "%%a=%%b"
)

REM [1.5] Local uses BOT_TOKEN_DEV, VPS uses BOT_TOKEN_LIVE — different tokens, no 409. Do NOT kill local on deploy.
echo [1.5/6] Skipping local bot stop (LOCAL=DEV token, VPS=LIVE token; no conflict)...
ping -n 1 127.0.0.1 >nul

echo [2/6] Creating Deployment Zip (all .py + config)...
set "ZIP_NAME=deploy_package_new.zip"

powershell -NoProfile -ExecutionPolicy Bypass -Command "Push-Location '%~dp0'; $items = @(); Get-ChildItem -Path . -Filter *.py -Recurse -File | Where-Object { $_.FullName -notmatch '\\.venv\\|\\R\\|New folder\\' } | ForEach-Object { $items += $_.FullName }; Get-ChildItem -Path . -Filter *.bat -File | ForEach-Object { $items += $_.FullName }; foreach ($f in @('.env.example','requirements.txt','deploy_vps.ps1')) { if (Test-Path $f) { $items += (Resolve-Path $f).Path } }; $items = $items | Select-Object -Unique; if ($items.Count -eq 0) { Write-Error 'No files to zip'; exit 1 }; Write-Host \"Packing $($items.Count) files...\"; if (Test-Path '.\deploy_package_new.zip') { Remove-Item '.\deploy_package_new.zip' -Force }; Compress-Archive -Path $items -DestinationPath '.\deploy_package_new.zip' -Force; Pop-Location"

if not exist "%ZIP_NAME%" (
    echo ERROR: Failed to create zip artifact.
    exit /b 1
)

echo [2.5/6] Preparing VPS Configuration (Strict Mode)...
python prepare_vps_env.py
if %errorlevel% neq 0 (
    echo [FATAL] VPS Environment Preparation Failed!
    echo Check local .env for BOT_TOKEN_LIVE.
    exit /b 1
)
if not exist "vps_generated.env" (
    echo ERROR: vps_generated.env was not created.
    exit /b 1
)

echo [3/6] Uploading to VPS (%VPS_HOST%)...
"%~dp0pscp.exe" -batch -P %VPS_PORT% -pw %VPS_PASS% "%ZIP_NAME%" deploy_vps.ps1 vps_generated.env %VPS_USER%@%VPS_HOST%:"%VPS_BOT_DIR%/"
if %errorlevel% neq 0 (
    echo ERROR: Upload failed.
    exit /b 1
)
rem vps_generated.env is uploaded to %VPS_BOT_DIR%\vps_generated.env; deploy_vps.ps1 is called with -EnvFile that path
rem actually pscp can upload vps_generated.env as .env if we specify target filename?
rem pscp syntax: pscp [options] source [user@]host:target
rem If target ends in /, it's a directory.
rem Let's do it in two pscp commands or just one and rename later.
rem Simpler: Upload as vps_generated.env, then rename using plink.

rem (Removed premature move of vps_generated.env. deploy_vps.ps1 handles it now.)


echo [4/6] Remote Deployment Triggered...
echo ------------------------------------------
"%~dp0plink.exe" -batch -P %VPS_PORT% -pw %VPS_PASS% %VPS_USER%@%VPS_HOST% "powershell -ExecutionPolicy Bypass -File %VPS_BOT_DIR%\deploy_vps.ps1 -ZipFile %VPS_BOT_DIR%\%ZIP_NAME% -RemoteDir %VPS_BOT_DIR% -EnvFile %VPS_BOT_DIR%\vps_generated.env"
set "PLINK_ERR=%errorlevel%"
echo ------------------------------------------

if %PLINK_ERR% neq 0 (
    echo ERROR: Remote deployment script failed.
    exit /b 1
)

exit /b 0
