$ErrorActionPreference = "SilentlyContinue"
$Latest = Get-ChildItem C:\Users\Administrator\ok\releases | Sort-Object CreationTime -Descending | Select-Object -First 1
if ($Latest) {
    Write-Host "Latest Release: $($Latest.FullName)"
    $OutLog = "$($Latest.FullName)\spider.out.log"
    $ErrLog = "$($Latest.FullName)\spider.err.log"
    
    if (Test-Path $ErrLog) {
        Write-Host "--- STDERR (Last 50 lines) ---" -ForegroundColor Red
        Get-Content $ErrLog -Tail 50
    }
    else {
        Write-Host "No STDERR log found." -ForegroundColor Yellow
    }

    # 0. Runner Stderr (Critical startup errors)
    $RunnerErr = "$($Latest.FullName)\runner_stderr.log"
    if (Test-Path $RunnerErr) {
        Write-Host "--- RUNNER STDERR ---" -ForegroundColor Red
        try { Get-Content $RunnerErr -Tail 20 } catch { }
    }
    
    # 1. Runner Log
    $RunnerLog = "$($Latest.FullName)\runner.log"
    if (Test-Path $RunnerLog) {
        Write-Host "--- RUNNER LOG (Last 20 lines) ---" -ForegroundColor Magenta
        try {
            Copy-Item $RunnerLog "$RunnerLog.tmp" -Force
            Get-Content "$RunnerLog.tmp" -Tail 20 -Force
            Remove-Item "$RunnerLog.tmp" -Force
        }
        catch { Write-Host "Read Error: $_" }
    }

    # 2. Internal Spider Log
    $InternalLog = "$($Latest.FullName)\spider.log"
    if (Test-Path $InternalLog) {
        Write-Host "--- SPIDER INTERNAL LOG (Last 50 lines) ---" -ForegroundColor Green
        try {
            Copy-Item $InternalLog "$InternalLog.tmp" -Force
            Get-Content "$InternalLog.tmp" -Tail 50 -Force
            Remove-Item "$InternalLog.tmp" -Force
        }
        catch { Write-Host "Read Error: $_" }
    }

    # 3. Stdout/Stderr (Legacy/Wrapper)
    if (Test-Path $OutLog) {
        Write-Host "--- STDOUT (Last 20 lines) ---" -ForegroundColor Cyan
        try {
            Copy-Item $OutLog "$OutLog.tmp" -Force
            Get-Content "$OutLog.tmp" -Tail 20 -Force
            Remove-Item "$OutLog.tmp" -Force
        }
        catch { Write-Host "Read Error: $_" }
    }
    else {
        Write-Host "No STDOUT log found." -ForegroundColor Yellow
    }
}
else {
    Write-Host "No releases found."
}
