@echo off
setlocal
echo ==============================================
3: echo        VPS DIAGNOSTIC TOOL (Spider Bot)
4: echo ==============================================
5: 
6: set VPS_IP=87.106.210.120
7: set VPS_USER=Administrator
8: set VPS_PASS=000cdewsxzaQ
9: 
10: echo [1/5] Checking Local Files...
11: if not exist pscp.exe echo [ERROR] pscp.exe missing!
12: if not exist plink.exe echo [ERROR] plink.exe missing!
13: if not exist spider_trading_bot.py echo [ERROR] spider_trading_bot.py missing!
14: 
15: echo [2/5] Ping Test to VPS (%VPS_IP%)...
16: ping -n 2 %VPS_IP%
17: if %errorlevel% neq 0 echo [FAIL] VPS is unreachable.
18: 
19: echo [3/5] Testing SSH Credentials...
20: plink.exe -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP% "echo Connection Successful!"
21: if %errorlevel% neq 0 (
22:     echo [FAIL] SSH failed. Possible causes:
23:     echo - Wrong Password
24:     echo - Host key not verified (Run CheckSSH.bat first)
25:     echo - Firewall blockage
26: )
27: 
28: echo [4/5] Checking Python on VPS...
29: plink.exe -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP% "where python & python --version"
30: if %errorlevel% neq 0 echo [FAIL] Python not found in Path on VPS.
31: 
32: echo [5/5] Checking Bot Process on VPS...
33: plink.exe -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP% "tasklist /FI \"IMAGENAME eq python.exe\""
34: 
35: echo.
36: echo Diagnostic Finished.
37: pause
