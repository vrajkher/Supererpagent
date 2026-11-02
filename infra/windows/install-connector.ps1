# Run as Administrator on the client's Tally PC.
# Prereqs: Windows 10/11 with the built-in OpenSSH client (Settings > Optional features), TallyPrime with
# XML/HTTP enabled on port 9000 (F1 Help > Settings > Connectivity), the client company open.
param(
  [Parameter(Mandatory=$true)][string]$Server,
  [Parameter(Mandatory=$true)][string]$Code,
  [string]$Exe = "$PSScriptRoot\raj-connector.exe",
  [string]$Data = "$env:ProgramData\RAJ",
  [string]$Nssm = "$PSScriptRoot\nssm.exe"
)
$ErrorActionPreference = "Stop"
$sig = Get-AuthenticodeSignature $Exe
if ($sig.Status -ne "Valid") { throw "raj-connector.exe is not validly signed ($($sig.Status)). Do not install." }
New-Item -ItemType Directory -Force -Path $Data | Out-Null
icacls $Data /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" | Out-Null
& $Exe --data $Data companies
& $Exe --data $Data pair --server $Server --code $Code
# Service runs as LocalSystem; Tally must be running in the interactive session with the company open.
& $Nssm install RAJConnector $Exe --data $Data run
& $Nssm set RAJConnector Start SERVICE_AUTO_START
& $Nssm set RAJConnector AppRestartDelay 5000
& $Nssm set RAJConnector AppStdout "$Data\connector.log"
& $Nssm set RAJConnector AppStderr "$Data\connector.log"
& $Nssm start RAJConnector
Write-Host "Installed. Check status with: & '$Exe' --data '$Data' status"
