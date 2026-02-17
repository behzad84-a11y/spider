@echo off
cd /d "%~dp0"
plink.exe -P 22 -batch -pw "000cdewsxzaQ" Administrator@87.106.210.120 "cmd /c \"powershell -NoProfile -Command \"Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' } | Select-Object ProcessId, CommandLine, CreationDate | Format-List\"\""
