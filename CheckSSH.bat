@echo off
setlocal
echo ==============================================
echo        VPS SSH CONNECTION TEST (DEBUG)
echo ==============================================

set VPS_IP=87.106.210.120
set VPS_USER=Administrator
set VPS_PASS=000cdewsxzaQ

echo 1. Ping Test (ICMP)...
ping -n 4 %VPS_IP%
if %errorlevel% neq 0 (
    echo [FAIL] Ping Failed. VPS is OFFLINE or UNREACHABLE.
    echo Check your internet or VPS status.
    pause
    exit /b
)
echo [OK] Ping Success.

echo.
echo 2. SSH Port Test (Simulated via Plink)...
echo Connecting with verbose output (-v)...
plink.exe -v -pw %VPS_PASS% %VPS_USER%@%VPS_IP% "echo SSH Connection Works!"

echo.
echo ==============================================
echo [ANALYSIS]
echo If you saw "Access denied" -> Password is WRONG.
echo If you saw "Connection timed out" -> VPS is FROZEN or FIREWALL BLOCKED.
echo If you saw "SSH Connection Works!" -> Everything is fine.
echo ==============================================
pause
