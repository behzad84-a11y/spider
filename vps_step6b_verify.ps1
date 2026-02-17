$procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' }
Write-Host 'Remaining python processes:' $procs.Count
