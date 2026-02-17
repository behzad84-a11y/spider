@echo off
cd /d C:\trade\me\ok

echo ==========================
echo  GIT AUTO PUSH
echo ==========================

git add .

git commit -m "auto update %date% %time%"

git push origin main

echo ==========================
echo   DONE
echo ==========================

pause