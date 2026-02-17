@echo off
REM ============================================
REM QUICK ENVIRONMENT SWITCHER
REM Switch between LOCAL and VPS modes instantly
REM ============================================

setlocal enabledelayedexpansion
pushd "%~dp0"

cls
echo.
echo ============================================
echo     ENVIRONMENT SWITCHER
echo ============================================
echo.
echo Current Environment (from .env):
for /f "tokens=2 delims==" %%A in ('findstr /B "ENV_TYPE" .env') do echo   ENV_TYPE = %%A
echo.
echo Choose action:
echo   1. Switch to VPS (deploy to 87.106.210.120)
echo   2. Switch to LOCAL (use local .env)
echo   3. Deploy and Switch to VPS
echo   4. View current .env
echo   5. Exit
echo.
set /p choice="Select (1-5): "

if "%choice%"=="1" goto switch_vps
if "%choice%"=="2" goto switch_local
if "%choice%"=="3" goto deploy_vps
if "%choice%"=="4" goto view_env
if "%choice%"=="5" goto exit_script
goto invalid

:switch_vps
echo.
echo [INFO] Switching to VPS mode...
REM Stop local bot
taskkill /F /IM python.exe /T >nul 2>&1

REM Create backup
copy .env .env.local.backup >nul

REM Check if vps.env exists
if not exist vps.env (
    echo [ERROR] vps.env not found!
    echo Please run deploy script first.
    pause
    goto exit_script
)

REM Use vps.env as .env
copy vps.env .env >nul

echo [SUCCESS] Switched to VPS mode
echo   ENV_TYPE = VPS
echo   MODE = LIVE
echo   Backup saved as: .env.local.backup
echo.
pause
goto exit_script

:switch_local
echo.
echo [INFO] Switching to LOCAL mode...

REM Create backup
copy .env .env.vps.backup >nul

REM Create or restore local .env
(
    echo BOT_TOKEN=8322852694:AAHndfTGPjyPneeB6mkAKLfv4TopZ7QdxuE
    echo MODE=DEV
    echo ENV_TYPE=LOCAL
    echo EXCHANGE_TYPE=coinex
    echo COINEX_API_KEY=C739AFE1A401410EAA03D28D4ADE1BD5
    echo COINEX_SECRET=8E5B70913E3B2526A9896969E3483635E5FB95F60373E2EC
    echo KUCOIN_API_KEY=698a0d79bec02400012e083a
    echo KUCOIN_SECRET=cf637b1f-2941-4a55-b40f-d14ecc00bcda
    echo KUCOIN_PASSPHRASE=000cdewsxzaQ@
) > .env

echo [SUCCESS] Switched to LOCAL mode
echo   ENV_TYPE = LOCAL
echo   MODE = DEV
echo   Backup saved as: .env.vps.backup
echo.
pause
goto exit_script

:deploy_vps
echo.
echo [INFO] Starting VPS deployment with environment switch...
echo.

REM Check if vps.env exists
if not exist vps.env (
    echo [ERROR] vps.env not found!
    echo Creating vps.env from current .env...
    
    (
        echo BOT_TOKEN=8322852694:AAHndfTGPjyPneeB6mkAKLfv4TopZ7QdxuE
        echo MODE=LIVE
        echo ENV_TYPE=VPS
        echo EXCHANGE_TYPE=coinex
        echo COINEX_API_KEY=C739AFE1A401410EAA03D28D4ADE1BD5
        echo COINEX_SECRET=8E5B70913E3B2526A9896969E3483635E5FB95F60373E2EC
        echo KUCOIN_API_KEY=698a0d79bec02400012e083a
        echo KUCOIN_SECRET=cf637b1f-2941-4a55-b40f-d14ecc00bcda
        echo KUCOIN_PASSPHRASE=000cdewsxzaQ@
    ) > vps.env
    
    echo [OK] vps.env created
)

REM Run the improved deployment script
if exist DeployToVPS_Fixed.ps1 (
    powershell -ExecutionPolicy Bypass -File DeployToVPS_Fixed.ps1
) else if exist HardenedDeploy.ps1 (
    echo [WARNING] Using old deployment script. Recommended: Use DeployToVPS_Fixed.ps1
    powershell -ExecutionPolicy Bypass -File HardenedDeploy.ps1
) else (
    echo [ERROR] No deployment script found!
    pause
)

goto exit_script

:view_env
echo.
echo Current .env file:
echo ============================================
type .env
echo ============================================
echo.
pause
goto exit_script

:invalid
echo.
echo [ERROR] Invalid choice!
pause
goto exit_script

:exit_script
popd
endlocal
