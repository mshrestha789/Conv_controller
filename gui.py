import json
from pathlib import Path
import sys
import time

from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QWidget,
    QLabel,
    QLineEdit,
    QPushButton,
    QGridLayout,
    QVBoxLayout,
    QHBoxLayout,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QFrame,
    QStackedWidget,
    QSizePolicy,
)

from config import (
    APP_TITLE,
    BARCODE_DUPLICATE_WINDOW_SEC,
    DEFAULT_DIRECTION,
    DIRECTION_CHANGE_DEAD_TIME_MS,
    SENSOR_TO_STOP_DELAY_SEC,
    BELT_SETTLE_DELAY_SEC,
    POST_CAPTURE_DELAY_SEC,
    AUTO_CAPTURE_FORWARD,
    AUTO_CAPTURE_REVERSE,
    SENSOR_POLL_MS,
    STATUS_UPDATE_MS,
    SENSOR_STUCK_ACTIVE_TIMEOUT_SEC,
    NO_DETECTION_TIMEOUT_SEC,
)

from hardware import Hardware
from camera import Camera
from storage import StorageError, StorageManager


class ExpectedSamplesDialog(QDialog):
    """Touch-only expected-sample entry with safe scanner behavior."""

    MAX_EXPECTED_SAMPLES = 10000

    def __init__(self, parent, current_value, captured_count):
        super().__init__(parent)
        self.setWindowTitle("Expected Samples")
        self.setModal(True)
        self.selected_value = None
        self.captured_count = max(0, int(captured_count))

        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(
                min(470, max(1, available.width() - 60)),
                min(570, max(1, available.height() - 50)),
            )
        else:
            self.resize(440, 550)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        title = QLabel("EXPECTED SAMPLES (OPTIONAL)")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: 800;")
        root.addWidget(title)

        explanation = QLabel(
            f"Photos already saved: {self.captured_count}\n"
            "Enter the total samples for this barcode, or choose NOT SET."
        )
        explanation.setAlignment(Qt.AlignCenter)
        explanation.setWordWrap(True)
        explanation.setStyleSheet("font-size: 16px;")
        root.addWidget(explanation)

        self.value_edit = QLineEdit()
        self.value_edit.setAlignment(Qt.AlignCenter)
        self.value_edit.setMaxLength(5)
        self.value_edit.setInputMethodHints(
            Qt.ImhDigitsOnly | Qt.ImhNoPredictiveText
        )
        self.value_edit.setMinimumHeight(52)
        self.value_edit.setStyleSheet("font-size: 26px; font-weight: 800;")
        if current_value > 0:
            self.value_edit.setText(str(current_value))
        self.value_edit.textChanged.connect(self._sanitize_value)
        root.addWidget(self.value_edit)

        self.validation_label = QLabel("")
        self.validation_label.setAlignment(Qt.AlignCenter)
        self.validation_label.setStyleSheet(
            "color: #c44949; font-size: 14px; font-weight: 700;"
        )
        root.addWidget(self.validation_label)

        presets = QHBoxLayout()
        presets.setSpacing(8)
        not_set_button = self._touch_button("NOT SET")
        not_set_button.clicked.connect(self._choose_not_set)
        presets.addWidget(not_set_button)
        for value in (10, 50, 100):
            button = self._touch_button(str(value))
            button.clicked.connect(
                lambda checked=False, selected=value: self.value_edit.setText(
                    str(selected)
                )
            )
            button.setEnabled(value >= self.captured_count)
            presets.addWidget(button)
        root.addLayout(presets)

        keypad = QGridLayout()
        keypad.setSpacing(8)
        for index, digit in enumerate("123456789"):
            button = self._touch_button(digit)
            button.clicked.connect(
                lambda checked=False, selected=digit: self._append_digit(
                    selected
                )
            )
            keypad.addWidget(button, index // 3, index % 3)

        clear_button = self._touch_button("CLEAR")
        clear_button.clicked.connect(self.value_edit.clear)
        keypad.addWidget(clear_button, 3, 0)
        zero_button = self._touch_button("0")
        zero_button.clicked.connect(lambda checked=False: self._append_digit("0"))
        keypad.addWidget(zero_button, 3, 1)
        backspace_button = self._touch_button("⌫")
        backspace_button.clicked.connect(self.value_edit.backspace)
        keypad.addWidget(backspace_button, 3, 2)
        root.addLayout(keypad, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        self.save_button = buttons.button(QDialogButtonBox.Save)
        self.save_button.setText("SAVE EXPECTED COUNT")
        self.save_button.setMinimumHeight(48)
        self.save_button.setAutoDefault(False)
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        cancel_button.setMinimumHeight(48)
        cancel_button.setDefault(True)
        buttons.accepted.connect(self._save_value)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        # A barcode scanner also sends Enter. Enter intentionally activates
        # CANCEL, never SAVE, while this modal dialog is open.
        self._sanitize_value(self.value_edit.text())
        self.value_edit.setFocus()

    @staticmethod
    def _touch_button(text):
        button = QPushButton(text)
        button.setMinimumHeight(52)
        button.setFocusPolicy(Qt.NoFocus)
        button.setStyleSheet("font-size: 18px; font-weight: 750;")
        return button

    def _append_digit(self, digit):
        if len(self.value_edit.text()) < 5:
            self.value_edit.insert(digit)

    def _sanitize_value(self, text):
        clean = "".join(character for character in text if character.isdigit())
        clean = clean[:5]
        if clean != text:
            self.value_edit.setText(clean)
            return
        value = int(clean or 0)
        minimum = max(1, self.captured_count)
        self.save_button.setEnabled(
            minimum <= value <= self.MAX_EXPECTED_SAMPLES
        )
        if clean and value < self.captured_count:
            self.validation_label.setText(
                f"Total cannot be less than {self.captured_count} saved photos."
            )
        elif value > self.MAX_EXPECTED_SAMPLES:
            self.validation_label.setText(
                f"Maximum allowed total is {self.MAX_EXPECTED_SAMPLES}."
            )
        else:
            self.validation_label.clear()

    def _choose_not_set(self):
        self.selected_value = 0
        self.accept()

    def _save_value(self):
        value = int(self.value_edit.text() or 0)
        if max(1, self.captured_count) <= value <= self.MAX_EXPECTED_SAMPLES:
            self.selected_value = value
            self.accept()


class BatchHistoryDialog(QDialog):
    """Browse, export, and explicitly delete finalized batch photos."""

    def __init__(self, parent):
        super().__init__(parent)
        self.controller = parent
        self.storage = parent.storage
        self.sessions = []
        self.selected_manifest_path = None
        self.selected_summary = None

        self.setWindowTitle("Batch History")
        self.setModal(True)
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(
                min(980, max(1, available.width() - 30)),
                min(570, max(1, available.height() - 30)),
            )
        else:
            self.resize(940, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title = QLabel("BATCH HISTORY")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: 800;")
        root.addWidget(title)

        content = QHBoxLayout()
        content.setSpacing(10)

        batch_column = QVBoxLayout()
        batch_label = QLabel("Batches")
        batch_label.setStyleSheet("font-size: 16px; font-weight: 800;")
        self.batch_list = QListWidget()
        self.batch_list.setMinimumWidth(285)
        self.batch_list.currentItemChanged.connect(
            self._on_batch_selected
        )
        batch_column.addWidget(batch_label)
        batch_column.addWidget(self.batch_list, 1)
        content.addLayout(batch_column, 2)

        photo_column = QVBoxLayout()
        photo_label = QLabel("Saved photos")
        photo_label.setStyleSheet("font-size: 16px; font-weight: 800;")
        self.photo_list = QListWidget()
        self.photo_list.setMinimumWidth(270)
        self.photo_list.currentItemChanged.connect(
            self._on_photo_selected
        )
        photo_column.addWidget(photo_label)
        photo_column.addWidget(self.photo_list, 1)
        content.addLayout(photo_column, 2)

        detail_column = QVBoxLayout()
        self.details_label = QLabel("Select a batch.")
        self.details_label.setWordWrap(True)
        self.details_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.details_label.setStyleSheet(
            "font-size: 14px; background: #eef2f7; padding: 8px;"
        )
        self.history_preview = QLabel("No photo selected")
        self.history_preview.setAlignment(Qt.AlignCenter)
        self.history_preview.setMinimumSize(280, 230)
        self.history_preview.setStyleSheet(
            "background: #182033; color: #dce6ff; border-radius: 8px;"
        )
        detail_column.addWidget(self.details_label)
        detail_column.addWidget(self.history_preview, 1)
        content.addLayout(detail_column, 3)
        root.addLayout(content, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.refresh_button = QPushButton("REFRESH")
        self.export_button = QPushButton("SAVE BATCH TO USB")
        self.delete_button = QPushButton("DELETE SELECTED PHOTO")
        close_button = QPushButton("CLOSE")
        for button in (
            self.refresh_button,
            self.export_button,
            self.delete_button,
            close_button,
        ):
            button.setMinimumHeight(48)
            button.setAutoDefault(False)
            button.setFocusPolicy(Qt.NoFocus)
            actions.addWidget(button)
        close_button.setDefault(True)
        self.refresh_button.clicked.connect(self.refresh_sessions)
        self.export_button.clicked.connect(self._export_selected_batch)
        self.delete_button.clicked.connect(self._delete_selected_photo)
        close_button.clicked.connect(self.accept)
        root.addLayout(actions)

        self.refresh_sessions()

    def refresh_sessions(self):
        preserve = self.selected_manifest_path
        self.sessions = self.storage.list_sessions()
        self.batch_list.clear()

        selected_row = 0
        for row, summary in enumerate(self.sessions):
            created = str(summary.get("created_at") or "").replace("T", " ")
            created = created[:19] or "Unknown date"
            status = str(summary.get("status") or "unknown").upper()
            barcode = summary.get("barcode") or "Unknown barcode"
            count = int(summary.get("actual_count") or 0)
            item = QListWidgetItem(
                f"{barcode}\n{created} • {status} • {count} photos"
            )
            item.setData(
                Qt.UserRole,
                str(summary.get("manifest_path") or ""),
            )
            self.batch_list.addItem(item)
            if preserve and Path(item.data(Qt.UserRole)) == Path(preserve):
                selected_row = row

        if self.sessions:
            self.batch_list.setCurrentRow(selected_row)
        else:
            self.selected_manifest_path = None
            self.selected_summary = None
            self.photo_list.clear()
            self.details_label.setText("No stored batches were found.")
            self.history_preview.setText("No photo selected")
            self.export_button.setEnabled(False)
            self.delete_button.setEnabled(False)

    def _on_batch_selected(self, current, _previous):
        self.photo_list.clear()
        self.history_preview.clear()
        self.history_preview.setText("No photo selected")
        self.delete_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.selected_summary = None
        self.selected_manifest_path = None
        if current is None:
            return

        manifest_path = Path(current.data(Qt.UserRole))
        summary = next(
            (
                item
                for item in self.sessions
                if Path(item.get("manifest_path")) == manifest_path
            ),
            None,
        )
        if summary is None:
            return

        self.selected_summary = summary
        self.selected_manifest_path = manifest_path
        if summary.get("error"):
            self.details_label.setText(
                f"This batch cannot be read.\n\n{summary['error']}"
            )
            return

        try:
            self.storage.get_session_images(manifest_path)
        except StorageError as error:
            self.details_label.setText(str(error))
            return

        for number, image_path in enumerate(images, start=1):
            item = QListWidgetItem(f"{number}. {image_path.name}")
            item.setData(Qt.UserRole, str(image_path))
            self.photo_list.addItem(item)

        expected = int(summary.get("expected_count") or 0)
        expected_text = str(expected) if expected > 0 else "Not set"
        exported = summary.get("last_verified_export_at")
        if exported:
            export_text = str(exported).replace("T", " ")[:19]
            if summary.get("export_needs_refresh"):
                export_text += " (OUTDATED — export again)"
        else:
            export_text = "Not yet exported"
        self.details_label.setText(
            f"Barcode: {summary.get('barcode')}\n"
            f"Status: {str(summary.get('status')).upper()}\n"
            f"Session: {summary.get('session_id')}\n"
            f"Saved photos: {len(images)}\n"
            f"Expected: {expected_text}\n"
            f"Verified USB export: {export_text}"
        )
        # Manifest-only export is allowed so a re-export can remove USB files
        # that were later deleted from the finalized local batch.
        self.export_button.setEnabled(True)

    def _on_photo_selected(self, current, _previous):
        self.delete_button.setEnabled(False)
        if current is None or self.selected_summary is None:
            self.history_preview.clear()
            self.history_preview.setText("No photo selected")
            return

        image_path = Path(current.data(Qt.UserRole))
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            self.history_preview.clear()
            self.history_preview.setText("Photo could not be opened")
        else:
            self.history_preview.setPixmap(
                pixmap.scaled(
                    self.history_preview.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )

        self.delete_button.setEnabled(
            self.selected_summary.get("status") in {"completed", "cancelled"}
        )

    def _export_selected_batch(self):
        if self.selected_manifest_path is None:
            return
        if self.controller.start_usb_export_for_manifest(
            self.selected_manifest_path
        ):
            self.accept()

    def _delete_selected_photo(self):
        current = self.photo_list.currentItem()
        if (
            current is None
            or self.selected_manifest_path is None
            or self.selected_summary is None
        ):
            return

        image_path = Path(current.data(Qt.UserRole))
        barcode = self.selected_summary.get("barcode")
        export_warning = (
            "\n\nA previous USB copy will not be changed. This batch will "
            "be marked as needing another export."
            if self.selected_summary.get("last_verified_export_at")
            else ""
        )
        reply = QMessageBox.question(
            self,
            "Permanently Delete Historical Photo?",
            f"Barcode: {barcode}\n"
            f"Photo: {image_path.name}\n\n"
            "This permanently deletes the local image and records the "
            "deletion in the finalized batch manifest. This cannot be "
            f"undone.{export_warning}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            remaining = self.storage.delete_historical_image(
                self.selected_manifest_path,
                image_path,
            )
        except StorageError as error:
            QMessageBox.critical(
                self,
                "Historical Photo Deletion Failed",
                str(error),
            )
            return

        self.controller._say(
            f"Historical photo deleted. Batch {barcode} now has "
            f"{remaining} saved photos."
        )
        self.refresh_sessions()


class StemConveyorGUI(QWidget):
    """
    Touch-friendly conveyor imaging interface.

    Normal automatic sequence:
        1. Start conveyor.
        2. Sensor detects one stem.
        3. Conveyor keeps moving for SENSOR_TO_STOP_DELAY_SEC.
        4. Conveyor stops.
        5. Wait BELT_SETTLE_DELAY_SEC.
        6. Capture the image while the stem is stationary.
        7. Wait POST_CAPTURE_DELAY_SEC.
        8. Restart conveyor for the next stem.

    The workflow assumes one stem at a time between the sensor
    and camera. Closely spaced stems can invalidate the timing
    because the conveyor stops during each imaging cycle. A separate
    no-detection watchdog stops the belt if automatic operation runs
    too long without seeing a new stem.
    """

    STAGE_IDLE = "idle"
    STAGE_TRAVELING = "traveling"
    STAGE_SETTLING = "settling"
    STAGE_CAPTURING = "capturing"
    STAGE_RESTARTING = "restarting"

    def __init__(self):
        super().__init__()

        # ====================================================
        # MODULES
        # ====================================================

        self.hardware = Hardware()
        self.camera = Camera(self)
        self.camera.capture_completed.connect(
            self._on_camera_capture_completed
        )
        self.camera.ready_changed.connect(
            self._on_camera_ready_changed
        )
        self.camera.reset_completed.connect(
            self._on_camera_reset_completed
        )
        self.storage = StorageManager()

        # Barcode-batch state is persisted by StorageManager. A batch found
        # after restart is locked until the operator explicitly resumes or
        # closes it; the conveyor can never start while recovery is pending.
        self.active_batch_id = None
        if self.storage.active_manifest is not None:
            self.active_batch_id = self.storage.active_manifest.get(
                "barcode"
            )
        self.recovery_pending = self.active_batch_id is not None
        self.storage_fault_message = ""
        self.last_scan_code = None
        self.last_scan_at = 0.0
        self.usb_export_in_progress = False
        self.usb_export_process = None
        self.usb_export_manifest_path = None

        # ====================================================
        # SYSTEM STATE
        # ====================================================

        self.system_running = False
        self.conveyor_running = False

        self.selected_direction = DEFAULT_DIRECTION
        self.direction_change_in_progress = False
        self.pending_direction = None
        self.reset_in_progress = False

        # Rising-edge detection so one stem does not repeatedly
        # trigger while it remains in front of the sensor.
        self.sensor_was_active = False

        # Sensor fault watchdog. A fault is latched if the proximity input
        # stays continuously ACTIVE longer than the configured timeout while
        # the automatic system is operating. RESET SYSTEM clears the latch
        # only after the physical sensor input is CLEAR.
        self.sensor_active_since = None
        self.sensor_fault = False
        self.sensor_fault_kind = None
        self.sensor_fault_message = ""

        # No-detection watchdog. While the belt is moving in a direction where
        # automatic capture is enabled, this tracks how long the system has
        # run without seeing a new stem. It is paused/reset whenever the belt
        # is intentionally stopped or a stem is detected.
        self.no_detection_since = None

        # One automatic/manual capture cycle at a time.
        self.capture_cycle_in_progress = False
        self.capture_stage = self.STAGE_IDLE
        self.capture_source = "auto"
        self.restart_after_capture = False
        self.pending_capture_path = None

        # ====================================================
        # IMAGE STATE
        # ====================================================

        self.image_files = []
        self.current_index = -1
        self.session_photo_count = self.storage.current_photo_count()

        # ====================================================
        # WINDOW
        # ====================================================

        self.setWindowTitle(APP_TITLE)
        screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry() if screen else None
        self.compact_mode = bool(
            screen_geometry
            and (
                screen_geometry.width() <= 1100
                or screen_geometry.height() <= 650
            )
        )

        if self.compact_mode:
            self.resize(
                screen_geometry.width(),
                screen_geometry.height(),
            )
            self.setMinimumSize(800, 480)
        else:
            self.resize(1180, 760)
            self.setMinimumSize(900, 620)

        self._build_ui()
        self._apply_styles()
        self.refresh_image_list()
        self._refresh_batch_ui()

        if self.storage.recovery_error:
            QTimer.singleShot(0, self._show_batch_recovery_error)
        elif self.recovery_pending:
            QTimer.singleShot(0, self._prompt_incomplete_batch_recovery)
        else:
            QTimer.singleShot(0, self._focus_barcode_input)

        # ====================================================
        # SENSOR TIMER
        # ====================================================

        self.sensor_timer = QTimer(self)
        self.sensor_timer.timeout.connect(self.check_sensor)
        self.sensor_timer.start(SENSOR_POLL_MS)

        # ====================================================
        # STATUS TIMER
        # ====================================================

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.update_status_display)
        self.status_timer.start(STATUS_UPDATE_MS)

        # ====================================================
        # DIRECTION-CHANGE TIMER
        # ====================================================

        self.direction_timer = QTimer(self)
        self.direction_timer.setSingleShot(True)
        self.direction_timer.timeout.connect(self._finish_direction_change)

        # ====================================================
        # AUTOMATIC CAPTURE TIMERS
        # ====================================================

        # Sensor detected -> stem travels toward camera.
        self.travel_timer = QTimer(self)
        self.travel_timer.setSingleShot(True)
        self.travel_timer.timeout.connect(self.stop_belt_for_capture)

        # Belt stopped -> wait for vibration/motion to settle.
        self.settle_timer = QTimer(self)
        self.settle_timer.setSingleShot(True)
        self.settle_timer.timeout.connect(self.capture_after_settle)

        # Photo saved -> short pause -> restart conveyor.
        self.restart_timer = QTimer(self)
        self.restart_timer.setSingleShot(True)
        self.restart_timer.timeout.connect(self.restart_after_capture_cycle)

        self.update_status_display()
        self._update_direction_button()

    # ========================================================
    # UI CREATION
    # ========================================================

    def _build_ui(self):
        self._create_ui_widgets()

        if self.compact_mode:
            self._build_compact_ui()
        else:
            self._build_desktop_ui()

        self._connect_ui_signals()

    def _create_ui_widgets(self):
        """Create shared widgets before arranging them for either display."""
        self.header = QLabel("STEM IMAGING STATION")
        self.header.setObjectName("header")
        self.header.setAlignment(Qt.AlignCenter)

        self.subtitle = QLabel(
            "Place one stem at a time on the belt. "
            "The system positions it, stops the belt, takes a photo, "
            "and starts again automatically."
        )
        self.subtitle.setObjectName("subtitle")
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setWordWrap(True)

        self.image_preview = QLabel(
            "No photos yet\n\nPress START BELT to begin."
        )
        self.image_preview.setObjectName("imagePreview")
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.image_preview.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding,
        )

        self.image_list = QListWidget()
        self.image_list.setObjectName("imageList")
        self.image_list.setFocusPolicy(Qt.NoFocus)
        self.image_list.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding,
        )

        if self.compact_mode:
            self.image_preview.setMinimumSize(0, 180)
            self.image_list.setMinimumWidth(0)
        else:
            self.image_preview.setMinimumSize(620, 360)
            self.image_list.setMinimumWidth(300)

        self.batch_value_label = QLabel("NO ACTIVE BATCH")
        self.batch_value_label.setObjectName("batchValue")
        self.batch_value_label.setAlignment(Qt.AlignCenter)
        self.batch_value_label.setMinimumHeight(42)

        self.barcode_edit = QLineEdit()
        self.barcode_edit.setObjectName("barcodeInput")
        self.barcode_edit.setPlaceholderText("Scan batch barcode")
        self.barcode_edit.setMaxLength(128)
        self.barcode_edit.setClearButtonEnabled(True)
        self.barcode_edit.setMinimumHeight(44)
        self.barcode_edit.setToolTip(
            "Present one batch barcode. Scanning never starts the conveyor."
        )

        self.expected_count_button = QPushButton(
            "EXPECTED SAMPLES\nNOT SET"
        )
        self.expected_count_button.setObjectName("expectedCountButton")
        self.expected_count_button.setMinimumWidth(180)
        self.expected_count_button.setMinimumHeight(48)
        self.expected_count_button.setToolTip(
            "Optional total sample count. Tap to enter using the keypad."
        )

        self.btn_complete_batch = QPushButton("COMPLETE BATCH")
        self.btn_complete_batch.setObjectName("completeBatchButton")
        self.btn_complete_batch.setMinimumHeight(44)

        self.btn_cancel_batch = QPushButton("CANCEL BATCH")
        self.btn_cancel_batch.setObjectName("cancelBatchButton")
        self.btn_cancel_batch.setMinimumHeight(44)

        self.btn_start = QPushButton("▶  START BELT")
        self.btn_start.setObjectName("startButton")

        self.btn_stop = QPushButton("■  STOP / PAUSE")
        self.btn_stop.setObjectName("stopButton")

        self.btn_reset = QPushButton("↻  RESET SYSTEM")
        self.btn_reset.setObjectName("resetButton")
        self.btn_reset.setToolTip(
            "Stops the conveyor, clears the current cycle, and restarts the camera."
        )

        self.btn_direction = QPushButton("↔  CHANGE DIRECTION")
        self.btn_direction.setObjectName("directionButton")

        self.btn_manual_capture = QPushButton("CAMERA  TAKE PHOTO")
        self.btn_manual_capture.setObjectName("photoButton")

        self.btn_prev = QPushButton("◀ Previous")
        self.btn_next = QPushButton("Next ▶")
        self.btn_copy_current = QPushButton("Save This to USB")
        self.btn_copy_all = QPushButton("Save Batch to USB")
        self.btn_batch_history = QPushButton("BATCH HISTORY")
        self.btn_batch_history.setToolTip(
            "Browse, export, or delete photos from previous batches."
        )
        self.btn_delete = QPushButton("Delete Photo")
        self.btn_exit = QPushButton("Exit")

        self.btn_open_photos = QPushButton("PHOTOS")
        self.btn_open_photos.setObjectName("photosButton")
        self.btn_open_photos.setToolTip("Open saved-photo management.")

        self.btn_back_to_main = QPushButton("◀  BACK TO CONTROLS")
        self.btn_back_to_main.setObjectName("backButton")

        self.message_label = QLabel(
            "Ready! Press START BELT when everyone is clear of the conveyor."
        )
        self.message_label.setObjectName("messageBanner")
        self.message_label.setAlignment(Qt.AlignCenter)
        self.message_label.setWordWrap(True)

        self.auto_mode_label = QLabel(
            f"Auto mode: detect stem → move {SENSOR_TO_STOP_DELAY_SEC:.1f} s "
            f"→ stop belt → settle {BELT_SETTLE_DELAY_SEC:.1f} s "
            f"→ photo → restart. No detection for "
            f"{NO_DETECTION_TIMEOUT_SEC:.0f} s → belt stops."
        )
        self.auto_mode_label.setObjectName("smallHint")
        self.auto_mode_label.setAlignment(Qt.AlignCenter)

    def _connect_ui_signals(self):
        self.barcode_edit.returnPressed.connect(self.handle_barcode_scan)
        self.expected_count_button.clicked.connect(
            self.open_expected_samples_dialog
        )
        self.btn_complete_batch.clicked.connect(self.complete_batch)
        self.btn_cancel_batch.clicked.connect(self.cancel_batch)

        self.btn_start.clicked.connect(self.start_system)
        self.btn_stop.clicked.connect(self.stop_system)
        self.btn_reset.clicked.connect(self.reset_system)
        self.btn_direction.clicked.connect(self.toggle_direction)
        self.btn_manual_capture.clicked.connect(self.manual_capture)

        self.btn_prev.clicked.connect(self.previous_image)
        self.btn_next.clicked.connect(self.next_image)
        self.btn_copy_current.clicked.connect(self.copy_current_to_usb)
        self.btn_copy_all.clicked.connect(self.copy_all_to_usb)
        self.btn_batch_history.clicked.connect(self.open_batch_history)
        self.btn_delete.clicked.connect(self.delete_current_image)
        self.btn_exit.clicked.connect(self.close)
        self.image_list.itemClicked.connect(self.select_image_from_list)

        self.btn_open_photos.clicked.connect(self._show_photo_page)
        self.btn_back_to_main.clicked.connect(self._show_control_page)

        # The barcode scanner appends Enter. Touching an ordinary control must
        # not move keyboard focus away from the scanner sink, otherwise a late
        # duplicate scan could activate a focused button.
        for button in self.findChildren(QPushButton):
            button.setFocusPolicy(Qt.NoFocus)

    def _build_desktop_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        root.addWidget(self.header)
        root.addWidget(self.subtitle)
        root.addLayout(self._make_status_row(10))
        root.addLayout(self._make_batch_row(10))

        middle = QHBoxLayout()
        middle.setSpacing(14)
        middle.addWidget(self._make_preview_panel(), stretch=3)
        middle.addWidget(self._make_photo_list_panel(), stretch=1)

        root.addLayout(middle, stretch=1)

        control_row = QHBoxLayout()
        control_row.setSpacing(10)
        for button in (
            self.btn_start,
            self.btn_stop,
            self.btn_reset,
            self.btn_direction,
            self.btn_manual_capture,
        ):
            button.setMinimumHeight(66)
            control_row.addWidget(button)

        root.addLayout(control_row)

        photo_row = QHBoxLayout()
        photo_row.setSpacing(8)
        for button in (
            self.btn_prev,
            self.btn_next,
            self.btn_copy_current,
            self.btn_copy_all,
            self.btn_batch_history,
            self.btn_delete,
            self.btn_exit,
        ):
            button.setMinimumHeight(46)
            button.setObjectName("smallButton")
            photo_row.addWidget(button)

        root.addLayout(photo_row)
        root.addWidget(self.message_label)
        root.addWidget(self.auto_mode_label)

    def _build_compact_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        self.header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header_row.addWidget(self.header, stretch=1)

        self.btn_open_photos.setMinimumSize(112, 42)
        header_row.addWidget(self.btn_open_photos)

        self.btn_exit.setObjectName("headerButton")
        self.btn_exit.setMinimumSize(140, 42)
        header_row.addWidget(self.btn_exit)
        root.addLayout(header_row)

        self.subtitle.hide()
        self.auto_mode_label.hide()
        root.addLayout(self._make_status_row(6))
        root.addLayout(self._make_batch_row(6))

        self.compact_stack = QStackedWidget()
        self.compact_stack.setObjectName("compactPages")

        self.control_page = QWidget()
        control_page_layout = QVBoxLayout(self.control_page)
        control_page_layout.setContentsMargins(0, 0, 0, 0)
        control_page_layout.setSpacing(6)
        control_page_layout.addWidget(self._make_preview_panel(), stretch=1)

        control_row = QHBoxLayout()
        control_row.setSpacing(6)
        for button in (
            self.btn_start,
            self.btn_stop,
            self.btn_reset,
            self.btn_direction,
            self.btn_manual_capture,
        ):
            button.setMinimumHeight(54)
            control_row.addWidget(button, stretch=1)
        control_page_layout.addLayout(control_row)

        self.photo_page = QWidget()
        photo_page_layout = QVBoxLayout(self.photo_page)
        photo_page_layout.setContentsMargins(0, 0, 0, 0)
        photo_page_layout.setSpacing(6)

        photo_header = QHBoxLayout()
        photo_title = QLabel("SAVED PHOTOS")
        photo_title.setObjectName("photoPageTitle")
        photo_header.addWidget(photo_title)
        photo_header.addStretch(1)
        self.btn_batch_history.setMinimumSize(170, 42)
        photo_header.addWidget(self.btn_batch_history)
        self.btn_back_to_main.setMinimumSize(190, 42)
        photo_header.addWidget(self.btn_back_to_main)
        photo_page_layout.addLayout(photo_header)
        photo_page_layout.addWidget(self._make_photo_list_panel(), stretch=1)

        photo_actions = QHBoxLayout()
        photo_actions.setSpacing(6)
        for button in (
            self.btn_prev,
            self.btn_next,
            self.btn_copy_current,
            self.btn_copy_all,
            self.btn_delete,
        ):
            button.setMinimumHeight(48)
            button.setObjectName("smallButton")
            photo_actions.addWidget(button, stretch=1)
        photo_page_layout.addLayout(photo_actions)

        self.compact_stack.addWidget(self.control_page)
        self.compact_stack.addWidget(self.photo_page)
        self.compact_stack.setCurrentWidget(self.control_page)
        root.addWidget(self.compact_stack, stretch=1)
        root.addWidget(self.message_label)

    def _make_status_row(self, spacing):
        status_row = QHBoxLayout()
        status_row.setSpacing(spacing)

        self.system_value = self._make_status_card(
            status_row, "SYSTEM", "READY"
        )
        self.belt_value = self._make_status_card(
            status_row, "BELT", "STOPPED"
        )
        self.sensor_value = self._make_status_card(
            status_row, "SENSOR", "CLEAR"
        )
        self.photos_value = self._make_status_card(
            status_row, "PHOTOS", "0"
        )
        self.usb_value = self._make_status_card(
            status_row, "USB", "NOT FOUND"
        )
        return status_row

    def _make_batch_row(self, spacing):
        batch_row = QHBoxLayout()
        batch_row.setSpacing(spacing)

        title = QLabel("BATCH")
        title.setObjectName("batchTitle")
        batch_row.addWidget(title)
        batch_row.addWidget(self.batch_value_label, stretch=2)
        batch_row.addWidget(self.barcode_edit, stretch=2)
        batch_row.addWidget(self.expected_count_button)
        batch_row.addWidget(self.btn_complete_batch)
        batch_row.addWidget(self.btn_cancel_batch)
        return batch_row

    def _make_preview_panel(self):
        preview_panel = QFrame()
        preview_panel.setObjectName("panel")
        preview_layout = QVBoxLayout(preview_panel)

        if self.compact_mode:
            preview_layout.setContentsMargins(8, 5, 8, 8)
            preview_layout.setSpacing(4)

        preview_title = QLabel("Latest Photo")
        preview_title.setObjectName("sectionTitle")
        preview_layout.addWidget(preview_title)
        preview_layout.addWidget(self.image_preview, stretch=1)
        return preview_panel

    def _make_photo_list_panel(self):
        list_panel = QFrame()
        list_panel.setObjectName("panel")
        list_layout = QVBoxLayout(list_panel)

        if self.compact_mode:
            list_layout.setContentsMargins(8, 5, 8, 8)
            list_layout.setSpacing(4)

        list_title = QLabel("Saved Photos")
        list_title.setObjectName("sectionTitle")
        list_hint = QLabel(
            "Tap a photo to select it. Return to Controls to view it."
            if self.compact_mode
            else "Tap a photo to view it."
        )
        list_hint.setObjectName("smallHint")
        list_layout.addWidget(list_title)
        list_layout.addWidget(list_hint)
        list_layout.addWidget(self.image_list, stretch=1)
        return list_panel

    def _show_photo_page(self):
        if not self.compact_mode:
            return

        self.refresh_image_list()
        self.compact_stack.setCurrentWidget(self.photo_page)
        self.btn_open_photos.setEnabled(False)

    def _show_control_page(self):
        if not self.compact_mode:
            return

        self.compact_stack.setCurrentWidget(self.control_page)
        self.btn_open_photos.setEnabled(True)
        self.show_current_image()

    def open_batch_history(self):
        if self.usb_export_in_progress:
            self._say("Wait for the USB export to finish.")
            return
        if (
            self.conveyor_running
            or self.capture_cycle_in_progress
            or self.direction_change_in_progress
            or self.reset_in_progress
        ):
            self._say(
                "Pause the belt and wait for the current operation before "
                "opening Batch History."
            )
            return

        dialog = BatchHistoryDialog(self)
        dialog.exec()
        self.refresh_image_list()
        self.update_status_display()
        QTimer.singleShot(0, self._focus_barcode_input)

    def _make_status_card(self, layout, title, value):
        card = QFrame()
        card.setObjectName("statusCard")

        card_layout = QVBoxLayout(card)
        if self.compact_mode:
            card_layout.setContentsMargins(6, 3, 6, 3)
            card_layout.setSpacing(0)
        else:
            card_layout.setContentsMargins(8, 7, 8, 7)
            card_layout.setSpacing(2)

        title_label = QLabel(title)
        title_label.setObjectName("statusTitle")
        title_label.setAlignment(Qt.AlignCenter)

        value_label = QLabel(value)
        value_label.setObjectName("statusValue")
        value_label.setAlignment(Qt.AlignCenter)

        card_layout.addWidget(title_label)
        card_layout.addWidget(value_label)

        layout.addWidget(card)

        return value_label

    # ========================================================
    # STYLE
    # ========================================================

    def _apply_styles(self):
        self.setStyleSheet(
            """
            QWidget {
                background-color: #f5f7fb;
                color: #172033;
                font-family: Arial, DejaVu Sans, sans-serif;
            }

            QLabel#header {
                color: #2457d6;
                font-size: 30px;
                font-weight: 800;
                padding: 4px;
            }

            QLabel#subtitle {
                color: #4f5d75;
                font-size: 16px;
                padding-bottom: 6px;
            }

            QFrame#statusCard {
                background-color: white;
                border: 1px solid #dce3ef;
                border-radius: 12px;
            }

            QLabel#statusTitle {
                color: #718096;
                font-size: 12px;
                font-weight: 700;
            }

            QLabel#statusValue {
                color: #172033;
                font-size: 18px;
                font-weight: 800;
            }

            QFrame#panel {
                background-color: white;
                border: 1px solid #dce3ef;
                border-radius: 14px;
            }

            QLabel#sectionTitle {
                color: #253858;
                font-size: 18px;
                font-weight: 800;
            }

            QLabel#smallHint {
                color: #718096;
                font-size: 13px;
            }

            QLabel#batchTitle {
                color: #52637a;
                font-size: 14px;
                font-weight: 800;
            }

            QLabel#batchValue {
                background-color: #eef2f7;
                color: #52637a;
                border: 1px solid #d6deea;
                border-radius: 9px;
                font-size: 15px;
                font-weight: 800;
                padding: 7px;
            }

            QLineEdit#barcodeInput, QPushButton#expectedCountButton {
                background-color: white;
                color: #172033;
                border: 2px solid #9db4dd;
                border-radius: 9px;
                font-size: 15px;
                font-weight: 700;
                padding: 6px;
            }

            QPushButton#expectedCountButton:disabled {
                background-color: #eef2f7;
                color: #718096;
                border-color: #d6deea;
            }

            QLineEdit#barcodeInput:focus {
                border-color: #2457d6;
            }

            QLabel#imagePreview {
                background-color: #182033;
                color: #dce6ff;
                border: 2px solid #2c3b5d;
                border-radius: 12px;
                font-size: 19px;
                font-weight: 600;
                padding: 10px;
            }

            QListWidget#imageList {
                background-color: #fbfcff;
                border: 1px solid #dce3ef;
                border-radius: 10px;
                font-size: 14px;
                padding: 5px;
            }

            QListWidget#imageList::item {
                min-height: 34px;
                padding: 5px;
                border-radius: 6px;
            }

            QListWidget#imageList::item:selected {
                background-color: #dce7ff;
                color: #173c99;
            }

            QPushButton {
                border: none;
                border-radius: 12px;
                font-size: 16px;
                font-weight: 800;
                padding: 10px 14px;
            }

            QPushButton:pressed {
                padding-top: 12px;
                padding-bottom: 8px;
            }

            QPushButton:disabled {
                background-color: #d7dce5;
                color: #818a99;
            }

            QPushButton#startButton {
                background-color: #22a06b;
                color: white;
            }

            QPushButton#startButton:hover {
                background-color: #1b895c;
            }

            QPushButton#stopButton {
                background-color: #d64545;
                color: white;
            }

            QPushButton#stopButton:hover {
                background-color: #bd3838;
            }

            QPushButton#resetButton {
                background-color: #d9822b;
                color: white;
            }

            QPushButton#resetButton:hover {
                background-color: #bd6f20;
            }

            QPushButton#directionButton {
                background-color: #7b61d1;
                color: white;
            }

            QPushButton#directionButton:hover {
                background-color: #684fc0;
            }

            QPushButton#photoButton {
                background-color: #2475d0;
                color: white;
            }

            QPushButton#photoButton:hover {
                background-color: #1c63b4;
            }

            QPushButton#completeBatchButton {
                background-color: #2457d6;
                color: white;
            }

            QPushButton#cancelBatchButton {
                background-color: #d9822b;
                color: white;
            }

            QPushButton#smallButton {
                background-color: #e8edf6;
                color: #2e3b55;
                border: 1px solid #d3dbe9;
                font-size: 14px;
                font-weight: 700;
            }

            QPushButton#smallButton:hover {
                background-color: #dce5f3;
            }

            QLabel#messageBanner {
                background-color: #eaf2ff;
                color: #234d9b;
                border: 1px solid #c9dcff;
                border-radius: 10px;
                font-size: 17px;
                font-weight: 700;
                padding: 10px;
            }
            """
        )

        if self.compact_mode:
            self.setStyleSheet(
                self.styleSheet()
                + """
                QLabel#header {
                    font-size: 22px;
                    padding: 0;
                }

                QFrame#statusCard {
                    border-radius: 8px;
                }

                QLabel#statusTitle {
                    font-size: 10px;
                }

                QLabel#statusValue {
                    font-size: 14px;
                }

                QFrame#panel {
                    border-radius: 9px;
                }

                QLabel#sectionTitle,
                QLabel#photoPageTitle {
                    color: #253858;
                    font-size: 15px;
                    font-weight: 800;
                }

                QLabel#smallHint {
                    font-size: 12px;
                }

                QLabel#imagePreview {
                    border-radius: 8px;
                    font-size: 15px;
                    padding: 4px;
                }

                QListWidget#imageList {
                    font-size: 14px;
                    padding: 3px;
                }

                QListWidget#imageList::item {
                    min-height: 40px;
                    padding: 4px;
                }

                QPushButton {
                    border-radius: 8px;
                    font-size: 13px;
                    padding: 6px 8px;
                }

                QPushButton#smallButton {
                    font-size: 13px;
                }

                QPushButton#photosButton,
                QPushButton#backButton,
                QPushButton#headerButton {
                    background-color: #e8edf6;
                    color: #2e3b55;
                    border: 1px solid #d3dbe9;
                    font-size: 13px;
                    font-weight: 800;
                }

                QPushButton#photosButton:hover,
                QPushButton#backButton:hover,
                QPushButton#headerButton:hover {
                    background-color: #dce5f3;
                }

                QLabel#messageBanner {
                    border-radius: 8px;
                    font-size: 14px;
                    padding: 6px;
                }
                """
            )

    # ========================================================
    # BARCODE BATCH CONTROL
    # ========================================================

    def _focus_barcode_input(self):
        if hasattr(self, "barcode_edit") and self.barcode_edit.isEnabled():
            self.barcode_edit.setFocus(Qt.OtherFocusReason)

    def _refresh_batch_ui(self):
        manifest = self.storage.active_manifest

        if manifest is not None:
            barcode = str(manifest.get("barcode") or "")
            display = barcode
            if len(display) > 28:
                display = f"{display[:25]}..."

            self.active_batch_id = barcode
            self.batch_value_label.setText(display)
            self.batch_value_label.setToolTip(barcode)
            self.batch_value_label.setStyleSheet(
                "background-color: #e5f6ef; color: #146b49;"
            )
            self.barcode_edit.setPlaceholderText(
                "Batch locked - later scans are ignored"
            )
            expected = int(manifest.get("expected_count") or 0)
            actual = self.storage.current_photo_count()
            if expected > 0:
                self.expected_count_button.setText(
                    f"EXPECTED SAMPLES\n{actual} / {expected}"
                )
            else:
                self.expected_count_button.setText(
                    "EXPECTED SAMPLES\nNOT SET"
                )
        else:
            self.active_batch_id = None
            self.batch_value_label.setText("NO ACTIVE BATCH")
            self.batch_value_label.setToolTip("")
            self.batch_value_label.setStyleSheet("")
            self.barcode_edit.setPlaceholderText(
                "Scan new batch barcode"
            )
            self.expected_count_button.setText(
                "EXPECTED SAMPLES\nNOT SET"
            )

        blocked = bool(
            self.storage.recovery_error
            or self.storage_fault_message
            or self.recovery_pending
            or self.usb_export_in_progress
        )
        has_batch = manifest is not None and not blocked
        busy = (
            self.capture_cycle_in_progress
            or self.direction_change_in_progress
            or self.reset_in_progress
        )

        self.barcode_edit.setEnabled(
            not self.storage.recovery_error
            and not self.storage_fault_message
            and not self.usb_export_in_progress
        )
        self.btn_complete_batch.setEnabled(has_batch and not busy)
        self.btn_cancel_batch.setEnabled(has_batch and not busy)
        self.expected_count_button.setEnabled(
            has_batch
            and not self.conveyor_running
            and not busy
        )

        if not has_batch:
            self.btn_complete_batch.setEnabled(False)
            self.btn_cancel_batch.setEnabled(False)

        QTimer.singleShot(0, self._focus_barcode_input)

    def _show_batch_recovery_error(self):
        self.recovery_pending = True
        self.update_status_display()
        QMessageBox.critical(
            self,
            "Batch Recovery Error",
            "The previous batch state could not be trusted. The conveyor is "
            "locked to prevent images from being assigned to the wrong "
            "barcode.\n\n"
            f"{self.storage.recovery_error}\n\n"
            "Use developer access to inspect the image folder and active "
            "batch state file.",
        )

    def _prompt_incomplete_batch_recovery(self):
        manifest = self.storage.active_manifest
        if manifest is None:
            self.recovery_pending = False
            self._refresh_batch_ui()
            self.update_status_display()
            return

        barcode = manifest.get("barcode", "Unknown")
        count = self.storage.current_photo_count()

        dialog = QMessageBox(self)
        dialog.setWindowTitle("Incomplete Batch Found")
        dialog.setIcon(QMessageBox.Warning)
        dialog.setText(f"Incomplete batch: {barcode}")
        dialog.setInformativeText(
            f"Saved photos: {count}\n\n"
            "Resume this batch, or close it as incomplete. The conveyor "
            "will remain stopped until you choose."
        )
        resume_button = dialog.addButton(
            "RESUME BATCH",
            QMessageBox.AcceptRole,
        )
        close_button = dialog.addButton(
            "CLOSE AS INCOMPLETE",
            QMessageBox.DestructiveRole,
        )
        decide_button = dialog.addButton(
            "DECIDE LATER",
            QMessageBox.RejectRole,
        )
        dialog.setDefaultButton(decide_button)
        dialog.setEscapeButton(decide_button)
        dialog.exec()

        if dialog.clickedButton() is resume_button:
            self.recovery_pending = False
            self.session_photo_count = count
            self._say(
                f"Batch {barcode} resumed with {count} saved photos. "
                "Press START BELT when ready."
            )

        elif dialog.clickedButton() is close_button:
            try:
                self.storage.cancel_batch("closed_after_restart")
            except StorageError as error:
                self._handle_storage_failure(str(error))
                return

            self.recovery_pending = False
            self.active_batch_id = None
            self.last_scan_code = None
            self._say(
                "Incomplete batch closed. Scan a barcode for the next batch."
            )

        self.refresh_image_list()
        self._refresh_batch_ui()
        self.update_status_display()

    def handle_barcode_scan(self):
        raw_code = self.barcode_edit.text()
        self.barcode_edit.clear()
        QTimer.singleShot(0, self._focus_barcode_input)

        try:
            code = self.storage.validate_barcode(raw_code)
        except StorageError as error:
            if raw_code.strip():
                self._say(f"Barcode rejected: {error}")
            return

        now = time.monotonic()

        if self.storage.recovery_error or self.storage_fault_message:
            self._say(
                "Barcode ignored. Resolve the batch recovery error first."
            )
            return

        if self.usb_export_in_progress:
            self._say("Barcode ignored while the batch is being saved to USB.")
            return

        if self.recovery_pending:
            self._say(
                "Barcode ignored. Resume or close the incomplete batch first."
            )
            return

        if self.storage.active_manifest is not None:
            if code == self.active_batch_id:
                self._say(
                    f"Duplicate scan ignored. Batch {code} is already active."
                )
            else:
                self._say(
                    f"Barcode {code} ignored. Complete or cancel batch "
                    f"{self.active_batch_id} first."
                )
            return

        if (
            code == self.last_scan_code
            and now - self.last_scan_at < BARCODE_DUPLICATE_WINDOW_SEC
        ):
            self._say(f"Duplicate barcode {code} ignored.")
            return

        try:
            self.storage.create_batch(
                code,
                expected_count=0,
            )
        except StorageError as error:
            QMessageBox.warning(
                self,
                "Batch Could Not Be Created",
                str(error),
            )
            return

        self.last_scan_code = code
        self.last_scan_at = now
        self.active_batch_id = code
        self.session_photo_count = 0
        self.current_index = -1
        self.refresh_image_list()
        self._refresh_batch_ui()
        self._say(
            f"Batch {code} is ready. The belt is still stopped. "
            "Press START BELT when the conveyor is clear."
        )
        self.update_status_display()

    def open_expected_samples_dialog(self):
        if (
            self.storage.active_manifest is None
            or self.recovery_pending
            or self.conveyor_running
            or self.capture_cycle_in_progress
            or self.usb_export_in_progress
        ):
            return

        current_value = int(
            self.storage.active_manifest.get("expected_count") or 0
        )
        dialog = ExpectedSamplesDialog(
            self,
            current_value=current_value,
            captured_count=self.storage.current_photo_count(),
        )
        if dialog.exec() != QDialog.Accepted:
            QTimer.singleShot(0, self._focus_barcode_input)
            return

        value = dialog.selected_value
        if value is None:
            QTimer.singleShot(0, self._focus_barcode_input)
            return

        try:
            self.storage.update_expected_count(value)
        except StorageError as error:
            self._handle_storage_failure(str(error))
            return

        self._refresh_batch_ui()
        if value > 0:
            self._say(
                f"Expected sample count set to {value}. "
                "The belt remains stopped until START BELT is pressed."
            )
        else:
            self._say(
                "Expected sample count is not set. Complete the batch "
                "manually when all samples are finished."
            )
        self.update_status_display()

    def complete_batch(self):
        if self.storage.active_manifest is None:
            self._say("There is no active batch to complete.")
            return
        if self.usb_export_in_progress:
            self._say("Wait for the USB export to finish.")
            return
        if (
            self.capture_cycle_in_progress
            or self.direction_change_in_progress
            or self.reset_in_progress
        ):
            self._say(
                "Wait for the current movement/photo/reset operation to finish."
            )
            return

        if self.conveyor_running:
            reply = QMessageBox.question(
                self,
                "Stop and Complete Batch?",
                "The belt is still running. Stop it and continue to the "
                "batch-completion check?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            self.stop_system()

        count = self.storage.current_photo_count()
        expected = int(
            self.storage.active_manifest.get("expected_count") or 0
        )
        mismatch = expected > 0 and expected != count
        sensor_note = (
            "\n\nWARNING: The proximity sensor is still ACTIVE."
            if self.hardware.stem_detected()
            else ""
        )
        expected_text = (
            f"Expected: {expected}\n" if expected > 0 else "Expected: unknown\n"
        )
        mismatch_text = (
            "\nThe saved-photo count does not match the expected count."
            if mismatch
            else ""
        )

        reply = QMessageBox.question(
            self,
            "Complete Batch?",
            f"Batch: {self.active_batch_id}\n"
            f"Saved photos: {count}\n"
            f"{expected_text}"
            f"{mismatch_text}{sensor_note}\n\n"
            "Complete this batch and unlock barcode scanning?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        completed_barcode = self.active_batch_id
        try:
            self.storage.complete_batch()
        except StorageError as error:
            self._handle_storage_failure(str(error))
            return

        self.active_batch_id = None
        self.last_scan_code = None
        self.last_scan_at = 0.0
        self._refresh_batch_ui()
        self._say(
            f"Batch {completed_barcode} completed with {count} photos. "
            "Scan the next batch barcode; the belt remains stopped."
        )
        self.update_status_display()

    def cancel_batch(self):
        if self.storage.active_manifest is None:
            self._say("There is no active batch to cancel.")
            return
        if self.usb_export_in_progress:
            self._say("Wait for the USB export to finish.")
            return
        if (
            self.capture_cycle_in_progress
            or self.direction_change_in_progress
            or self.reset_in_progress
        ):
            self._say(
                "Wait for the current movement/photo/reset operation to finish."
            )
            return

        if self.conveyor_running:
            self.stop_system()

        count = self.storage.current_photo_count()
        reply = QMessageBox.question(
            self,
            "Cancel Batch?",
            f"Cancel batch {self.active_batch_id}?\n\n"
            f"Saved photos: {count}\n"
            "Photos will be retained and the manifest will be marked "
            "CANCELLED; they will not be silently deleted.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        cancelled_barcode = self.active_batch_id
        try:
            self.storage.cancel_batch("operator_cancelled")
        except StorageError as error:
            self._handle_storage_failure(str(error))
            return

        self.active_batch_id = None
        self.last_scan_code = None
        self.last_scan_at = 0.0
        self._refresh_batch_ui()
        self._say(
            f"Batch {cancelled_barcode} cancelled. "
            "Scan a barcode for the next batch."
        )
        self.update_status_display()

    def incomplete_batch_note(self):
        if self.storage.active_manifest is None:
            return ""
        return (
            f"\n\nBatch {self.active_batch_id} is still incomplete. Its "
            "saved state will be offered for recovery on the next start."
        )

    # ========================================================
    # SYSTEM CONTROL
    # ========================================================

    def start_system(self):
        if self.usb_export_in_progress:
            self._say("Wait for the USB export to finish before starting.")
            return

        if (
            self.storage.recovery_error
            or self.storage_fault_message
            or self.recovery_pending
        ):
            self._say(
                "The conveyor is locked until incomplete batch recovery is resolved."
            )
            return

        if self.storage.active_manifest is None or not self.active_batch_id:
            self._say(
                "Scan a valid batch barcode before starting the belt."
            )
            self._focus_barcode_input()
            return

        expected = int(
            self.storage.active_manifest.get("expected_count") or 0
        )
        actual = self.storage.current_photo_count()
        if expected > 0 and actual >= expected:
            self._say(
                f"Expected count {expected} has been reached. Complete the "
                "batch or change the expected count before restarting."
            )
            return

        try:
            self.storage.check_storage_ready(require_active=True)
        except StorageError as error:
            QMessageBox.critical(
                self,
                "Storage Not Ready",
                f"The conveyor will not start.\n\n{error}",
            )
            return

        if self.reset_in_progress:
            self._say("System reset is still in progress. Please wait.")
            return

        if self.sensor_fault:
            if self.sensor_fault_kind == "no_detection":
                self._say(
                    "NO DETECTION fault is latched. Check the stem feed and "
                    "proximity sensor, then press RESET SYSTEM."
                )
            else:
                self._say(
                    "SENSOR FAULT is latched. Check/clear the proximity sensor, "
                    "then press RESET SYSTEM."
                )
            return

        # Starting with an already-active sensor is ambiguous: it may be a
        # stem left in front of the sensor or a stuck-active fault. Require a
        # clear sensor before motion begins.
        if self.hardware.stem_detected():
            self._say(
                "Sensor is ACTIVE. Remove the stem or check the sensor first; "
                "START BELT requires the sensor to be CLEAR."
            )
            return

        if not self.camera.ready:
            self._say(
                "Camera is not ready yet. Wait for initialization or check the CSI connection."
            )
            return

        if self.capture_cycle_in_progress:
            self._say("Please wait until the current photo cycle is finished.")
            return

        if self.direction_change_in_progress:
            self._say("Direction is changing. Please wait.")
            return

        if self.system_running and self.conveyor_running:
            self._say("The belt is already running.")
            return

        started = self.hardware.conveyor_start(self.selected_direction)

        if not started:
            QMessageBox.warning(
                self,
                "Conveyor Could Not Start",
                "The conveyor start command was refused.\n\n"
                "Check GPIO initialization and, for reverse operation, "
                "confirm REVERSE_RELAY_PIN is configured.",
            )
            return

        self.system_running = True
        self.conveyor_running = True

        # START requires the sensor to be clear, so begin with a clean edge
        # detector and no active-duration timer.
        self.sensor_was_active = False
        self.sensor_active_since = None
        self._arm_no_detection_watchdog()

        self._say(
            f"Belt started {self.selected_direction.upper()}. "
            f"Ready for one stem at a time. If no stem is detected for "
            f"{NO_DETECTION_TIMEOUT_SEC:.0f} seconds, the belt will stop."
        )

        self.update_status_display()

    def stop_system(self):
        self.direction_timer.stop()
        self.direction_change_in_progress = False
        self.pending_direction = None

        self.cancel_capture_cycle()

        self.system_running = False
        self.conveyor_running = False
        self.sensor_active_since = None
        self.no_detection_since = None

        self.hardware.conveyor_stop()

        if self.storage.active_manifest is not None:
            self._say(
                f"Batch {self.active_batch_id} paused with "
                f"{self.storage.current_photo_count()} saved photos. "
                "Load more samples, then press START BELT to resume."
            )
        else:
            self._say("Belt stopped.")
        self.update_status_display()

    def reset_system(self):
        """Return the application to a safe known state.

        RESET SYSTEM always stops the conveyor first, cancels software timing,
        clears the current capture cycle, and restarts only the isolated camera
        worker. The active barcode batch and its saved-photo count are retained.
        It never restarts the conveyor automatically. A stuck-ACTIVE
        sensor fault clears only if the physical proximity input is CLEAR after
        reset. A NO DETECTION fault also requires RESET before a new START.
        """
        if self.reset_in_progress:
            self._say("System reset is already in progress.")
            return

        reply = QMessageBox.question(
            self,
            "Reset System?",
            "Reset the system?\n\n"
            "The conveyor will stop immediately, the current imaging cycle "
            "will be cleared, and the camera will restart.\n\n"
            "A stuck-ACTIVE sensor fault clears only if the proximity sensor is CLEAR.\n"
            "A NO DETECTION fault also requires RESET before another START.\n\n"
            "The active barcode batch and saved images will be preserved.\n\n"
            "The conveyor will NOT restart automatically.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        # SAFETY FIRST: remove the conveyor run command before doing any
        # camera/process recovery work.
        try:
            self.hardware.conveyor_stop()
        except Exception:
            pass

        self.system_running = False
        self.conveyor_running = False

        # Cancel direction change and all conveyor/capture timing.
        self.direction_timer.stop()
        self.direction_change_in_progress = False
        self.pending_direction = None

        self.travel_timer.stop()
        self.settle_timer.stop()
        self.restart_timer.stop()

        # Clear the imaging state without calling camera.cancel_capture();
        # camera.reset() itself owns termination/recreation of the worker.
        self.capture_cycle_in_progress = False
        self.capture_stage = self.STAGE_IDLE
        self.capture_source = "auto"
        self.restart_after_capture = False
        self.pending_capture_path = None

        # Stop the stuck-active timer while the system is intentionally reset.
        # The sensor fault latch is evaluated again after camera reset.
        self.sensor_active_since = None
        self.no_detection_since = None
        self.sensor_was_active = self.hardware.stem_detected()

        self.reset_in_progress = True
        self._say(
            "RESETTING SYSTEM... Belt is OFF. Restarting the camera."
        )
        self.update_status_display()

        started = self.camera.reset()

        if not started:
            self.reset_in_progress = False
            self._say(
                "Reset could not be started. Belt remains stopped."
            )
            self.update_status_display()

    # ========================================================
    # DIRECTION
    # ========================================================

    def toggle_direction(self):
        if self.reset_in_progress:
            self._say("System reset is in progress. Please wait.")
            return

        if self.capture_cycle_in_progress:
            self._say("Please wait until the current photo cycle is finished.")
            return

        if not self.hardware.reverse_configured:
            QMessageBox.information(
                self,
                "Reverse Control Not Configured",
                "The direction button is ready in the software, but the reverse GPIO pin is not set.\n\n"
                "After the reverse-control hardware is wired, set REVERSE_RELAY_PIN in config.py.",
            )
            return

        if self.direction_change_in_progress:
            self._say("Direction is already changing.")
            return

        target = (
            "reverse"
            if self.selected_direction == "forward"
            else "forward"
        )

        if not self.system_running:
            self.selected_direction = target
            self.hardware.direction = target

            self._say(
                f"Direction set to {target.upper()}. Press START BELT when ready."
            )

            self._update_direction_button()
            self.update_status_display()
            return

        # Running: stop first, wait for dead time, then restart.
        self.direction_change_in_progress = True
        self.pending_direction = target

        self.conveyor_running = False
        self.no_detection_since = None
        self.hardware.conveyor_stop()

        self._say(f"Changing direction to {target.upper()}...")

        self.btn_direction.setEnabled(False)
        self.direction_timer.start(DIRECTION_CHANGE_DEAD_TIME_MS)

        self.update_status_display()

    def _finish_direction_change(self):
        target = self.pending_direction

        self.pending_direction = None
        self.direction_change_in_progress = False

        if target is None:
            self._update_direction_button()
            return

        self.selected_direction = target

        if self.system_running:
            started = self.hardware.conveyor_start(target)

            if started:
                self.conveyor_running = True
                self.sensor_was_active = self.hardware.stem_detected()
                self.sensor_active_since = None
                self._arm_no_detection_watchdog()

                self._say(
                    f"Direction changed. Belt is now moving {target.upper()}."
                )

            else:
                self.conveyor_running = False
                self.system_running = False

                self._say("Could not start in the new direction.")

        self._update_direction_button()
        self.update_status_display()

    def _update_direction_button(self):
        if self.reset_in_progress:
            self.btn_direction.setText("↔  WAIT FOR RESET")
            self.btn_direction.setEnabled(False)
            return

        if self.sensor_fault:
            if self.sensor_fault_kind == "no_detection":
                self.btn_direction.setText("↔  CHECK SENSOR")
            else:
                self.btn_direction.setText("↔  SENSOR FAULT")
            self.btn_direction.setEnabled(False)
            return

        if not self.hardware.reverse_configured:
            self.btn_direction.setText("↔  REVERSE NOT SET UP")
            self.btn_direction.setEnabled(False)
            self.btn_direction.setToolTip(
                "Set REVERSE_RELAY_PIN in config.py after confirming the wiring."
            )
            return

        if self.direction_change_in_progress:
            self.btn_direction.setText("↔  CHANGING...")
            self.btn_direction.setEnabled(False)
            return

        if self.capture_cycle_in_progress:
            self.btn_direction.setText("↔  WAIT FOR PHOTO")
            self.btn_direction.setEnabled(False)
            return

        self.btn_direction.setEnabled(True)

        if self.selected_direction == "forward":
            self.btn_direction.setText("↔  SWITCH TO REVERSE")
        else:
            self.btn_direction.setText("↔  SWITCH TO FORWARD")

    # ========================================================
    # CAMERA STATE
    # ========================================================

    def _on_camera_ready_changed(self, ready, message):
        if ready:
            if (
                not self.reset_in_progress
                and not self.system_running
                and not self.capture_cycle_in_progress
            ):
                self._say(
                    "Camera ready. Press START BELT when the conveyor area is clear."
                )
        else:
            # Fail closed whenever camera availability is lost.
            if self.system_running or self.conveyor_running:
                self.hardware.conveyor_stop()
                self.system_running = False
                self.conveyor_running = False
                self.no_detection_since = None

            if self.reset_in_progress:
                self._say(
                    "RESETTING SYSTEM... Belt is OFF. Restarting the camera."
                )
            elif message:
                self._say(f"Camera unavailable. Belt stopped. {message}")
            else:
                self._say("Camera unavailable. Belt stopped.")

        self.update_status_display()

    def _on_camera_reset_completed(self, success, message):
        # Conveyor remains OFF regardless of reset result.
        try:
            self.hardware.conveyor_stop()
        except Exception:
            pass

        self.system_running = False
        self.conveyor_running = False
        self.reset_in_progress = False
        self.capture_cycle_in_progress = False
        self.capture_stage = self.STAGE_IDLE
        self.restart_after_capture = False
        self.pending_capture_path = None

        detected = self.hardware.stem_detected()
        self.sensor_was_active = detected
        self.sensor_active_since = None
        self.no_detection_since = None

        # A sensor fault is deliberately latched until RESET SYSTEM is
        # completed while the physical sensor input is CLEAR. This prevents
        # a stuck-active sensor from being "reset" back into motion.
        if detected:
            self.sensor_fault = True
            self.sensor_fault_kind = "stuck_active"
            self.sensor_fault_message = (
                "Proximity sensor is still ACTIVE after reset."
            )
        else:
            self.sensor_fault = False
            self.sensor_fault_kind = None
            self.sensor_fault_message = ""

        if success and not self.sensor_fault:
            self._say(
                "System reset complete. Camera ready. Sensor clear. "
                "Belt is stopped. Press START BELT when ready."
            )
        elif success and self.sensor_fault:
            self._say(
                "SENSOR FAULT remains latched. Belt is OFF. Clear/check the "
                "proximity sensor, then press RESET SYSTEM again."
            )

            QMessageBox.warning(
                self,
                "Sensor Fault - Belt Stopped",
                "The camera restarted, but the proximity sensor is still "
                "ACTIVE. The conveyor remains locked OFF.\n\n"
                "Remove any object from the sensor area and check the sensor "
                "or wiring, then press RESET SYSTEM again.",
            )
        else:
            self._say(
                "RESET FAILED. Belt remains stopped. Check the camera connection "
                "and try RESET SYSTEM again."
            )

            QMessageBox.warning(
                self,
                "Reset Failed - Belt Stopped",
                "The camera could not be restarted. The conveyor remains OFF.\n\n"
                f"{message}",
            )

        self.update_status_display()

    # ========================================================
    # SENSOR + AUTOMATIC CAPTURE
    # ========================================================

    def check_sensor(self):
        detected = self.hardware.stem_detected()

        # ----------------------------------------------------
        # STUCK-ACTIVE SENSOR WATCHDOG
        # ----------------------------------------------------
        # Only time the ACTIVE state while the automatic system is operating.
        # When the belt is intentionally stopped by the operator, a stem may
        # legitimately remain near the sensor without creating a fault.
        monitor_active = (
            self.system_running
            or self.capture_cycle_in_progress
            or self.direction_change_in_progress
        ) and not self.reset_in_progress

        if monitor_active and detected and not self.sensor_fault:
            if self.sensor_active_since is None:
                self.sensor_active_since = time.monotonic()
            else:
                active_for = time.monotonic() - self.sensor_active_since
                if active_for >= SENSOR_STUCK_ACTIVE_TIMEOUT_SEC:
                    self._trigger_sensor_fault(
                        f"Proximity sensor stayed ACTIVE for "
                        f"{active_for:.1f} seconds.",
                        kind="stuck_active",
                    )
                    return
        else:
            # Clear the duration timer as soon as the sensor returns CLEAR or
            # when monitoring is intentionally inactive. The fault latch itself
            # is only cleared by RESET SYSTEM with the sensor physically clear.
            self.sensor_active_since = None

        if self.sensor_fault:
            return

        # ----------------------------------------------------
        # NORMAL RISING-EDGE DETECTION
        # ----------------------------------------------------
        if detected and not self.sensor_was_active:
            self.sensor_was_active = True

            # A real sensor transition ends the current no-detection interval.
            # A fresh interval begins only after the conveyor restarts.
            self.no_detection_since = None

            if (
                self.system_running
                and self.conveyor_running
                and not self.capture_cycle_in_progress
                and self._auto_capture_allowed()
            ):
                self.start_automatic_capture_cycle()

            elif (
                self.system_running
                and self.conveyor_running
                and not self._auto_capture_allowed()
            ):
                self._say(
                    f"Stem detected, but auto photo is OFF in "
                    f"{self.selected_direction.upper()} mode."
                )

        # Rearm after the object leaves the sensor.
        elif not detected:
            self.sensor_was_active = False

        # ----------------------------------------------------
        # NO-DETECTION WATCHDOG
        # ----------------------------------------------------
        # Monitor only while the belt is actually moving in a direction where
        # automatic capture is enabled. Pausing during a photo or direction
        # change avoids counting intentional stopped time.
        monitor_no_detection = (
            self.system_running
            and self.conveyor_running
            and not self.capture_cycle_in_progress
            and not self.direction_change_in_progress
            and not self.reset_in_progress
            and self._auto_capture_allowed()
        )

        if monitor_no_detection and not detected:
            if self.no_detection_since is None:
                self.no_detection_since = time.monotonic()
            else:
                no_detection_for = time.monotonic() - self.no_detection_since
                if no_detection_for >= NO_DETECTION_TIMEOUT_SEC:
                    self._trigger_sensor_fault(
                        f"No stem was detected while the belt ran for "
                        f"{no_detection_for:.1f} seconds.",
                        kind="no_detection",
                    )
                    return
        elif not monitor_no_detection:
            self.no_detection_since = None

    def _arm_no_detection_watchdog(self):
        """Start a fresh no-detection interval during automatic operation."""
        if (
            self.system_running
            and self.conveyor_running
            and self._auto_capture_allowed()
            and not self.capture_cycle_in_progress
            and not self.direction_change_in_progress
            and not self.reset_in_progress
        ):
            self.no_detection_since = time.monotonic()
        else:
            self.no_detection_since = None

    def _trigger_sensor_fault(self, message, kind="stuck_active"):
        if self.sensor_fault:
            return

        self.sensor_fault = True
        self.sensor_fault_kind = kind
        self.sensor_fault_message = message
        self.sensor_active_since = None
        self.no_detection_since = None

        # Fail closed: remove motion and cancel any automatic sequence.
        self.direction_timer.stop()
        self.direction_change_in_progress = False
        self.pending_direction = None

        self.cancel_capture_cycle()

        self.system_running = False
        self.conveyor_running = False

        try:
            self.hardware.conveyor_stop()
        except Exception:
            pass

        if kind == "no_detection":
            self._say(
                "NO DETECTION. Belt is OFF. Check the stem feed and proximity "
                "sensor, then press RESET SYSTEM."
            )
            dialog_title = "No Detection - Belt Stopped"
            dialog_text = (
                "No stem was detected within the allowed belt-run time. "
                "The conveyor has been stopped and automatic operation is "
                "locked out.\n\n"
                f"{message}\n\n"
                "This may mean no stem was fed, or the sensor/wiring may be "
                "stuck CLEAR or disconnected. Check the feed and sensor, then "
                "press RESET SYSTEM. The belt will remain OFF until START BELT "
                "is pressed again."
            )
        else:
            self._say(
                "SENSOR FAULT. Belt is OFF. Check/clear the proximity sensor, "
                "then press RESET SYSTEM."
            )
            dialog_title = "Sensor Fault - Belt Stopped"
            dialog_text = (
                "The proximity sensor remained ACTIVE for too long. The conveyor "
                "has been stopped and automatic operation is locked out.\n\n"
                f"{message}\n\n"
                "Check for a stem blocking the sensor and inspect the sensor/wiring. "
                "When the sensor is CLEAR, press RESET SYSTEM."
            )

        self.update_status_display()

        QMessageBox.warning(
            self,
            dialog_title,
            dialog_text,
        )

    def _auto_capture_allowed(self):
        if self.selected_direction == "forward":
            return AUTO_CAPTURE_FORWARD

        return AUTO_CAPTURE_REVERSE

    def start_automatic_capture_cycle(self):
        if self.capture_cycle_in_progress:
            return

        self.capture_cycle_in_progress = True
        self.capture_stage = self.STAGE_TRAVELING
        self.capture_source = "auto"
        self.restart_after_capture = True
        self.no_detection_since = None

        self._say(
            f"Stem detected! Moving to the camera. "
            f"Belt will stop in {SENSOR_TO_STOP_DELAY_SEC:.1f} seconds."
        )

        self.travel_timer.start(
            int(SENSOR_TO_STOP_DELAY_SEC * 1000)
        )

        self.update_status_display()

    def stop_belt_for_capture(self):
        if not self.capture_cycle_in_progress:
            return

        if not self.system_running and self.capture_source == "auto":
            self.cancel_capture_cycle()
            return

        self.hardware.conveyor_stop()
        self.conveyor_running = False
        self.no_detection_since = None
        self.capture_stage = self.STAGE_SETTLING

        self._say(
            "Belt stopped. Waiting for the stem to become completely still..."
        )

        self.settle_timer.start(
            int(BELT_SETTLE_DELAY_SEC * 1000)
        )

        self.update_status_display()

    def capture_after_settle(self):
        if not self.capture_cycle_in_progress:
            return

        if self.capture_source == "auto" and not self.system_running:
            self.cancel_capture_cycle()
            return

        self.capture_stage = self.STAGE_CAPTURING
        self._say("Taking photo...")
        self.update_status_display()

        started = self.start_capture_image(source=self.capture_source)

        if not started and self.capture_cycle_in_progress:
            self._handle_camera_failure(
                "The camera worker could not be started."
            )

    def _on_camera_capture_completed(self, success, path_text, message):
        # Ignore a late result from a capture that the user already stopped.
        if (
            not self.capture_cycle_in_progress
            or self.capture_stage != self.STAGE_CAPTURING
        ):
            return

        save_path = Path(path_text)
        self.pending_capture_path = None

        if not success:
            self._handle_camera_failure(message)
            return

        try:
            self.storage.record_capture(
                save_path,
                source=self.capture_source,
            )
        except StorageError as error:
            self._handle_storage_failure(str(error))
            return

        self.session_photo_count = self.storage.current_photo_count()
        self.refresh_image_list(preferred_path=save_path)

        expected = int(
            self.storage.active_manifest.get("expected_count") or 0
        )
        expected_reached = (
            expected > 0 and self.session_photo_count >= expected
        )

        if expected_reached:
            # The belt is already stopped for the photo. Do not restart after
            # the declared number of samples; the operator still explicitly
            # reviews and completes the batch.
            self.system_running = False
            self.conveyor_running = False
            self.capture_cycle_in_progress = False
            self.capture_stage = self.STAGE_IDLE
            self.restart_after_capture = False
            self._say(
                f"Expected count {expected} reached. Belt remains stopped. "
                "Review the count, then press COMPLETE BATCH."
            )

        elif self.restart_after_capture and self.system_running:
            self.capture_stage = self.STAGE_RESTARTING
            self._say("Photo saved! Restarting the belt...")

            self.restart_timer.start(
                int(POST_CAPTURE_DELAY_SEC * 1000)
            )
        else:
            self.capture_cycle_in_progress = False
            self.capture_stage = self.STAGE_IDLE
            self.restart_after_capture = False
            self._say("Photo saved. Belt remains stopped.")

        self.update_status_display()

    def _handle_camera_failure(self, message):
        # Fail closed: never restart the conveyor after a camera error.
        self.hardware.conveyor_stop()
        self.conveyor_running = False
        self.system_running = False
        self.no_detection_since = None

        self.capture_cycle_in_progress = False
        self.capture_stage = self.STAGE_IDLE
        self.restart_after_capture = False
        self.pending_capture_path = None

        self._say(
            "CAMERA ERROR. Belt remains stopped. "
            "Check the camera, then press RESET SYSTEM."
        )
        self.update_status_display()

        QMessageBox.warning(
            self,
            "Camera Error - Belt Stopped",
            "The photo could not be completed. The conveyor has been kept OFF.\n\n"
            f"{message}",
        )

    def _handle_storage_failure(self, message):
        # A valid image without a durable manifest entry must not be followed
        # by another sample. Preserve files for recovery and fail closed.
        self.hardware.conveyor_stop()
        self.conveyor_running = False
        self.system_running = False
        self.no_detection_since = None
        self.capture_cycle_in_progress = False
        self.capture_stage = self.STAGE_IDLE
        self.restart_after_capture = False
        self.pending_capture_path = None
        self.storage_fault_message = str(message)

        self._say(
            "STORAGE ERROR. Belt remains stopped. Do not continue this batch."
        )
        self.update_status_display()

        QMessageBox.critical(
            self,
            "Storage Error - Belt Stopped",
            "The batch data could not be committed safely. "
            "The conveyor has been kept OFF.\n\n"
            f"{message}\n\n"
            "Do not restart until the storage problem and batch manifest "
            "have been inspected.",
        )

    def restart_after_capture_cycle(self):
        if not self.capture_cycle_in_progress:
            return

        if not self.system_running:
            self.cancel_capture_cycle()
            return

        started = self.hardware.conveyor_start(self.selected_direction)

        if not started:
            self.conveyor_running = False
            self.capture_cycle_in_progress = False
            self.capture_stage = self.STAGE_IDLE
            self.restart_after_capture = False

            self._say("Could not restart the conveyor.")
            self.update_status_display()
            return

        self.conveyor_running = True
        self.capture_cycle_in_progress = False
        self.capture_stage = self.STAGE_IDLE
        self.restart_after_capture = False

        # Do not create a false rising edge immediately after restart. The
        # stuck-active watchdog will start timing on the next sensor poll if
        # the input is still ACTIVE.
        self.sensor_was_active = self.hardware.stem_detected()
        self.sensor_active_since = None
        self._arm_no_detection_watchdog()

        self._say(
            "Photo saved. Belt restarted. Ready for the next stem! "
            f"No-detection timeout: {NO_DETECTION_TIMEOUT_SEC:.0f} s."
        )
        self.update_status_display()

    def cancel_capture_cycle(self):
        self.travel_timer.stop()
        self.settle_timer.stop()
        self.restart_timer.stop()

        self.camera.cancel_capture()

        self.capture_cycle_in_progress = False
        self.capture_stage = self.STAGE_IDLE
        self.capture_source = "auto"
        self.restart_after_capture = False
        self.pending_capture_path = None
        self.no_detection_since = None

    # ========================================================
    # MANUAL PHOTO
    # ========================================================

    def manual_capture(self):
        if self.usb_export_in_progress:
            self._say("Wait for the USB export to finish before imaging.")
            return

        if self.storage.active_manifest is None:
            self._say(
                "Scan a batch barcode before taking a sample photo."
            )
            self._focus_barcode_input()
            return

        expected = int(
            self.storage.active_manifest.get("expected_count") or 0
        )
        if expected > 0 and self.storage.current_photo_count() >= expected:
            self._say(
                "Expected sample count has been reached. Complete the batch "
                "or increase the expected count before taking another photo."
            )
            return

        if self.storage.recovery_error or self.storage_fault_message:
            self._say(
                "Storage is locked. Resolve the storage error before imaging."
            )
            return

        if self.recovery_pending:
            self._say(
                "Resolve the incomplete batch before taking a photo."
            )
            return

        if self.reset_in_progress:
            self._say("System reset is in progress. Please wait.")
            return

        if self.capture_cycle_in_progress:
            self._say("Please wait for the current photo cycle to finish.")
            return

        if self.direction_change_in_progress:
            self._say("Please wait until the direction change is finished.")
            return

        self.capture_cycle_in_progress = True
        self.capture_source = "manual"

        # If the belt was moving, restart it after the photo.
        self.restart_after_capture = (
            self.system_running and self.conveyor_running
        )

        if self.conveyor_running:
            self.hardware.conveyor_stop()
            self.conveyor_running = False

        self.capture_stage = self.STAGE_SETTLING
        self._say("Belt stopped. Preparing manual photo...")

        self.settle_timer.start(
            int(BELT_SETTLE_DELAY_SEC * 1000)
        )

        self.update_status_display()

    # ========================================================
    # IMAGE CAPTURE
    # ========================================================

    def start_capture_image(self, source="auto"):
        try:
            save_path = self.storage.next_capture_path(source=source)
        except StorageError as error:
            self._handle_storage_failure(str(error))
            return False

        self.pending_capture_path = save_path

        started = self.camera.capture_async(save_path)

        if not started:
            self.pending_capture_path = None

        return started

    # ========================================================
    # IMAGE LIST
    # ========================================================

    def refresh_image_list(self, preferred_path=None):
        old_path = None

        if (
            self.image_files
            and 0 <= self.current_index < len(self.image_files)
        ):
            old_path = self.image_files[self.current_index]

        self.image_files = self.storage.get_images()
        self.image_list.clear()

        for number, image_path in enumerate(self.image_files, start=1):
            item = QListWidgetItem(f"{number}. {image_path.name}")
            self.image_list.addItem(item)

        if not self.image_files:
            self.current_index = -1
            self.image_preview.clear()
            if self.storage.active_manifest is None:
                self.image_preview.setText(
                    "No active batch\n\nScan a batch barcode to begin."
                )
            else:
                self.image_preview.setText(
                    "No photos in this batch\n\nPress START BELT when ready."
                )
            self.update_status_display()
            return

        target_path = preferred_path or old_path

        if target_path is not None and target_path in self.image_files:
            self.current_index = self.image_files.index(target_path)

        elif self.current_index < 0:
            self.current_index = len(self.image_files) - 1

        elif self.current_index >= len(self.image_files):
            self.current_index = len(self.image_files) - 1

        self.show_current_image()
        self.update_status_display()

    def show_current_image(self):
        if (
            not self.image_files
            or self.current_index < 0
            or self.current_index >= len(self.image_files)
        ):
            self.image_preview.clear()
            self.image_preview.setText("No photo selected.")
            return

        image_path = self.image_files[self.current_index]
        pixmap = QPixmap(str(image_path))

        if pixmap.isNull():
            self.image_preview.clear()
            self.image_preview.setText("This photo could not be opened.")
            return

        scaled = pixmap.scaled(
            self.image_preview.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        self.image_preview.setPixmap(scaled)
        self.image_list.setCurrentRow(self.current_index)

    def select_image_from_list(self, item):
        row = self.image_list.row(item)

        if 0 <= row < len(self.image_files):
            self.current_index = row
            self.show_current_image()

    def previous_image(self):
        if not self.image_files:
            return

        self.current_index = max(0, self.current_index - 1)
        self.show_current_image()

    def next_image(self):
        if not self.image_files:
            return

        self.current_index = min(
            len(self.image_files) - 1,
            self.current_index + 1,
        )
        self.show_current_image()

    def delete_current_image(self):
        if self.usb_export_in_progress:
            self._say("Wait for the USB export to finish.")
            return

        if not self.image_files or self.current_index < 0:
            QMessageBox.information(
                self,
                "No Photo Selected",
                "Choose a photo first.",
            )
            return

        if self.storage.active_manifest is None:
            QMessageBox.information(
                self,
                "Batch Is Closed",
                "Completed and cancelled batches cannot be edited.",
            )
            return

        image_path = self.image_files[self.current_index]

        reply = QMessageBox.question(
            self,
            "Delete Photo?",
            f"Delete this photo?\n\n{image_path.name}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        try:
            deleted = self.storage.delete_image(image_path)
        except StorageError as error:
            self._handle_storage_failure(str(error))
            return

        if not deleted:
            QMessageBox.warning(
                self,
                "Delete Problem",
                "The photo could not be deleted.",
            )
            return

        self.current_index = -1
        self.refresh_image_list()
        self.session_photo_count = self.storage.current_photo_count()
        self._say(
            f"Photo deleted. Batch now has {self.session_photo_count} photos."
        )

    # ========================================================
    # USB
    # ========================================================

    def copy_current_to_usb(self):
        if self.usb_export_in_progress:
            self._say("A USB export is already in progress.")
            return
        if self.conveyor_running or self.capture_cycle_in_progress:
            self._say("Pause the belt before saving photos to USB.")
            return

        if not self.image_files or self.current_index < 0:
            QMessageBox.information(
                self,
                "No Photo Selected",
                "Choose a photo first.",
            )
            return

        image_path = self.image_files[self.current_index]

        try:
            destination = self.storage.copy_image_to_usb(image_path)

        except Exception as error:
            QMessageBox.warning(
                self,
                "USB Problem",
                f"The photo could not be copied.\n\n{error}",
            )
            return

        if destination is None:
            QMessageBox.information(
                self,
                "USB Not Found",
                "Insert a USB drive and try again.",
            )
            return

        self._say(f"Saved {image_path.name} to USB.")

    def copy_all_to_usb(self):
        try:
            manifest_path = self.storage._manifest_path()
        except StorageError as error:
            self._say(str(error))
            return
        self.start_usb_export_for_manifest(manifest_path)

    def start_usb_export_for_manifest(self, manifest_path):
        if self.usb_export_in_progress:
            self._say("A USB export is already in progress.")
            return False
        if self.conveyor_running or self.capture_cycle_in_progress:
            self._say("Pause the belt before saving the batch to USB.")
            return False

        try:
            manifest_path, manifest = self.storage.load_session_manifest(
                manifest_path
            )
            self.storage.get_session_images(manifest_path)
        except StorageError as error:
            QMessageBox.warning(
                self,
                "Batch Cannot Be Exported",
                str(error),
            )
            return False

        usb_mount = self.storage.find_usb_mount()
        if usb_mount is None:
            QMessageBox.information(
                self,
                "USB Not Found",
                "Insert a USB drive and try again.",
            )
            return False

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.finished.connect(self._on_usb_export_finished)
        process.errorOccurred.connect(self._on_usb_export_process_error)
        process.setProgram(sys.executable)
        process.setArguments(
            [
                str(Path(__file__).with_name("usb_export_worker.py")),
                str(manifest_path),
                str(usb_mount),
            ]
        )
        self.usb_export_process = process
        self.usb_export_manifest_path = manifest_path
        self.usb_export_in_progress = True
        self._say(
            f"Saving and verifying batch {manifest.get('barcode')} on USB. "
            "Keep the drive inserted..."
        )
        self._refresh_batch_ui()
        self.update_status_display()
        process.start()
        return True

    def _on_usb_export_process_error(self, _process_error):
        process = self.usb_export_process
        if process is None or process.state() != QProcess.NotRunning:
            return
        self._finish_usb_export(
            success=False,
            message=process.errorString(),
        )

    def _on_usb_export_finished(self, exit_code, _exit_status):
        process = self.usb_export_process
        if process is None:
            return

        output = bytes(process.readAll()).decode(
            "utf-8",
            errors="replace",
        ).strip()
        result = None
        for line in reversed(output.splitlines()):
            try:
                result = json.loads(line)
                break
            except ValueError:
                continue

        if exit_code == 0 and result and result.get("success"):
            copied = int(result.get("copied") or 0)
            destination = result.get("destination")
            self._finish_usb_export(
                success=True,
                message=(
                    f"Saved and verified {copied} batch photos plus manifest "
                    "on USB."
                ),
                copied=copied,
                destination=destination,
            )
            return

        details = "USB export failed."
        if result and result.get("error"):
            details = str(result["error"])
        elif output:
            details = output
        self._finish_usb_export(success=False, message=details)

    def _finish_usb_export(
        self,
        success,
        message,
        copied=0,
        destination=None,
    ):
        process = self.usb_export_process
        manifest_path = self.usb_export_manifest_path
        self.usb_export_process = None
        self.usb_export_manifest_path = None
        self.usb_export_in_progress = False
        if process is not None:
            process.deleteLater()

        audit_failure = False
        if success:
            try:
                self.storage.record_verified_usb_export(
                    manifest_path,
                    destination,
                    copied,
                )
            except Exception as error:
                success = False
                audit_failure = True
                message = (
                    "The image copies verified, but the final manifest/export "
                    f"audit could not be synchronized: {error}"
                )

        self._refresh_batch_ui()
        self.update_status_display()
        if success:
            self._say(message)
        elif audit_failure:
            self._say(
                "USB image copies completed, but export verification records "
                "need attention."
            )
            QMessageBox.warning(
                self,
                "USB Export Incomplete",
                f"{message}\n\nKeep the Raspberry Pi images and repeat the "
                "batch export after resolving the problem.",
            )
        else:
            self._say("USB export failed. Raspberry Pi images are unchanged.")
            QMessageBox.warning(
                self,
                "USB Export Failed",
                f"The Raspberry Pi images are unchanged.\n\n{message}",
            )

    # ========================================================
    # STATUS
    # ========================================================

    def update_status_display(self):
        # ----------------------------------------------------
        # SYSTEM
        # ----------------------------------------------------

        if self.storage.recovery_error or self.storage_fault_message:
            self.system_value.setText("STORAGE ERROR")
            self._set_status_color(self.system_value, "#c44949")

        elif self.recovery_pending:
            self.system_value.setText("BATCH RECOVERY")
            self._set_status_color(self.system_value, "#d9822b")

        elif self.reset_in_progress:
            self.system_value.setText("RESETTING")
            self._set_status_color(self.system_value, "#d9822b")

        elif self.sensor_fault:
            if self.sensor_fault_kind == "no_detection":
                self.system_value.setText("NO DETECTION")
            else:
                self.system_value.setText("SENSOR FAULT")
            self._set_status_color(self.system_value, "#c44949")

        elif self.direction_change_in_progress:
            self.system_value.setText("CHANGING")
            self._set_status_color(self.system_value, "#7b61d1")

        elif self.capture_cycle_in_progress:
            stage_text = {
                self.STAGE_TRAVELING: "POSITIONING",
                self.STAGE_SETTLING: "SETTLING",
                self.STAGE_CAPTURING: "TAKING PHOTO",
                self.STAGE_RESTARTING: "RESTARTING",
            }.get(self.capture_stage, "BUSY")

            self.system_value.setText(stage_text)
            self._set_status_color(self.system_value, "#c17b16")

        elif self.system_running:
            self.system_value.setText("RUNNING")
            self._set_status_color(self.system_value, "#1f8f62")

        else:
            self.system_value.setText("READY")
            self._set_status_color(self.system_value, "#52637a")

        # ----------------------------------------------------
        # BELT
        # ----------------------------------------------------

        if self.direction_change_in_progress:
            self.belt_value.setText("STOPPED")
            self._set_status_color(self.belt_value, "#c17b16")

        elif self.conveyor_running:
            self.belt_value.setText(self.selected_direction.upper())
            self._set_status_color(self.belt_value, "#1f8f62")

        else:
            self.belt_value.setText(
                f"STOPPED • {self.selected_direction.upper()}"
            )
            self._set_status_color(self.belt_value, "#c44949")

        # ----------------------------------------------------
        # SENSOR
        # ----------------------------------------------------

        detected = self.hardware.stem_detected()

        if self.sensor_fault:
            if self.sensor_fault_kind == "no_detection":
                self.sensor_value.setText("CHECK SENSOR")
            else:
                self.sensor_value.setText("FAULT")
            self._set_status_color(self.sensor_value, "#c44949")
        elif detected:
            self.sensor_value.setText("STEM SEEN")
            self._set_status_color(self.sensor_value, "#c17b16")
        else:
            self.sensor_value.setText("CLEAR")
            self._set_status_color(self.sensor_value, "#1f8f62")

        # ----------------------------------------------------
        # PHOTOS
        # ----------------------------------------------------

        self.photos_value.setText(str(self.storage.current_photo_count()))
        self._set_status_color(self.photos_value, "#2457d6")

        # ----------------------------------------------------
        # USB
        # ----------------------------------------------------

        usb_found = self.storage.find_usb_mount() is not None

        if self.usb_export_in_progress:
            self.usb_value.setText("SAVING")
            self._set_status_color(self.usb_value, "#d9822b")
        elif usb_found:
            self.usb_value.setText("READY")
            self._set_status_color(self.usb_value, "#1f8f62")
        else:
            self.usb_value.setText("NOT FOUND")
            self._set_status_color(self.usb_value, "#718096")

        # Avoid nonessential actions during an automatic cycle.
        active_manifest = self.storage.active_manifest
        expected = int(
            active_manifest.get("expected_count") or 0
        ) if active_manifest is not None else 0
        actual = self.storage.current_photo_count()
        if active_manifest is not None and expected > 0:
            self.expected_count_button.setText(
                f"EXPECTED SAMPLES\n{actual} / {expected}"
            )
        else:
            self.expected_count_button.setText(
                "EXPECTED SAMPLES\nNOT SET"
            )
        count_limit_reached = (
            expected > 0
            and actual >= expected
        )
        batch_ready = (
            active_manifest is not None
            and not self.recovery_pending
            and not self.storage.recovery_error
            and not self.storage_fault_message
            and not self.usb_export_in_progress
        )

        self.btn_start.setEnabled(
            batch_ready
            and self.camera.ready
            and not self.sensor_fault
            and not self.capture_cycle_in_progress
            and not self.reset_in_progress
            and not self.direction_change_in_progress
            and not count_limit_reached
        )
        self.btn_manual_capture.setEnabled(
            batch_ready
            and self.camera.ready
            and not self.capture_cycle_in_progress
            and not self.reset_in_progress
            and not self.direction_change_in_progress
            and not count_limit_reached
        )
        self.btn_reset.setEnabled(not self.reset_in_progress)

        batch_action_ready = (
            batch_ready
            and not self.capture_cycle_in_progress
            and not self.reset_in_progress
            and not self.direction_change_in_progress
        )
        self.btn_complete_batch.setEnabled(batch_action_ready)
        self.btn_cancel_batch.setEnabled(batch_action_ready)
        self.expected_count_button.setEnabled(
            batch_action_ready and not self.conveyor_running
        )
        storage_action_ready = (
            not self.usb_export_in_progress
            and not self.conveyor_running
            and not self.capture_cycle_in_progress
            and not self.direction_change_in_progress
            and not self.reset_in_progress
        )
        self.btn_copy_current.setEnabled(
            storage_action_ready and bool(self.image_files)
        )
        self.btn_copy_all.setEnabled(
            storage_action_ready and bool(self.image_files)
        )
        self.btn_batch_history.setEnabled(storage_action_ready)
        self.btn_delete.setEnabled(
            storage_action_ready
            and bool(self.image_files)
            and active_manifest is not None
        )

        self._update_direction_button()

    @staticmethod
    def _set_status_color(label, color):
        label.setStyleSheet(f"color: {color};")

    def _say(self, message):
        self.message_label.setText(message)

    def emergency_stop(self):
        """Best-effort software emergency stop used on errors and exit."""
        try:
            self.direction_timer.stop()
            self.cancel_capture_cycle()
        except Exception:
            pass

        self.system_running = False
        self.conveyor_running = False
        self.sensor_active_since = None
        self.no_detection_since = None

        try:
            self.hardware.conveyor_stop()
        except Exception:
            pass

    # ========================================================
    # RESIZE / CLOSE
    # ========================================================

    def resizeEvent(self, event):
        if hasattr(self, "image_preview"):
            self.show_current_image()

        super().resizeEvent(event)

    def closeEvent(self, event):
        self.emergency_stop()
        if self.usb_export_process is not None:
            self.usb_export_process.kill()
            self.usb_export_process.waitForFinished(1000)
        self.camera.close()
        self.hardware.cleanup()
        event.accept()
