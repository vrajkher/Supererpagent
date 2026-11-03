# Build raj-connector.exe and the RAJ-Connector-Setup installer on a Windows build machine.
#   ./infra/windows/build-installer.ps1 -Version 0.1.0
# Output: dist\RAJ-Connector-Setup-<version>.exe. Sign both files before giving them to clients:
#   signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a dist\raj-connector.exe   (before ISCC)
#   or pass /DSignTool=... to ISCC with a configured Inno Setup sign tool.
param([string]$Version = "0.1.0")
$ErrorActionPreference = "Stop"
./infra/windows/build-connector.ps1
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { throw "Install Inno Setup 6 (https://jrsoftware.org/isdl.php or: choco install innosetup)" }
& $iscc "/DAppVersion=$Version" infra\windows\raj-connector.iss
Write-Host "Installer: dist\RAJ-Connector-Setup-$Version.exe"
