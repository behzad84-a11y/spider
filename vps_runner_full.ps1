$r = Get-ChildItem 'C:\Users\Administrator\ok\releases' -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($r) {
  $path = Join-Path $r.FullName 'runner.log'
  Write-Host "Full runner.log from $($r.FullName):"
  Get-Content $path -ErrorAction SilentlyContinue
  Write-Host "--- Line count:" (Get-Content $path -ErrorAction SilentlyContinue | Measure-Object -Line).Count
} else { Write-Host 'No releases.' }
