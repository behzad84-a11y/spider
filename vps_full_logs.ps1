$r = (Get-ChildItem 'C:\Users\Administrator\ok\releases' -Directory -EA SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
Write-Host '=== runner.log (full) ==='
Get-Content (Join-Path $r 'runner.log') -EA SilentlyContinue
Write-Host ''
Write-Host '=== spider.out.log (full) ==='
Get-Content (Join-Path $r 'spider.out.log') -EA SilentlyContinue
Write-Host ''
Write-Host '=== spider.err.log (last 60) ==='
Get-Content (Join-Path $r 'spider.err.log') -Tail 60 -EA SilentlyContinue
