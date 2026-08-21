import json
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from config import (
    CAMERA_CAPTURE_TIMEOUT_SEC,
    CAMERA_KILL_GRACE_MS,
    CAMERA_RESET_RESTART_DELAY_MS,
)


class Camera(QObject):
    """Persistent, isolated, timeout-protected camera interface.

    ``camera_worker.py`` owns Picamera2 in a separate process. The worker is
    started once and normally remains alive for repeated fast captures.

    If a camera/libcamera call hangs, the GUI stays responsive because the
    blocking call is isolated in the worker process. RESET SYSTEM can kill and
    recreate that worker without rebooting the Raspberry Pi.
    """

    capture_completed = Signal(bool, str, str)
    ready_changed = Signal(bool, str)
    reset_completed = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.process = None
        self.save_path = None

        self._ready = False
        self._faulted = False
        self._capturing = False
        self._timed_out = False
        self._cancelled = False

        self._reset_in_progress = False
        self._stopping_for_reset = False

        self._stderr_tail = ""
        self._stdout_buffer = ""
        self._orphaned_processes = []

        # Hard timeout for one image capture.
        self.timeout_timer = QTimer(self)
        self.timeout_timer.setSingleShot(True)
        self.timeout_timer.timeout.connect(self._on_timeout)

        # If a timed-out worker does not disappear promptly, detach it so the
        # GUI never waits indefinitely for process teardown.
        self.kill_grace_timer = QTimer(self)
        self.kill_grace_timer.setSingleShot(True)
        self.kill_grace_timer.timeout.connect(self._finalize_stuck_timeout)

        # RESET SYSTEM uses its own kill/restart timers.
        self.reset_kill_timer = QTimer(self)
        self.reset_kill_timer.setSingleShot(True)
        self.reset_kill_timer.timeout.connect(self._finalize_reset_kill)

        self.reset_restart_timer = QTimer(self)
        self.reset_restart_timer.setSingleShot(True)
        self.reset_restart_timer.timeout.connect(self._restart_after_reset)

        self.worker_script = Path(__file__).with_name("camera_worker.py")
        self._start_worker()

    # ========================================================
    # STATE
    # ========================================================

    @property
    def ready(self):
        return (
            self._ready
            and not self._faulted
            and not self._reset_in_progress
            and self.process is not None
        )

    @property
    def busy(self):
        return self._capturing

    @property
    def faulted(self):
        return self._faulted

    @property
    def resetting(self):
        return self._reset_in_progress

    # ========================================================
    # WORKER START / EVENTS
    # ========================================================

    def _start_worker(self):
        if self.process is not None:
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
            self._faulted = False
            self.ready_changed.emit(True, "Camera ready.")

            if self._reset_in_progress:
                self._reset_in_progress = False
                self._stopping_for_reset = False
                self.reset_completed.emit(True, "Camera restarted successfully.")

            return

        if kind == "fatal":
            self._set_fault(
                event.get("message") or "Camera initialization failed."
            )
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

    # ========================================================
    # CAPTURE
    # ========================================================

    def capture_async(self, save_path: Path):
        if not self.ready or self.busy:
            return False

        self.save_path = Path(save_path)
        self.save_path.parent.mkdir(parents=True, exist_ok=True)

        self._capturing = True
        self._timed_out = False
        self._cancelled = False

        command = json.dumps(
            {
                "cmd": "capture",
                "path": str(self.save_path),
            }
        ) + "\n"

        written = self.process.write(command.encode("utf-8"))

        if written < 0:
            self._capturing = False
            self.save_path = None
            return False

        self.timeout_timer.start(
            int(CAMERA_CAPTURE_TIMEOUT_SEC * 1000)
        )
        return True

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
            f"Camera capture timed out after "
            f"{CAMERA_CAPTURE_TIMEOUT_SEC:.0f} seconds. "
            "The belt remains stopped. Check the camera connection, then "
            "press RESET SYSTEM to restart the camera subsystem.",
        )

        self.process.kill()
        self.kill_grace_timer.start(CAMERA_KILL_GRACE_MS)

    def cancel_capture(self):
        """Cancel an in-progress capture without blocking the GUI.

        A forced cancellation kills the worker and intentionally leaves the
        camera faulted. The conveyor stays OFF. RESET SYSTEM can then recreate
        the camera worker cleanly.
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

        self.ready_changed.emit(
            False,
            "Camera capture was cancelled. Press RESET SYSTEM before continuing.",
        )

        if self.process is not None:
            self.process.kill()

    # ========================================================
    # SOFTWARE RESET / RECOVERY
    # ========================================================

    def reset(self):
        """Restart only the isolated camera subsystem.

        This method is intentionally non-blocking. It kills the current worker,
        waits briefly for the CSI/libcamera resources to be released, then
        starts a fresh worker. It never starts the conveyor.
        """
        if self._reset_in_progress:
            return False

        self.timeout_timer.stop()
        self.kill_grace_timer.stop()
        self.reset_kill_timer.stop()
        self.reset_restart_timer.stop()

        self._remove_partial_file(self.save_path)
        self.save_path = None

        # Ignore any result from a capture that was active before reset.
        self._capturing = False
        self._cancelled = False
        self._timed_out = False

        self._ready = False
        self._faulted = False
        self._reset_in_progress = True
        self._stopping_for_reset = False

        self.ready_changed.emit(False, "Camera restarting...")

        if self.process is None:
            self.reset_restart_timer.start(
                CAMERA_RESET_RESTART_DELAY_MS
            )
            return True

        self._stopping_for_reset = True

        try:
            self.process.kill()
        except Exception:
            pass

        self.reset_kill_timer.start(CAMERA_KILL_GRACE_MS)
        return True

    def _finalize_reset_kill(self):
        """Do not let RESET wait forever for a broken worker to exit."""
        if not self._reset_in_progress:
            return

        if self.process is not None:
            process = self.process
            self._detach_process(process)
            self.process = None

        self._stopping_for_reset = False
        self.reset_restart_timer.start(
            CAMERA_RESET_RESTART_DELAY_MS
        )

    def _restart_after_reset(self):
        if not self._reset_in_progress:
            return

        # If a process unexpectedly still exists, do not create two camera
        # owners. Wait for its normal finished callback/reset kill timer.
        if self.process is not None:
            return

        self._faulted = False
        self._ready = False
        self._stopping_for_reset = False
        self._start_worker()

    # ========================================================
    # PROCESS TERMINATION / FAULTS
    # ========================================================

    def _finalize_stuck_timeout(self):
        if self.process is None:
            return

        process = self.process
        self._detach_process(process)
        self.process = None

    def _on_process_error(self, process_error):
        # Killing the old worker is expected during RESET SYSTEM.
        if self._stopping_for_reset:
            return

        if self._faulted:
            return

        self._set_fault(
            f"Camera worker process error: {process_error}"
        )

    def _on_worker_finished(self, exit_code, exit_status):
        self.timeout_timer.stop()
        self.kill_grace_timer.stop()
        self.reset_kill_timer.stop()

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

        # Intentional worker termination during RESET SYSTEM.
        if self._reset_in_progress and self._stopping_for_reset:
            self._stopping_for_reset = False
            self.reset_restart_timer.start(
                CAMERA_RESET_RESTART_DELAY_MS
            )
            return

        if self._cancelled:
            self._cancelled = False
            return

        if self._faulted:
            return

        message = (
            self._stderr_tail.strip()
            or f"Camera worker exited with code {exit_code}."
        )

        self._set_fault(message)

        if was_capturing:
            self._remove_partial_file(save_path)
            self.capture_completed.emit(
                False,
                str(save_path or ""),
                message,
            )

    def _set_fault(self, message):
        message = str(message)
        self._faulted = True
        self._ready = False
        self.ready_changed.emit(False, message)

        if self._reset_in_progress:
            self._reset_in_progress = False
            self._stopping_for_reset = False
            self.reset_restart_timer.stop()
            self.reset_kill_timer.stop()
            self.reset_completed.emit(False, message)

    @staticmethod
    def _remove_partial_file(save_path):
        if not save_path:
            return

        save_path = Path(save_path)
        temporary_path = save_path.with_name(
            f".{save_path.stem}.partial{save_path.suffix}"
        )

        for path in (save_path, temporary_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

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

    # ========================================================
    # SHUTDOWN
    # ========================================================

    def close(self):
        self.timeout_timer.stop()
        self.kill_grace_timer.stop()
        self.reset_kill_timer.stop()
        self.reset_restart_timer.stop()

        self._reset_in_progress = False
        self._stopping_for_reset = False

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
