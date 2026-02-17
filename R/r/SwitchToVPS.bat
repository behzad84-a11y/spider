@echo off
setlocal

REM Always run from this .bat directory (prevents nightly failures / wrong relative paths)
pushd "%~dp0"

:: --- CONFIGURATION ---
set VPS_USER=Administrator
set VPS_PASS=000cdewsxzaQ
set VPS_IP=87.106.210.120
set VPS_PATH=.
:: ---------------------

echo ==========================================
echo   SWITCHING TO VPS MODE (STOPPING LOCAL)
echo ==========================================

echo [0/3] Checking Connection to %VPS_IP%...
ping -n 1 %VPS_IP% >nul
if %errorlevel% neq 0 (
    echo -----------------------------------------------------
    echo [ERROR] Cannot reach VPS at %VPS_IP%
    echo Possible causes:
    echo 1. VPS is powered OFF.
    echo 2. Wrong IP address.
    echo 3. Firewall blocking connection.
    echo -----------------------------------------------------
    popd
    pause
    exit /b 1
)
echo Connection OK.

echo [1/3] Stopping Local Bot...
REM NOTE: killing all python.exe is aggressive but keeps your original behavior.
taskkill /F /IM python.exe /FI "WINDOWTITLE eq spider_trading_bot.py" >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
echo Local bot stopped.

echo [2/3] Uploading Code, ENV and Database (Syncing)...
echo --------------------------------------------

REM Use absolute tool paths to avoid "works sometimes" issues
"%~dp0pscp.exe" -batch -pw %VPS_PASS% -scp *.py %VPS_USER%@%VPS_IP%:%VPS_PATH%/
if %errorlevel% neq 0 (
    echo [ERROR] Failed to upload Python files!
    popd
    pause
    exit /b 1
)

"%~dp0pscp.exe" -batch -pw %VPS_PASS% -scp *.txt %VPS_USER%@%VPS_IP%:%VPS_PATH%/
"%~dp0pscp.exe" -batch -pw %VPS_PASS% -scp *.bat %VPS_USER%@%VPS_IP%:%VPS_PATH%/
"%~dp0pscp.exe" -batch -pw %VPS_PASS% -scp *.sh %VPS_USER%@%VPS_IP%:%VPS_PATH%/
"%~dp0pscp.exe" -batch -pw %VPS_PASS% -scp .env %VPS_USER%@%VPS_IP%:%VPS_PATH%/

echo 4. Database (CRITICAL)...
"%~dp0pscp.exe" -batch -pw %VPS_PASS% -scp trades.db %VPS_USER%@%VPS_IP%:%VPS_PATH%/
if %errorlevel% neq 0 (
    echo [ERROR] Failed to upload database!
    popd
    pause
    exit /b 1
)

echo [3/3] Starting Bot on VPS (Background Persistent Mode)...
echo Connecting and triggering remote restart...

"%~dp0plink.exe" -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP% ^
"schtasks /delete /f /tn TradingBot >nul 2>&1 & taskkill /F /IM python.exe /F >nul 2>&1 & schtasks /create /f /tn TradingBot /sc once /st 00:00 /tr \"cmd.exe /c C:\Users\Administrator\run_bot_vps.bat\" /ru %VPS_USER% /rp %VPS_PASS% && schtasks /run /tn TradingBot"

if %errorlevel% neq 0 (
    echo [ERROR] Failed to start bot on VPS.
    echo Please check if the path C:\Users\Administrator\run_bot_vps.bat is correct on the VPS.
    popd
    pause
    exit /b 1
)

echo.
echo ==========================================
echo  SUCCESS! Bot is now running on VPS.
echo ==========================================

popd
pause
