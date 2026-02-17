@echo off
setlocal

:: --- CONFIGURATION ---
set VPS_USER=Administrator
set VPS_PASS=000cdewsxzaQ
set VPS_IP=87.106.210.120
set VPS_PATH=.
:: ---------------------

echo ==========================================
echo   SWITCHING TO LOCAL MODE (STOPPING VPS)
echo ==========================================

echo [1/3] Stopping Remote Bot (VPS)...
plink.exe -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP% "taskkill /F /IM python.exe /IM cmd.exe" >nul 2>&1
echo Remote bot stopped.

echo [2/3] Syncing Database (Downloading trades.db)...
pscp.exe -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP%:%VPS_PATH%/trades.db trades.db
if %errorlevel% neq 0 (
    echo WARNING: Failed to download database. Using local version.
) else (
    echo Database synced successfully.
)

echo [3/3] Starting Local Bot...
python spider_trading_bot.py

echo Done. You are now running LOCALLY.
pause
