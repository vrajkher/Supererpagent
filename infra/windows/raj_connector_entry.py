"""PyInstaller entry for raj-connector.exe (R1).

A frozen entry script runs as a top-level ``__main__`` with no parent package, so it must use absolute
imports. ``raj/connector/__main__.py`` (``from .cli import main``) is only for ``python -m raj.connector``.
"""
from raj.connector.cli import main

if __name__ == "__main__":
    main()
