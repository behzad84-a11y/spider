Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' } | ForEach-Object {
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  Write-Host 'Killed PID' $_.ProcessId
}
$count = (Get-CimInstance Win32_Process -Filter "Name='python.exe'").Count
if ($count -eq 0) { Write-Host 'No python processes were running.' }
