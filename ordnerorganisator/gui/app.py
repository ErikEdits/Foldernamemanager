"""Start der grafischen Oberfläche."""

from __future__ import annotations

import sys

from .. import APP_NAME, __version__


def run(argv: list[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "Die Oberfläche benötigt PySide6 (pip install PySide6). Die Kommandozeile steht ohne PySide6 "
            "zur Verfügung: python -m ordnerorganisator --help",
            file=sys.stderr,
        )
        return 1
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("KI.Ordnerorganisator")  # type: ignore
        except Exception:
            pass
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    from .main_window import MainWindow

    win = MainWindow()
    win.show()
    return app.exec()
