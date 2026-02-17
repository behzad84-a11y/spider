@echo off
REM ============================================
REM Spider Trading Bot - LOCAL (Always Running)
REM این اسکریپت ربات محلی رو همیشه در حال اجرا نگه می‌داره
REM اگه crash کرد، خودش restart می‌کنه
REM ============================================

cd /d "%~dp0"

REM Set environment for LOCAL
set ENV_TYPE=LOCAL
set MODE=DEV

REM Check if venv exists
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] venv not found!
    echo Please run: python -m venv .venv
    echo Then: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

REM Verify .env file
if not exist ".env" (
    echo [ERROR] .env file not found!
    pause
    exit /b 1
)

echo ============================================
echo    LOCAL BOT - Always Running Mode
echo ============================================
echo Environment: LOCAL
echo Mode: DEV
echo Token: BOT_TOKEN_DEV
echo.
echo Bot will restart automatically if it crashes.
echo Press Ctrl+C to stop.
echo ============================================
echo.

REM Main loop - restart on crash
:loop
echo [%date% %time%] Starting local bot...
.\.venv\Scripts\python.exe -u spider_trading_bot.py

REM If we get here, bot exited
set EXIT_CODE=%ERRORLEVEL%
echo.
echo [%date% %time%] Bot exited with code %EXIT_CODE%

if %EXIT_CODE% equ 0 (
    echo Bot stopped cleanly. Restarting in 5 seconds...
    timeout /t 5 >nul
) else (
    echo Bot crashed! Restarting in 10 seconds...
    timeout /t 10 >nul
)

goto loop

