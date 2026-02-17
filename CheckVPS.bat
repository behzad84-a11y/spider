@echo off
set "VPS_IP=87.106.210.120"
set "VPS_USER=Administrator"
set "VPS_PASS=000cdewsxzaQ"

echo Checking Bot Status on VPS (%VPS_IP%)...
echo ------------------------------------------
:: Using a simple tasklist without filters to avoid quoting issues
.\plink.exe -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP% "tasklist /V" | findstr /I "spider_trading_bot.py python"
if %ERRORLEVEL% NEQ 0 (
    echo [OFFLINE] Bot process not detected in tasklist. 
    echo Checking logs as fallback...
    .\plink.exe -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP% "dir C:\Users\Administrator\ok\bot_output.log"
) else (
    echo [ONLINE] Bot is running.
)
pause
