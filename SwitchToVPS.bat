@echo off
setlocal
pushd "%~dp0"

echo ==========================================
echo    ONE-CLICK VPS DEPLOYMENT WRAPPER
echo ==========================================

:: Bypass execution policy and run the hardened deployment script
powershell -ExecutionPolicy Bypass -File HardenedDeploy.ps1

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Deployment failed. Please check the logs above.
    pause
    exit /b 1
)

echo.
echo [SUCCESS] deployment wrapper finished.
popd
pause