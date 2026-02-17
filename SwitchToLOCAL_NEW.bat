@echo off
setlocal enabledelayedexpansion
pushd "%~dp0"

echo ==========================================
echo    SwitchToLOCAL (NEW ROBUST SYSTEM)
echo ==========================================

:: Load Config
if not exist vps_config.env (
    echo [ERROR] vps_config.env missing!
    pause
    exit /b 1
)

for /f "usebackq tokens=1,2 delims==" %%a in ("vps_config.env") do (
    set "%%a=%%b"
)

echo [1/3] Stopping Bot on VPS (%VPS_HOST%)...
"%~dp0plink.exe" -batch -P %VPS_PORT% -pw %VPS_PASS% %VPS_USER%@%VPS_HOST% "for /f \"tokens=2\" %%P in ('tasklist /v /fi \"IMAGENAME eq python.exe\" /fo table ^| findstr /i \"spider_trading_bot.py\"') do taskkill /f /pid %%P"

echo [1.5/3] Stopping existing local bot...
taskkill /f /fi "WINDOWTITLE eq SpiderBot_*" /im python.exe >nul 2>&1
taskkill /f /im python.exe >nul 2>&1

:: Enforce DEV mode locally
powershell -Command "(Get-Content .env) -replace '^MODE=.*', 'MODE=DEV' | Set-Content .env"
ping -n 3 127.0.0.1 >nul

echo [2/3] Starting Local Bot...
start "" cmd /c "python spider_trading_bot.py"
ping -n 10 127.0.0.1 >nul

echo [3/3] Verifying Local Startup...
set "FOUND=0"
tasklist /fi "IMAGENAME eq python.exe" | findstr /i "python.exe" >nul 2>&1
if %errorlevel% equ 0 set "FOUND=1"

if "!FOUND!"=="1" (
    echo.
    echo ==========================================
    echo    SUCCESS: LOCAL SWITCH COMPLETE
    echo ==========================================
) else (
    echo.
    echo ==========================================
    echo    FAILED: LOCAL STARTUP FAILED
    echo ==========================================
    exit /b 1
)
popd
exit /b 0
