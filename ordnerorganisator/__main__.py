"""Startpunkt: ohne Argumente die Oberfläche, mit Argumenten die Kommandozeile."""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from .cli import main as cli_main

        return cli_main(sys.argv[1:])
    from .gui.app import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
