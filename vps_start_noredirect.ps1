$r = (Get-ChildItem 'C:\Users\Administrator\ok\releases' -Directory -EA SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
$env:ENV_TYPE = 'VPS'
$p = Start-Process -FilePath 'python' -ArgumentList '-u', 'run_bot_vps.py' -WorkingDirectory $r -PassThru -WindowStyle Hidden
Write-Host "Started PID: $($p.Id). Wait 25s then check."
Start-Sleep -Seconds 25
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'"
Write-Host "Python count: $($procs.Count)"
$procs | ForEach-Object { Write-Host "  PID $($_.ProcessId): $($_.CommandLine)" }
