$out = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' } | Select-Object ProcessId, CommandLine, CreationDate
$report = if ($out) { $out | Format-List | Out-String } else { 'No python.exe processes found.' }
$report | Set-Content -Path 'C:\Users\Administrator\ok\vps_python_report.txt' -Encoding UTF8
Write-Output $report
