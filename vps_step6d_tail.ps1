Start-Sleep -Seconds 15
$r = Get-ChildItem 'C:\Users\Administrator\ok\releases' -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host 'Latest release:' $r.FullName
Get-Content (Join-Path $r.FullName 'spider.out.log') -Tail 30
