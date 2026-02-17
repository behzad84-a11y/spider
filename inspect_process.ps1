$ErrorActionPreference = "Stop"
Write-Host "--- PROCESS INSPECTION ---" -ForegroundColor Cyan
try {
    $procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' }
    if ($procs) {
        foreach ($p in $procs) {
            Write-Host "PID: $($p.ProcessId)" -ForegroundColor Yellow
            Write-Host "Command: $($p.CommandLine)"
            Write-Host "Created: $($p.CreationDate)"
            Write-Host "--------------------------------"
        }
    }
    else {
        Write-Host "No 'python.exe' processes found." -ForegroundColor Red
    }
}
catch {
    Write-Host "Error: $_" -ForegroundColor Red
}
