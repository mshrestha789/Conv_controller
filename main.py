import atexit
import signal
import sys

from PySide6.QtWidgets import QApplication

from config import APP_TITLE, FULLSCREEN
from gui import StemConveyorGUI
from watchdog import ApplicationWatchdog


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setStyle("Fusion")

    # StemConveyorGUI constructs Hardware first, which immediately commands
    # the active-low conveyor relay OFF before camera activity begins.
    window = StemConveyorGUI()
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

    # Normal Qt shutdown.
    app.aboutToQuit.connect(safe_cleanup)

    # Best effort for normal Python process exit.
    atexit.register(safe_cleanup)

    def exception_hook(exc_type, exc_value, traceback):
        safe_cleanup()
        sys.__excepthook__(exc_type, exc_value, traceback)

    sys.excepthook = exception_hook

    def signal_handler(signum, frame):
        safe_cleanup()
        app.quit()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if FULLSCREEN:
        window.showFullScreen()
    else:
        window.show()

    # READY=1 and WATCHDOG=1 are sent only after the hardware object and GUI
    # have been created. When run manually this is a harmless no-op.
    watchdog.start()

    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
