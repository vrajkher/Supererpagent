# Build a single-file connector on a clean Windows build machine, then sign it.
# CI (.github/workflows/ci.yml, job "windows-connector") runs the same commands and executes the EXE.
$ErrorActionPreference = "Stop"
python -m venv .build
.\.build\Scripts\pip install -e ".[tally]" "pyinstaller==6.22.3"
.\.build\Scripts\pyinstaller --noconfirm --onefile --name raj-connector --paths . `
  --collect-submodules raj.connector --collect-submodules raj.gateway --collect-submodules raj.tally `
  --collect-submodules uvicorn --collect-submodules rajtally_mcp --collect-submodules mcp.client --collect-submodules mcp.server `
  --collect-submodules mcp.shared --collect-submodules mcp.os --collect-submodules mcp.types `
  --collect-submodules mcp_types --collect-submodules openai_mcp_extensions --collect-data rajtally_mcp `
  infra\windows\raj_connector_entry.py
.\dist\raj-connector.exe --help
.\dist\raj-connector.exe tally-mcp --help
# signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a dist\raj-connector.exe
