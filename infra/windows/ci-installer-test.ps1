# CI: build the installer, install silently, verify, and uninstall on a clean Windows runner.
# Assumes dist\raj-connector.exe exists (build-connector.ps1) and .build\ is its venv.
$ErrorActionPreference = "Stop"
$fail = 0
function Check($ok, $what) { if ($ok) { Write-Host "PASS  $what" } else { Write-Host "FAIL  $what"; $script:fail = 1 } }

$version = "0.1.0"
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { choco install innosetup -y --no-progress | Out-Null }
& $iscc "/DAppVersion=$version" infra\windows\raj-connector.iss
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }
$setup = "dist\RAJ-Connector-Setup-$version.exe"
Check (Test-Path $setup) "installer built ($setup)"

$p = Start-Process $setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=installer.log" -Wait -PassThru
Check ($p.ExitCode -eq 0) "silent install exit code 0"
$app = "$env:ProgramFiles\RAJ Connector"
$exe = "$app\raj-connector.exe"
$data = "$env:ProgramData\RAJ"
Check (Test-Path $exe) "connector installed to Program Files"
Check (Test-Path "$app\pair.cmd") "pairing script installed"
Check (Test-Path "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\RAJ Connector\Pair this PC with the firm.lnk") "Start menu shortcut"
& $exe --help | Out-Null
Check ($LASTEXITCODE -eq 0) "installed exe runs --help"

# data folder: only SYSTEM and Administrators (no Users / Authenticated Users)
$acl = (icacls $data) -join "`n"
Check (($acl -match "SYSTEM") -and ($acl -match "Administrators") -and -not ($acl -match "Users")) "data folder restricted to SYSTEM and Administrators"

# installed exe talks MCP to its bundled RAJTALLYFINLMCP against the demo Tally
.\.build\Scripts\python infra\windows\smoke_frozen.py $exe
Check ($LASTEXITCODE -eq 0) "installed exe smoke test (MCP company listing)"

# background task can be registered and removed (not started: this runner is not paired)
& $exe --data $data service install --no-start
Check ($LASTEXITCODE -eq 0) "background task registered"
$task = Get-ScheduledTask -TaskName "RAJ Connector" -ErrorAction SilentlyContinue
Check ($null -ne $task) "task exists"
if ($task) {
  Check ($task.Principal.UserId -match "SYSTEM|S-1-5-18") "task runs as SYSTEM"
  Check (($task.Triggers | Where-Object { $_.CimClass.CimClassName -eq "MSFT_TaskBootTrigger" }) -ne $null) "task starts at boot"
  Check ($task.Actions[0].Arguments -match "run --log") "task runs the connector with a log file"
}

$un = Start-Process "$app\unins000.exe" -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
Check ($un.ExitCode -eq 0) "silent uninstall exit code 0"
Start-Sleep -Seconds 3
Check (-not (Test-Path $exe)) "connector removed"
Check ($null -eq (Get-ScheduledTask -TaskName "RAJ Connector" -ErrorAction SilentlyContinue)) "background task removed on uninstall"
Check (Test-Path $data) "data folder kept by default (pairing keys / journal)"

if ($fail) { Get-Content installer.log -Tail 40; exit 1 }
Write-Host "installer: all checks passed"
