$r = Get-ChildItem 'C:\Users\Administrator\ok\releases' -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $r) { Write-Host 'No releases folder or no subdirs.'; exit 1 }
Write-Host 'Latest release:' $r.FullName
Write-Host '--- spider.out.log (Tail 50) ---'
Get-Content (Join-Path $r.FullName 'spider.out.log') -Tail 50 -ErrorAction SilentlyContinue
Write-Host '--- spider.err.log (Tail 50) ---'
Get-Content (Join-Path $r.FullName 'spider.err.log') -Tail 50 -ErrorAction SilentlyContinue
Write-Host '--- runner.log (Tail 30) ---'
Get-Content (Join-Path $r.FullName 'runner.log') -Tail 30 -ErrorAction SilentlyContinue
