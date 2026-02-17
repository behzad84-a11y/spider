@echo off
REM ============================================
REM Spider Trading Bot - LOCAL TEST SCRIPT
REM این اسکریپت برای تست محلی استفاده می‌شه
REM ============================================

cd /d "%~dp0"

REM Kill existing bot instances
echo [%date% %time%] Stopping any existing bot instances...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq SpiderBot_*" >nul 2>&1
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'spider_trading_bot\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force } 2>nul

REM Wait a moment
timeout /t 2 >nul

REM Set environment for LOCAL testing
set ENV_TYPE=LOCAL
set MODE=DEV

REM Check if .venv exists, if not create it
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    echo Installing dependencies...
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)

REM Verify .env file exists
if not exist ".env" (
    echo ERROR: .env file not found!
    echo Please create .env file with BOT_TOKEN_DEV and other credentials.
    pause
    exit /b 1
)

REM Run bot with LOCAL environment
echo [%date% %time%] Starting LOCAL bot (ENV_TYPE=LOCAL, MODE=DEV)...
echo Using BOT_TOKEN_DEV from .env
echo.
.venv\Scripts\python.exe spider_trading_bot.py

REM If bot exits, show message
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Bot exited with error code %ERRORLEVEL%
    pause
)
