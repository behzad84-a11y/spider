$r = Get-ChildItem 'C:\Users\Administrator\ok\releases' -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $r) { Write-Host 'No release'; exit 1 }
Write-Host '=== spider.out.log (Tail 35) ==='
Get-Content (Join-Path $r.FullName 'spider.out.log') -Tail 35 -ErrorAction SilentlyContinue
Write-Host ''
Write-Host '=== spider.err.log (Tail 25) ==='
Get-Content (Join-Path $r.FullName 'spider.err.log') -Tail 25 -ErrorAction SilentlyContinue
Write-Host ''
Write-Host '=== runner.log (Tail 15) ==='
Get-Content (Join-Path $r.FullName 'runner.log') -Tail 15 -ErrorAction SilentlyContinue
