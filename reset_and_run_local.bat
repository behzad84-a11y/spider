@echo off
REM Reset and run local bot so code changes take effect
cd /d "%~dp0"

echo Stopping any running bot (spider_trading_bot.py)...
powershell -NoProfile -File "%~dp0kill_bot.ps1"
timeout /t 2 /nobreak >nul

echo Starting bot with latest code...
call run_bot_local.bat
