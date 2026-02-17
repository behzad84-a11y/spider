$r = Get-ChildItem 'C:\Users\Administrator\ok\releases' -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host 'Starting from:' $r.FullName
$p = Start-Process -FilePath 'python' -ArgumentList 'run_bot_vps.py' -WorkingDirectory $r.FullName -WindowStyle Hidden -PassThru
Write-Host 'Started process Id:' $p.Id
