Write-Host '=== where python ==='
& where.exe python 2>&1
Write-Host '=== python --version ==='
& python --version 2>&1
Write-Host '=== python -c print(1) ==='
& python -c "print(1)" 2>&1
$r = (Get-ChildItem 'C:\Users\Administrator\ok\releases' -Directory -EA SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
Write-Host '=== Test run from release (timeout 8s) ==='
if ($r) {
  $p = Start-Process -FilePath 'python' -ArgumentList '-u', 'run_bot_vps.py' -WorkingDirectory $r -PassThru -RedirectStandardOutput "$r\test_out.txt" -RedirectStandardError "$r\test_err.txt" -NoNewWindow
  Start-Sleep -Seconds 8
  if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
  Write-Host '--- test_out.txt ---'
  Get-Content "$r\test_out.txt" -ErrorAction SilentlyContinue
  Write-Host '--- test_err.txt ---'
  Get-Content "$r\test_err.txt" -ErrorAction SilentlyContinue
}
