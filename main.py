import atexit
import signal
import sys

from PySide6.QtWidgets import QApplication

from config import APP_TITLE, FULLSCREEN
from kiosk_gui import KioskStemConveyorGUI
from watchdog import ApplicationWatchdog


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setStyle("Fusion")

    # KioskStemConveyorGUI preserves the existing working conveyor GUI and
    # adds shutdown/developer/configuration controls around it.
    window = KioskStemConveyorGUI()
    watchdog = ApplicationWatchdog(app)

    cleanup_done = False

    def safe_cleanup():
        nonlocal cleanup_done
        if cleanup_done:
            return

        cleanup_done = True

        try:
            watchdog.stop()
        except Exception:
            pass

        try:
            window.emergency_stop()
        except Exception:
            pass

    app.aboutToQuit.connect(safe_cleanup)
    atexit.register(safe_cleanup)

    def exception_hook(exc_type, exc_value, traceback):
        safe_cleanup()
        sys.__excepthook__(exc_type, exc_value, traceback)

    sys.excepthook = exception_hook

    def signal_handler(signum, frame):
        try:
            window.allow_external_close()
        except Exception:
            pass
        safe_cleanup()
        app.quit()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if FULLSCREEN:
        window.showFullScreen()
    else:
        window.show()

    watchdog.start()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
