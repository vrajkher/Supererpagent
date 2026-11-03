# Build a single-file connector on a clean Windows build machine, then sign it.
python -m venv .build; .\.build\Scripts\pip install -e . pyinstaller
.\.build\Scripts\pyinstaller --onefile --name raj-connector --collect-submodules uvicorn raj\connector\__main__.py
# signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a dist\raj-connector.exe
