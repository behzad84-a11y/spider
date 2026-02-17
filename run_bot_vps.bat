@echo off
REM ============================================
REM Spider Trading Bot - VPS PRODUCTION SCRIPT
REM این اسکریپت برای اجرا روی VPS استفاده می‌شه
REM ============================================

cd /d "%~dp0"
set "BOT_DIR=%~dp0"

REM Set environment for VPS
set ENV_TYPE=VPS
set MODE=PAPER
REM Note: MODE can be changed to LIVE in .env or via /switch_mode command

REM Kill existing bot instances safely
echo [%date% %time%] Stopping any existing bot instances...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq SpiderBot_*" >nul 2>&1
powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'spider_trading_bot\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" 2>nul
timeout /t 2 >nul

REM Find Python
set "PYTHON_EXE=python"
if exist "C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe" (
    set "PYTHON_EXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
) else if exist "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" (
    set "PYTHON_EXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
)

REM Verify .env file exists
if not exist ".env" (
    echo ERROR: .env file not found!
    echo Please create .env file with BOT_TOKEN_LIVE and other credentials.
    pause
    exit /b 1
)

REM Detached Execution Loop (Auto-restart on crash)
:loop
echo [%date% %time%] Starting Bot (VPS Mode - ENV_TYPE=VPS, using BOT_TOKEN_LIVE)... >> bot.log
%PYTHON_EXE% spider_trading_bot.py >> bot.log 2>> bot_error.log
echo [%date% %time%] Bot exited with code %ERRORLEVEL%. Restarting in 10s... >> bot_error.log
timeout /t 10
goto loop
