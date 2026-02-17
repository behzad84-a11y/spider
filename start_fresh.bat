@echo off
cd /d "C:\Users\Administrator\ok\releases\20260216_184531"
set ENV_TYPE=VPS
python -u run_bot_vps.py >> spider_fresh.out.log 2>> spider_fresh.err.log
