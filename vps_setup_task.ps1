# Create start batch in release dir, then create and run scheduled task
$releaseDir = 'C:\Users\Administrator\ok\releases\20260216_184531'
$batPath = Join-Path $releaseDir 'start_fresh.bat'
@"
@echo off
cd /d "$releaseDir"
set ENV_TYPE=VPS
python -u run_bot_vps.py >> spider_fresh.out.log 2>> spider_fresh.err.log
"@ | Set-Content -Path $batPath -Encoding ASCII
schtasks /create /tn "SpiderBot" /tr "`"$batPath`"" /sc once /st 00:00 /f
schtasks /run /tn "SpiderBot"
Write-Host "Task created and run. Batch: $batPath"
