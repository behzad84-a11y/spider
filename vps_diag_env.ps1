Write-Host '=== C:\Users\Administrator\ok\.env ==='
Get-Content 'C:\Users\Administrator\ok\.env' -ErrorAction SilentlyContinue
Write-Host ''
Write-Host '=== Latest release .env ==='
$r = Get-ChildItem 'C:\Users\Administrator\ok\releases' -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($r) { Get-Content (Join-Path $r.FullName '.env') -ErrorAction SilentlyContinue } else { Write-Host 'No releases.' }
