"""systemd application-watchdog support without extra Python packages.

The heartbeat timer is a Qt QTimer in the main GUI event loop. If the GUI event
loop freezes, heartbeat messages stop and systemd's WatchdogSec can restart the
application.
"""

import os
import socket

from PySide6.QtCore import QObject, QTimer

from config import APP_WATCHDOG_HEARTBEAT_MS


class SystemdNotifier:
    def __init__(self):
        self.address = os.environ.get("NOTIFY_SOCKET")

    @property
    def available(self):
        return bool(self.address)

    def notify(self, message: str) -> bool:
        if not self.address:
            return False

        address = self.address
        if address.startswith("@"):
            # Abstract Unix-domain socket used by systemd.
            address = "\0" + address[1:]

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

        try:
            sock.connect(address)
            sock.sendall(message.encode("utf-8"))
            return True
        except OSError:
            return False
        finally:
            sock.close()


class ApplicationWatchdog(QObject):
    """Heartbeat systemd from the MAIN Qt event loop."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.notifier = SystemdNotifier()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._heartbeat)

    @property
    def active(self):
        return self.notifier.available

    def start(self):
        if not self.notifier.available:
            print(
                "systemd application watchdog inactive "
                "(program was not started by a notify-capable service)."
            )
            return

        interval_ms = self._resolve_interval_ms()
        self.notifier.notify(
            "READY=1\n"
            "STATUS=Stem conveyor GUI ready; watchdog heartbeat active"
        )
        self.notifier.notify("WATCHDOG=1")
        self.timer.start(interval_ms)
        print(f"systemd application watchdog active: {interval_ms} ms heartbeat")

    def stop(self):
        self.timer.stop()

        if self.notifier.available:
            self.notifier.notify("STOPPING=1")

    def set_status(self, text: str):
        if self.notifier.available:
            self.notifier.notify(f"STATUS={text}")

    def _heartbeat(self):
        # Because this QTimer is serviced by Qt's main event loop, a frozen
        # GUI automatically stops the heartbeat.
        self.notifier.notify("WATCHDOG=1")

    @staticmethod
    def _resolve_interval_ms():
        requested = max(250, int(APP_WATCHDOG_HEARTBEAT_MS))

        try:
            watchdog_usec = int(os.environ.get("WATCHDOG_USEC", "0"))
        except ValueError:
            watchdog_usec = 0

        if watchdog_usec <= 0:
            return requested

        # Ping comfortably faster than half the configured service timeout.
        safe_interval = max(250, int((watchdog_usec / 1000) / 3))
        return min(requested, safe_interval)
