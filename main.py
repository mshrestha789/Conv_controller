import sys

from PySide6.QtWidgets import QApplication

from config import (
    APP_TITLE,
    FULLSCREEN,
)
from gui import StemConveyorGUI


def main():
    app = QApplication(
        sys.argv
    )

    app.setApplicationName(
        APP_TITLE
    )

    app.setStyle(
        "Fusion"
    )

    window = StemConveyorGUI()

    if FULLSCREEN:
        window.showFullScreen()
    else:
        window.show()

    sys.exit(
        app.exec()
    )


if __name__ == "__main__":
    main()
