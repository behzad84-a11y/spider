@echo off
setlocal

:: Credentials from deploy_to_vps.bat
set VPS_USER=Administrator
set VPS_PASS=000cdewsxzaQ
set VPS_IP=87.106.210.120

echo [KILL] Stopping bot on VPS...
plink.exe -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP% "taskkill /F /IM python.exe"

echo [DONE] VPS bot should be dead.
pause
