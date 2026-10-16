"""Startdatei für die Kommandozeilen-Version (PyInstaller, ohne Qt)."""

import sys

from ordnerorganisator.cli import main

if __name__ == "__main__":
    sys.exit(main())
