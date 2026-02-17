# Check if running as Administrator
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Warning "Please run this script as Administrator!"
    exit
}

Write-Host "Installing OpenSSH Server..." -ForegroundColor Green
# Install OpenSSH Server
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

Write-Host "Starting OpenSSH Service..." -ForegroundColor Green
# Start the service and set to Automatic
Start-Service sshd
Set-Service -Name sshd -StartupType 'Automatic'

Write-Host "Configuring Firewall..." -ForegroundColor Green
# Open Firewall Port 22
if (!(Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue | Select-Object Name, Enabled)) {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
} else {
    Write-Host "Firewall rule already exists."
}

Write-Host "Done! SSH is now active." -ForegroundColor Cyan
Write-Host "You can now use the deploy script from your local machine." -ForegroundColor Cyan
Pause
