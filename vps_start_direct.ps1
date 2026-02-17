# Start runner directly with redirect (same as test that worked)
$r = (Get-ChildItem 'C:\Users\Administrator\ok\releases' -Directory -EA SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
if (-not $r) { Write-Host 'No release'; exit 1 }
$BotOut = Join-Path $r 'spider.out.log'
$BotErr = Join-Path $r 'spider.err.log'
$env:ENV_TYPE = 'VPS'
$p = Start-Process -FilePath 'python' -ArgumentList '-u', 'run_bot_vps.py' -WorkingDirectory $r -PassThru `
  -RedirectStandardOutput $BotOut -RedirectStandardError $BotErr -WindowStyle Hidden
Write-Host "Started PID: $($p.Id) -> $BotOut / $BotErr"