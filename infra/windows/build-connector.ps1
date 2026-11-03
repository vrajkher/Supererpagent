# Build and smoke-test from any working directory; sign the release artifact separately.
$ErrorActionPreference = "Stop"
Push-Location (Resolve-Path "$PSScriptRoot\..\..")
try {
    python -m venv .build
    if ($LASTEXITCODE -ne 0) { throw "Build environment creation failed" }
    .\.build\Scripts\python.exe -m pip install -e . pyinstaller==6.22.3
    if ($LASTEXITCODE -ne 0) { throw "Build dependency installation failed" }
    .\.build\Scripts\python.exe -m PyInstaller --noconfirm --onefile --name raj-connector --collect-submodules uvicorn raj\connector\__main__.py
    if ($LASTEXITCODE -ne 0) { throw "Connector build failed" }
    .\dist\raj-connector.exe --help
    if ($LASTEXITCODE -ne 0) { throw "Frozen connector startup failed" }
} finally {
    Pop-Location
}
# signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a dist\raj-connector.exe
