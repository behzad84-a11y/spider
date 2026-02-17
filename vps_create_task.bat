@echo off
cd /d "%~dp0"
plink.exe -P 22 -batch -pw "000cdewsxzaQ" Administrator@87.106.210.120 "schtasks /create /tn \"SpiderBot\" /tr \"cmd /c cd /d C:\Users\Administrator\ok\releases\20260216_184531 && python -u run_bot_vps.py >> spider_fresh.out.log 2>> spider_fresh.err.log\" /sc once /st 00:00 /f && schtasks /run /tn \"SpiderBot\""
