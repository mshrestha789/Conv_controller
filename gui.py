from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QFrame,
)

from config import (
    APP_TITLE,
    IMAGE_DIR,
    DEFAULT_DIRECTION,
    DIRECTION_CHANGE_DEAD_TIME_MS,
    SENSOR_TO_CAMERA_DELAY_SEC,
    AUTO_CAPTURE_FORWARD,
    AUTO_CAPTURE_REVERSE,
    SENSOR_POLL_MS,
    STATUS_UPDATE_MS,
)

from hardware import Hardware
from camera import Camera
from storage import StorageManager


class StemConveyorGUI(QWidget):
    """
    Touch-friendly conveyor imaging interface.

    Normal automatic sequence:
        1. Start conveyor.
        2. Sensor detects a stem.
        3. Conveyor keeps moving.
        4. App waits SENSOR_TO_CAMERA_DELAY_SEC.
        5. Camera captures one image.
        6. Conveyor keeps moving for the next stem.
    """

    def __init__(self):
        super().__init__()

        # ====================================================
        # MODULES
        # ====================================================

        self.hardware = Hardware()
        self.camera = Camera()
        self.storage = StorageManager()

        # ====================================================
        # SYSTEM STATE
        # ====================================================

        self.system_running = False
        self.conveyor_running = False

        self.selected_direction = DEFAULT_DIRECTION
        self.direction_change_in_progress = False

        # Used for rising-edge detection so one physical stem
        # does not schedule many photos while the sensor stays active.
        self.sensor_was_active = False

        # Each detected stem gets its own cancellable timer.
        self.pending_capture_timers = []

        # ====================================================
        # IMAGE STATE
        # ====================================================

        self.image_files = []
        self.current_index = -1
        self.session_photo_count = 0

        # ====================================================
        # WINDOW
        # ====================================================

        self.setWindowTitle(APP_TITLE)
        self.resize(1180, 760)
        self.setMinimumSize(900, 620)

        self._build_ui()
        self._apply_styles()
        self.refresh_image_list()

        # ====================================================
        # SENSOR TIMER
        # ====================================================

        self.sensor_timer = QTimer(self)
        self.sensor_timer.timeout.connect(
            self.check_sensor
        )
        self.sensor_timer.start(
            SENSOR_POLL_MS
        )

        # ====================================================
        # STATUS TIMER
        # ====================================================

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(
            self.update_status_display
        )
        self.status_timer.start(
            STATUS_UPDATE_MS
        )

        # ====================================================
        # DIRECTION-CHANGE TIMER
        # ====================================================

        self.direction_timer = QTimer(self)
        self.direction_timer.setSingleShot(True)
        self.direction_timer.timeout.connect(
            self._finish_direction_change
        )

        self.pending_direction = None

        self.update_status_display()
        self._update_direction_button()

    # ========================================================
    # UI CREATION
    # ========================================================

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # ----------------------------------------------------
        # HEADER
        # ----------------------------------------------------

        header = QLabel("STEM IMAGING STATION")
        header.setObjectName("header")
        header.setAlignment(Qt.AlignCenter)

        subtitle = QLabel(
            "Put stems on the belt. The sensor spots them and the camera takes the photo automatically."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)

        root.addWidget(header)
        root.addWidget(subtitle)

        # ----------------------------------------------------
        # STATUS CARDS
        # ----------------------------------------------------

        status_row = QHBoxLayout()
        status_row.setSpacing(10)

        self.system_value = self._make_status_card(
            status_row,
            "SYSTEM",
            "READY",
        )

        self.belt_value = self._make_status_card(
            status_row,
            "BELT",
            "STOPPED",
        )

        self.sensor_value = self._make_status_card(
            status_row,
            "SENSOR",
            "CLEAR",
        )

        self.photos_value = self._make_status_card(
            status_row,
            "PHOTOS",
            "0",
        )

        self.usb_value = self._make_status_card(
            status_row,
            "USB",
            "NOT FOUND",
        )

        root.addLayout(status_row)

        # ----------------------------------------------------
        # MAIN CONTENT
        # ----------------------------------------------------

        middle = QHBoxLayout()
        middle.setSpacing(14)

        # Image preview
        preview_panel = QFrame()
        preview_panel.setObjectName("panel")

        preview_layout = QVBoxLayout(preview_panel)

        preview_title = QLabel("Latest Photo")
        preview_title.setObjectName("sectionTitle")

        self.image_preview = QLabel(
            "No photos yet\n\nPress START BELT to begin."
        )
        self.image_preview.setObjectName("imagePreview")
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.image_preview.setMinimumSize(620, 360)

        preview_layout.addWidget(preview_title)
        preview_layout.addWidget(
            self.image_preview,
            stretch=1,
        )

        # Photo list
        list_panel = QFrame()
        list_panel.setObjectName("panel")

        list_layout = QVBoxLayout(list_panel)

        list_title = QLabel("Saved Photos")
        list_title.setObjectName("sectionTitle")

        list_hint = QLabel(
            "Tap a photo to view it."
        )
        list_hint.setObjectName("smallHint")

        self.image_list = QListWidget()
        self.image_list.setObjectName("imageList")
        self.image_list.setMinimumWidth(300)
        self.image_list.itemClicked.connect(
            self.select_image_from_list
        )

        list_layout.addWidget(list_title)
        list_layout.addWidget(list_hint)
        list_layout.addWidget(
            self.image_list,
            stretch=1,
        )

        middle.addWidget(
            preview_panel,
            stretch=3,
        )
        middle.addWidget(
            list_panel,
            stretch=1,
        )

        root.addLayout(
            middle,
            stretch=1,
        )

        # ----------------------------------------------------
        # MAIN CONTROLS
        # ----------------------------------------------------

        control_row = QHBoxLayout()
        control_row.setSpacing(10)

        self.btn_start = QPushButton(
            "▶  START BELT"
        )
        self.btn_start.setObjectName(
            "startButton"
        )

        self.btn_stop = QPushButton(
            "■  STOP BELT"
        )
        self.btn_stop.setObjectName(
            "stopButton"
        )

        self.btn_direction = QPushButton(
            "↔  CHANGE DIRECTION"
        )
        self.btn_direction.setObjectName(
            "directionButton"
        )

        self.btn_manual_capture = QPushButton(
            "CAMERA  TAKE PHOTO"
        )
        self.btn_manual_capture.setObjectName(
            "photoButton"
        )

        self.btn_start.clicked.connect(
            self.start_system
        )
        self.btn_stop.clicked.connect(
            self.stop_system
        )
        self.btn_direction.clicked.connect(
            self.toggle_direction
        )
        self.btn_manual_capture.clicked.connect(
            self.manual_capture
        )

        for button in (
            self.btn_start,
            self.btn_stop,
            self.btn_direction,
            self.btn_manual_capture,
        ):
            button.setMinimumHeight(66)
            control_row.addWidget(button)

        root.addLayout(control_row)

        # ----------------------------------------------------
        # PHOTO ACTIONS
        # ----------------------------------------------------

        photo_row = QHBoxLayout()
        photo_row.setSpacing(8)

        self.btn_prev = QPushButton(
            "◀ Previous"
        )
        self.btn_next = QPushButton(
            "Next ▶"
        )
        self.btn_copy_current = QPushButton(
            "Save This to USB"
        )
        self.btn_copy_all = QPushButton(
            "Save All to USB"
        )
        self.btn_delete = QPushButton(
            "Delete Photo"
        )
        self.btn_exit = QPushButton(
            "Exit"
        )

        self.btn_prev.clicked.connect(
            self.previous_image
        )
        self.btn_next.clicked.connect(
            self.next_image
        )
        self.btn_copy_current.clicked.connect(
            self.copy_current_to_usb
        )
        self.btn_copy_all.clicked.connect(
            self.copy_all_to_usb
        )
        self.btn_delete.clicked.connect(
            self.delete_current_image
        )
        self.btn_exit.clicked.connect(
            self.close
        )

        for button in (
            self.btn_prev,
            self.btn_next,
            self.btn_copy_current,
            self.btn_copy_all,
            self.btn_delete,
            self.btn_exit,
        ):
            button.setMinimumHeight(46)
            button.setObjectName(
                "smallButton"
            )
            photo_row.addWidget(button)

        root.addLayout(photo_row)

        # ----------------------------------------------------
        # MESSAGE BANNER
        # ----------------------------------------------------

        self.message_label = QLabel(
            "Ready! Press START BELT when everyone is clear of the conveyor."
        )
        self.message_label.setObjectName(
            "messageBanner"
        )
        self.message_label.setAlignment(
            Qt.AlignCenter
        )
        self.message_label.setWordWrap(True)

        root.addWidget(
            self.message_label
        )

        self.auto_mode_label = QLabel(
            f"Auto photo: sensor detects a stem → waits {SENSOR_TO_CAMERA_DELAY_SEC:.1f} s → takes one photo."
        )
        self.auto_mode_label.setObjectName(
            "smallHint"
        )
        self.auto_mode_label.setAlignment(
            Qt.AlignCenter
        )

        root.addWidget(
            self.auto_mode_label
        )

    def _make_status_card(
        self,
        layout,
        title,
        value,
    ):
        card = QFrame()
        card.setObjectName("statusCard")

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(
            8,
            7,
            8,
            7,
        )
        card_layout.setSpacing(2)

        title_label = QLabel(title)
        title_label.setObjectName(
            "statusTitle"
        )
        title_label.setAlignment(
            Qt.AlignCenter
        )

        value_label = QLabel(value)
        value_label.setObjectName(
            "statusValue"
        )
        value_label.setAlignment(
            Qt.AlignCenter
        )

        card_layout.addWidget(
            title_label
        )
        card_layout.addWidget(
            value_label
        )

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

    # ========================================================
    # SYSTEM CONTROL
    # ========================================================

    def start_system(self):
        if self.direction_change_in_progress:
            self._say(
                "Direction is changing. Please wait."
            )
            return

        if self.system_running and self.conveyor_running:
            self._say(
                "The belt is already running."
            )
            return

        started = self.hardware.conveyor_start(
            self.selected_direction
        )

        if not started:
            QMessageBox.warning(
                self,
                "Reverse Not Set Up",
                "Reverse control is not configured yet.\n\n"
                "Set REVERSE_RELAY_PIN in config.py after confirming the actual wiring.",
            )
            return

        self.system_running = True
        self.conveyor_running = True

        # If a stem is already sitting on the sensor when START
        # is pressed, wait for it to clear before accepting a
        # new rising edge.
        self.sensor_was_active = (
            self.hardware.stem_detected()
        )

        direction_text = (
            self.selected_direction.upper()
        )

        self._say(
            f"Belt started {direction_text}. Automatic photo mode is ready."
        )

        self.update_status_display()

    def stop_system(self):
        self.direction_timer.stop()
        self.direction_change_in_progress = False
        self.pending_direction = None

        self.cancel_pending_captures()

        self.system_running = False
        self.conveyor_running = False

        self.hardware.conveyor_stop()

        self._say(
            "Belt stopped. Pending automatic photos were canceled."
        )

        self.update_status_display()

    # ========================================================
    # DIRECTION
    # ========================================================

    def toggle_direction(self):
        if not self.hardware.reverse_configured:
            QMessageBox.information(
                self,
                "Reverse Control Not Configured",
                "The direction button is ready in the software, but the reverse GPIO pin is not set.\n\n"
                "After your reverse-control hardware is wired, set REVERSE_RELAY_PIN in config.py.",
            )
            return

        if self.direction_change_in_progress:
            self._say(
                "Direction is already changing."
            )
            return

        target = (
            "reverse"
            if self.selected_direction == "forward"
            else "forward"
        )

        # Any photo scheduled from the old travel direction is
        # no longer geometrically valid after a reversal.
        self.cancel_pending_captures()

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
        self.hardware.conveyor_stop()

        self._say(
            f"Changing direction to {target.upper()}..."
        )

        self.btn_direction.setEnabled(
            False
        )

        self.direction_timer.start(
            DIRECTION_CHANGE_DEAD_TIME_MS
        )

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
            started = self.hardware.conveyor_start(
                target
            )

            if started:
                self.conveyor_running = True

                self._say(
                    f"Direction changed. Belt is now moving {target.upper()}."
                )

            else:
                self.conveyor_running = False
                self.system_running = False

                self._say(
                    "Could not start in the new direction."
                )

        self._update_direction_button()
        self.update_status_display()

    def _update_direction_button(self):
        if not self.hardware.reverse_configured:
            self.btn_direction.setText(
                "↔  REVERSE NOT SET UP"
            )
            self.btn_direction.setEnabled(
                False
            )
            self.btn_direction.setToolTip(
                "Set REVERSE_RELAY_PIN in config.py after confirming the wiring."
            )
            return

        if self.direction_change_in_progress:
            self.btn_direction.setText(
                "↔  CHANGING..."
            )
            self.btn_direction.setEnabled(
                False
            )
            return

        self.btn_direction.setEnabled(
            True
        )

        if self.selected_direction == "forward":
            self.btn_direction.setText(
                "↔  SWITCH TO REVERSE"
            )
        else:
            self.btn_direction.setText(
                "↔  SWITCH TO FORWARD"
            )

    # ========================================================
    # SENSOR + AUTOMATIC CAPTURE
    # ========================================================

    def check_sensor(self):
        detected = self.hardware.stem_detected()

        # Rising edge: CLEAR -> DETECTED
        if detected and not self.sensor_was_active:
            self.sensor_was_active = True

            if (
                self.system_running
                and self.conveyor_running
                and self._auto_capture_allowed()
            ):
                self.schedule_automatic_capture()

            elif (
                self.system_running
                and self.conveyor_running
                and not self._auto_capture_allowed()
            ):
                self._say(
                    f"Stem detected, but auto photo is OFF in {self.selected_direction.upper()} mode."
                )

        # Rearm only after the stem leaves the sensor.
        elif not detected:
            self.sensor_was_active = False

    def _auto_capture_allowed(self):
        if self.selected_direction == "forward":
            return AUTO_CAPTURE_FORWARD

        return AUTO_CAPTURE_REVERSE

    def schedule_automatic_capture(self):
        timer = QTimer(self)
        timer.setSingleShot(True)

        trigger_direction = self.selected_direction

        timer.timeout.connect(
            lambda t=timer, d=trigger_direction:
            self._run_scheduled_capture(t, d)
        )

        self.pending_capture_timers.append(
            timer
        )

        timer.start(
            int(
                SENSOR_TO_CAMERA_DELAY_SEC
                * 1000
            )
        )

        queue_count = len(
            self.pending_capture_timers
        )

        self._say(
            f"Stem spotted! Photo in {SENSOR_TO_CAMERA_DELAY_SEC:.1f} seconds. "
            f"Queued photos: {queue_count}."
        )

    def _run_scheduled_capture(
        self,
        timer,
        trigger_direction,
    ):
        if timer in self.pending_capture_timers:
            self.pending_capture_timers.remove(
                timer
            )

        timer.deleteLater()

        # If STOP or direction change happened after detection,
        # do not take a now-invalid automatic image.
        if not self.system_running:
            return

        if not self.conveyor_running:
            return

        if (
            self.selected_direction
            != trigger_direction
        ):
            return

        if not self._auto_capture_allowed():
            return

        self.capture_image(
            source="auto"
        )

    def cancel_pending_captures(self):
        for timer in self.pending_capture_timers:
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                pass

        self.pending_capture_timers.clear()

    # ========================================================
    # MANUAL PHOTO
    # ========================================================

    def manual_capture(self):
        self._say(
            "Taking a photo now..."
        )

        self.capture_image(
            source="manual"
        )

    # ========================================================
    # IMAGE CAPTURE
    # ========================================================

    def capture_image(
        self,
        source="auto",
    ):
        # Milliseconds are included so two quick captures do not
        # overwrite each other.
        timestamp = (
            datetime.now()
            .strftime(
                "%Y%m%d_%H%M%S_%f"
            )[:-3]
        )

        save_path = (
            IMAGE_DIR
            / f"stem_{source}_{timestamp}.jpg"
        )

        success = self.camera.capture(
            save_path
        )

        if not success:
            self._say(
                "Camera problem: photo was not saved."
            )

            QMessageBox.warning(
                self,
                "Camera Problem",
                "The camera could not take a photo.",
            )
            return False

        self.session_photo_count += 1

        self.refresh_image_list(
            preferred_path=save_path
        )

        if source == "auto":
            self._say(
                f"Photo captured automatically: {save_path.name}"
            )
        else:
            self._say(
                f"Manual photo saved: {save_path.name}"
            )

        return True

    # ========================================================
    # IMAGE LIST
    # ========================================================

    def refresh_image_list(
        self,
        preferred_path=None,
    ):
        old_path = None

        if (
            self.image_files
            and 0 <= self.current_index < len(
                self.image_files
            )
        ):
            old_path = self.image_files[
                self.current_index
            ]

        self.image_files = (
            self.storage.get_images()
        )

        self.image_list.clear()

        for number, image_path in enumerate(
            self.image_files,
            start=1,
        ):
            item = QListWidgetItem(
                f"{number}. {image_path.name}"
            )

            self.image_list.addItem(
                item
            )

        if not self.image_files:
            self.current_index = -1
            self.image_preview.clear()
            self.image_preview.setText(
                "No photos yet\n\nPress START BELT to begin."
            )
            self.update_status_display()
            return

        target_path = (
            preferred_path
            or old_path
        )

        if (
            target_path is not None
            and target_path in self.image_files
        ):
            self.current_index = (
                self.image_files.index(
                    target_path
                )
            )

        elif self.current_index < 0:
            self.current_index = (
                len(self.image_files) - 1
            )

        elif self.current_index >= len(
            self.image_files
        ):
            self.current_index = (
                len(self.image_files) - 1
            )

        self.show_current_image()
        self.update_status_display()

    def show_current_image(self):
        if (
            not self.image_files
            or self.current_index < 0
            or self.current_index >= len(
                self.image_files
            )
        ):
            self.image_preview.clear()
            self.image_preview.setText(
                "No photo selected."
            )
            return

        image_path = (
            self.image_files[
                self.current_index
            ]
        )

        pixmap = QPixmap(
            str(image_path)
        )

        if pixmap.isNull():
            self.image_preview.clear()
            self.image_preview.setText(
                "This photo could not be opened."
            )
            return

        scaled = pixmap.scaled(
            self.image_preview.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        self.image_preview.setPixmap(
            scaled
        )

        self.image_list.setCurrentRow(
            self.current_index
        )

    def select_image_from_list(
        self,
        item,
    ):
        row = self.image_list.row(
            item
        )

        if 0 <= row < len(
            self.image_files
        ):
            self.current_index = row
            self.show_current_image()

    def previous_image(self):
        if not self.image_files:
            return

        self.current_index = max(
            0,
            self.current_index - 1,
        )

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
        if (
            not self.image_files
            or self.current_index < 0
        ):
            QMessageBox.information(
                self,
                "No Photo Selected",
                "Choose a photo first.",
            )
            return

        image_path = (
            self.image_files[
                self.current_index
            ]
        )

        reply = QMessageBox.question(
            self,
            "Delete Photo?",
            f"Delete this photo?\n\n{image_path.name}",
            QMessageBox.Yes
            | QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        if not self.storage.delete_image(
            image_path
        ):
            QMessageBox.warning(
                self,
                "Delete Problem",
                "The photo could not be deleted.",
            )
            return

        self.current_index = -1
        self.refresh_image_list()

        self._say(
            "Photo deleted."
        )

    # ========================================================
    # USB
    # ========================================================

    def copy_current_to_usb(self):
        if (
            not self.image_files
            or self.current_index < 0
        ):
            QMessageBox.information(
                self,
                "No Photo Selected",
                "Choose a photo first.",
            )
            return

        image_path = (
            self.image_files[
                self.current_index
            ]
        )

        try:
            destination = (
                self.storage.copy_image_to_usb(
                    image_path
                )
            )

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

        self._say(
            f"Saved {image_path.name} to USB."
        )

    def copy_all_to_usb(self):
        if not self.image_files:
            QMessageBox.information(
                self,
                "No Photos",
                "There are no photos to copy.",
            )
            return

        try:
            copied = (
                self.storage.copy_all_images_to_usb(
                    self.image_files
                )
            )

        except Exception as error:
            QMessageBox.warning(
                self,
                "USB Problem",
                f"Photos could not be copied.\n\n{error}",
            )
            return

        if copied is None:
            QMessageBox.information(
                self,
                "USB Not Found",
                "Insert a USB drive and try again.",
            )
            return

        self._say(
            f"Saved {copied} photos to USB."
        )

    # ========================================================
    # STATUS
    # ========================================================

    def update_status_display(self):
        # System
        if self.direction_change_in_progress:
            self.system_value.setText(
                "CHANGING"
            )
            self._set_status_color(
                self.system_value,
                "#7b61d1",
            )

        elif self.system_running:
            self.system_value.setText(
                "RUNNING"
            )
            self._set_status_color(
                self.system_value,
                "#1f8f62",
            )

        else:
            self.system_value.setText(
                "READY"
            )
            self._set_status_color(
                self.system_value,
                "#52637a",
            )

        # Belt
        if self.direction_change_in_progress:
            self.belt_value.setText(
                "STOPPING"
            )
            self._set_status_color(
                self.belt_value,
                "#c17b16",
            )

        elif self.conveyor_running:
            self.belt_value.setText(
                self.selected_direction.upper()
            )

            self._set_status_color(
                self.belt_value,
                "#1f8f62",
            )

        else:
            self.belt_value.setText(
                f"STOPPED • {self.selected_direction.upper()}"
            )

            self._set_status_color(
                self.belt_value,
                "#c44949",
            )

        # Sensor
        detected = (
            self.hardware.stem_detected()
        )

        if detected:
            self.sensor_value.setText(
                "STEM SEEN"
            )
            self._set_status_color(
                self.sensor_value,
                "#c17b16",
            )
        else:
            self.sensor_value.setText(
                "CLEAR"
            )
            self._set_status_color(
                self.sensor_value,
                "#1f8f62",
            )

        # Photos
        self.photos_value.setText(
            str(
                len(self.image_files)
            )
        )

        self._set_status_color(
            self.photos_value,
            "#2457d6",
        )

        # USB
        usb_found = (
            self.storage.find_usb_mount()
            is not None
        )

        if usb_found:
            self.usb_value.setText(
                "READY"
            )
            self._set_status_color(
                self.usb_value,
                "#1f8f62",
            )
        else:
            self.usb_value.setText(
                "NOT FOUND"
            )
            self._set_status_color(
                self.usb_value,
                "#718096",
            )

        self._update_direction_button()

    @staticmethod
    def _set_status_color(
        label,
        color,
    ):
        label.setStyleSheet(
            f"color: {color};"
        )

    def _say(
        self,
        message,
    ):
        self.message_label.setText(
            message
        )

    # ========================================================
    # RESIZE / CLOSE
    # ========================================================

    def resizeEvent(
        self,
        event,
    ):
        if hasattr(
            self,
            "image_preview",
        ):
            self.show_current_image()

        super().resizeEvent(
            event
        )

    def closeEvent(
        self,
        event,
    ):
        self.direction_timer.stop()

        self.cancel_pending_captures()

        self.system_running = False
        self.conveyor_running = False

        self.hardware.cleanup()
        self.camera.close()

        event.accept()
