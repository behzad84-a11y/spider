@echo off
setlocal

:: --- CONFIGURATION (FILL THESE) ---
set VPS_USER=Administrator
set VPS_PASS=000cdewsxzaQ
set VPS_IP=87.106.210.120
set VPS_PATH=.
:: ----------------------------------

echo [1/3] Stopping local bot...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq spider_trading_bot.py" >nul 2>&1
echo Local bot stopped.

echo [2/3] Uploading files to VPS...
echo Using PSCP for automated password entry...
pscp.exe -batch -pw %VPS_PASS% -scp spider_trading_bot.py %VPS_USER%@%VPS_IP%:%VPS_PATH%/
pscp.exe -batch -pw %VPS_PASS% -scp gln_strategy.py %VPS_USER%@%VPS_IP%:%VPS_PATH%/
pscp.exe -batch -pw %VPS_PASS% -scp forex_strategy.py %VPS_USER%@%VPS_IP%:%VPS_PATH%/
pscp.exe -batch -pw %VPS_PASS% -scp gln_forex_strategy.py %VPS_USER%@%VPS_IP%:%VPS_PATH%/
pscp.exe -batch -pw %VPS_PASS% -scp requirements.txt %VPS_USER%@%VPS_IP%:%VPS_PATH%/
pscp.exe -batch -pw %VPS_PASS% -scp run_bot_vps.bat %VPS_USER%@%VPS_IP%:%VPS_PATH%/

echo [3/3] Restarting bot on VPS...
echo Using Plink for automated restart...
plink.exe -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP% "run_bot_vps.bat"

echo Done! Bot updated on VPS.
