Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*spider_trading_bot*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
