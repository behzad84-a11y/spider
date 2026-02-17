@echo off
pscp -batch -P 22 -pw 000cdewsxzaQ start_remote_bot.ps1 Administrator@87.106.210.120:C:/Users/Administrator/ok/start_remote_bot.ps1
plink -batch -P 22 -pw 000cdewsxzaQ Administrator@87.106.210.120 "powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\ok\start_remote_bot.ps1"
