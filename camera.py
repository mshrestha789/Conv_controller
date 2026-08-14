import json
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from config import CAMERA_CAPTURE_TIMEOUT_SEC, CAMERA_KILL_GRACE_MS


class Camera(QObject):
    """Persistent, non-blocking, timeout-protected camera interface.

    camera_worker.py is started once when the GUI starts. Picamera2 remains
    initialized in that separate process, so repeated captures are fast. If a
    capture hangs, this process can still be killed without freezing the GUI.
    """

    capture_completed = Signal(bool, str, str)
    ready_changed = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.process = None
        self.save_path = None
        self._ready = False
        self._faulted = False
        self._capturing = False
        self._timed_out = False
        self._cancelled = False
        self._stderr_tail = ""
        self._stdout_buffer = ""
        self._orphaned_processes = []

        self.timeout_timer = QTimer(self)
        self.timeout_timer.setSingleShot(True)
        self.timeout_timer.timeout.connect(self._on_timeout)

        self.kill_grace_timer = QTimer(self)
        self.kill_grace_timer.setSingleShot(True)
        self.kill_grace_timer.timeout.connect(self._finalize_stuck_timeout)

        self.worker_script = Path(__file__).with_name("camera_worker.py")
        self._start_worker()

    @property
    def ready(self):
        return self._ready and not self._faulted and self.process is not None

    @property
    def busy(self):
        return self._capturing

    @property
    def faulted(self):
        return self._faulted

    def _start_worker(self):
        if self.process is not None or self._faulted:
            return

        if not self.worker_script.exists():
            self._set_fault("camera_worker.py is missing.")
            return

        process = QProcess(self)
        self.process = process
        self._ready = False
        self._stderr_tail = ""
        self._stdout_buffer = ""

        process.setProgram(sys.executable)
        process.setArguments([str(self.worker_script)])

        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.errorOccurred.connect(self._on_process_error)
        process.finished.connect(self._on_worker_finished)
        process.start()

    def capture_async(self, save_path: Path):
        if not self.ready or self.busy:
            return False

        self.save_path = Path(save_path)
        self.save_path.parent.mkdir(parents=True, exist_ok=True)

        self._capturing = True
        self._timed_out = False
        self._cancelled = False

        command = json.dumps({
            "cmd": "capture",
            "path": str(self.save_path),
        }) + "\n"

        written = self.process.write(command.encode("utf-8"))
        if written < 0:
            self._capturing = False
            self.save_path = None
            return False

        self.timeout_timer.start(int(CAMERA_CAPTURE_TIMEOUT_SEC * 1000))
        return True

    def _read_stdout(self):
        if self.process is None:
            return

        self._stdout_buffer += bytes(
            self.process.readAllStandardOutput()
        ).decode(errors="replace")

        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            self._handle_worker_event(event)

    def _read_stderr(self):
        if self.process is None:
            return

        text = bytes(self.process.readAllStandardError()).decode(
            errors="replace"
        )
        if text:
            self._stderr_tail = (self._stderr_tail + text)[-5000:]

    def _handle_worker_event(self, event):
        kind = event.get("event")

        if kind == "ready":
            self._ready = True
            self.ready_changed.emit(True, "Camera ready.")
            return

        if kind == "fatal":
            self._set_fault(event.get("message") or "Camera initialization failed.")
            return

        if kind == "capture":
            if not self._capturing:
                return

            self.timeout_timer.stop()
            self._capturing = False

            save_path = Path(event.get("path") or self.save_path or "")
            self.save_path = None

            if self._cancelled:
                self._cancelled = False
                return

            success = bool(event.get("success"))
            message = event.get("message") or ""

            if success:
                self.capture_completed.emit(True, str(save_path), message)
            else:
                self._remove_partial_file(save_path)
                self.capture_completed.emit(False, str(save_path), message)
            return

    def _on_timeout(self):
        if self.process is None or not self._capturing:
            return

        self._timed_out = True
        self._capturing = False
        self._faulted = True
        self._ready = False

        save_path = self.save_path
        self.save_path = None
        self._remove_partial_file(save_path)

        self.ready_changed.emit(False, "Camera timed out.")
        self.capture_completed.emit(
            False,
            str(save_path or ""),
            f"Camera capture timed out after {CAMERA_CAPTURE_TIMEOUT_SEC:.0f} seconds. "
            "The belt remains stopped. Check the CSI cable and reboot the Pi "
            "before continuing.",
        )

        self.process.kill()
        self.kill_grace_timer.start(CAMERA_KILL_GRACE_MS)

    def _finalize_stuck_timeout(self):
        if self.process is None:
            return

        process = self.process
        self._detach_process(process)
        self.process = None

    def _on_process_error(self, process_error):
        if self._faulted:
            return
        self._set_fault(f"Camera worker process error: {process_error}")

    def _on_worker_finished(self, exit_code, exit_status):
        self.timeout_timer.stop()
        self.kill_grace_timer.stop()

        process = self.process
        if process is not None:
            try:
                process.deleteLater()
            except Exception:
                pass

        self.process = None
        was_capturing = self._capturing
        save_path = self.save_path
        self._capturing = False
        self.save_path = None
        self._ready = False

        if self._cancelled:
            self._cancelled = False
            return

        if self._faulted:
            return

        message = self._stderr_tail.strip() or f"Camera worker exited with code {exit_code}."
        self._faulted = True
        self.ready_changed.emit(False, message)

        if was_capturing:
            self._remove_partial_file(save_path)
            self.capture_completed.emit(False, str(save_path or ""), message)

    def _set_fault(self, message):
        self._faulted = True
        self._ready = False
        self.ready_changed.emit(False, str(message))

    @staticmethod
    def _remove_partial_file(save_path):
        if not save_path:
            return
        try:
            Path(save_path).unlink(missing_ok=True)
        except OSError:
            pass

    def cancel_capture(self):
        """Cancel safely without blocking the GUI.

        If a capture is in progress, the persistent worker must be killed.
        Camera use is then locked for this application run. This is fail-closed
        behavior; STOP remains immediate and the belt stays OFF.
        """
        self.timeout_timer.stop()
        self.kill_grace_timer.stop()

        if not self._capturing:
            return

        self._cancelled = True
        self._capturing = False
        self._faulted = True
        self._ready = False
        self._remove_partial_file(self.save_path)
        self.save_path = None
        self.ready_changed.emit(False, "Camera capture was cancelled.")

        if self.process is not None:
            self.process.kill()

    def _detach_process(self, process):
        try:
            process.readyReadStandardOutput.disconnect(self._read_stdout)
        except Exception:
            pass
        try:
            process.readyReadStandardError.disconnect(self._read_stderr)
        except Exception:
            pass
        try:
            process.errorOccurred.disconnect(self._on_process_error)
        except Exception:
            pass
        try:
            process.finished.disconnect(self._on_worker_finished)
        except Exception:
            pass

        process.finished.connect(
            lambda *_args, p=process: self._cleanup_orphan(p)
        )
        self._orphaned_processes.append(process)

    def _cleanup_orphan(self, process):
        try:
            self._orphaned_processes.remove(process)
        except ValueError:
            pass
        process.deleteLater()

    def close(self):
        self.timeout_timer.stop()
        self.kill_grace_timer.stop()

        if self.process is not None:
            try:
                if self.ready and not self.busy:
                    command = json.dumps({"cmd": "shutdown"}) + "\n"
                    self.process.write(command.encode("utf-8"))
                else:
                    self.process.kill()
            except Exception:
                pass

        for process in list(self._orphaned_processes):
            try:
                process.kill()
            except Exception:
                pass

        print("Camera worker stopped.")
