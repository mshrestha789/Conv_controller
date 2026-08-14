import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from config import (
    CAMERA_CAPTURE_TIMEOUT_SEC,
    CAMERA_KILL_GRACE_MS,
)


class Camera(QObject):
    """Non-blocking, timeout-protected camera interface.

    Every capture runs in camera_worker.py. If the camera stack hangs, the
    worker is killed and the GUI is allowed to fail closed with the belt OFF.
    """

    capture_completed = Signal(bool, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.process = None
        self.save_path = None
        self._cancelled = False
        self._timed_out = False
        self._completion_emitted = False
        self._process_error_text = ""
        self._faulted = False
        self._orphaned_processes = []

        self.timeout_timer = QTimer(self)
        self.timeout_timer.setSingleShot(True)
        self.timeout_timer.timeout.connect(self._on_timeout)

        self.kill_grace_timer = QTimer(self)
        self.kill_grace_timer.setSingleShot(True)
        self.kill_grace_timer.timeout.connect(self._finalize_stuck_timeout)

        self.worker_script = Path(__file__).with_name("camera_worker.py")

    @property
    def busy(self):
        return self.process is not None

    @property
    def faulted(self):
        return self._faulted

    def capture_async(self, save_path: Path):
        """Launch a capture and return immediately.

        Completion is reported through:
            capture_completed(success, path, message)
        """
        if self.busy or self._faulted:
            return False

        if not self.worker_script.exists():
            self.capture_completed.emit(
                False,
                str(save_path),
                "camera_worker.py is missing.",
            )
            return False

        self.save_path = Path(save_path)
        self.save_path.parent.mkdir(parents=True, exist_ok=True)

        self._cancelled = False
        self._timed_out = False
        self._completion_emitted = False
        self._process_error_text = ""

        process = QProcess(self)
        self.process = process

        process.setProgram(sys.executable)
        process.setArguments([
            str(self.worker_script),
            str(self.save_path),
        ])

        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_process_error)
        process.start()

        self.timeout_timer.start(
            int(CAMERA_CAPTURE_TIMEOUT_SEC * 1000)
        )

        return True

    def _on_process_error(self, process_error):
        self._process_error_text = (
            f"Camera worker process error: {process_error}"
        )

    def _on_timeout(self):
        if self.process is None:
            return

        self._timed_out = True
        self._faulted = True

        # Never wait in the GUI thread for a broken camera process.
        self.process.kill()
        self.kill_grace_timer.start(CAMERA_KILL_GRACE_MS)

    def _on_finished(self, exit_code, exit_status):
        self.timeout_timer.stop()
        self.kill_grace_timer.stop()

        process = self.process
        save_path = self.save_path

        stdout_text = ""
        stderr_text = ""

        if process is not None:
            stdout_text = bytes(process.readAllStandardOutput()).decode(
                errors="replace"
            ).strip()
            stderr_text = bytes(process.readAllStandardError()).decode(
                errors="replace"
            ).strip()
            process.deleteLater()

        self.process = None
        self.save_path = None

        if self._cancelled:
            self._cancelled = False
            self._timed_out = False
            return

        if save_path is None or self._completion_emitted:
            return

        if self._timed_out:
            self._timed_out = False
            self._remove_partial_file(save_path)
            self._emit_once(
                False,
                save_path,
                f"Camera capture timed out after "
                f"{CAMERA_CAPTURE_TIMEOUT_SEC:.0f} seconds. "
                "The camera interface is locked for this application run. "
                "The belt will remain stopped; restart the application or "
                "reboot the Pi after checking the camera connection.",
            )
            return

        success = (
            exit_code == 0
            and save_path.exists()
            and save_path.stat().st_size > 0
        )

        if success:
            self._emit_once(True, save_path, stdout_text)
            return

        self._remove_partial_file(save_path)
        message = stderr_text or self._process_error_text

        if not message:
            message = f"Camera worker exited with code {exit_code}."

        self._emit_once(False, save_path, message)

    def _finalize_stuck_timeout(self):
        """Return control even if SIGKILL cannot immediately reap the worker.

        A process stuck in a kernel driver can sometimes remain present after
        kill() is requested. The GUI must still become usable and keep the belt
        OFF, so the stuck child is detached and camera use is locked out.
        """
        if self.process is None or self._completion_emitted:
            return

        process = self.process
        save_path = self.save_path

        try:
            process.finished.disconnect(self._on_finished)
        except Exception:
            pass

        try:
            process.errorOccurred.disconnect(self._on_process_error)
        except Exception:
            pass

        process.finished.connect(
            lambda *_args, p=process: self._cleanup_orphan(p)
        )

        self._orphaned_processes.append(process)
        self.process = None
        self.save_path = None

        if save_path is not None:
            self._remove_partial_file(save_path)
            self._emit_once(
                False,
                save_path,
                f"Camera capture timed out after "
                f"{CAMERA_CAPTURE_TIMEOUT_SEC:.0f} seconds and the camera "
                "worker did not exit promptly. The belt remains stopped. "
                "Check the CSI connection and reboot the Pi before continuing.",
            )

    def _cleanup_orphan(self, process):
        try:
            self._orphaned_processes.remove(process)
        except ValueError:
            pass

        process.deleteLater()

    def _emit_once(self, success, save_path, message):
        if self._completion_emitted:
            return

        self._completion_emitted = True
        self.capture_completed.emit(
            bool(success),
            str(save_path),
            str(message or ""),
        )

    @staticmethod
    def _remove_partial_file(save_path):
        try:
            Path(save_path).unlink(missing_ok=True)
        except OSError:
            pass

    def cancel_capture(self):
        """Cancel a capture without blocking STOP/Exit."""
        self.timeout_timer.stop()
        self.kill_grace_timer.stop()

        process = self.process
        self.process = None
        self.save_path = None

        if process is None:
            return

        self._cancelled = True

        try:
            process.finished.disconnect(self._on_finished)
        except Exception:
            pass

        try:
            process.errorOccurred.disconnect(self._on_process_error)
        except Exception:
            pass

        process.finished.connect(
            lambda *_args, p=process: self._cleanup_orphan(p)
        )

        self._orphaned_processes.append(process)
        process.kill()

    def close(self):
        self.cancel_capture()

        for process in list(self._orphaned_processes):
            try:
                process.kill()
            except Exception:
                pass

        print("Camera worker stopped.")
