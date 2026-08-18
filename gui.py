from datetime import datetime
from pathlib import Path
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
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
    IMAGE_DIR,
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
from storage import StorageManager


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
        self.session_photo_count = 0

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

        self.btn_start = QPushButton("â–¶  START BELT")
        self.btn_start.setObjectName("startButton")

        self.btn_stop = QPushButton("â–   STOP BELT")
        self.btn_stop.setObjectName("stopButton")

        self.btn_reset = QPushButton("â†»  RESET SYSTEM")
        self.btn_reset.setObjectName("resetButton")
        self.btn_reset.setToolTip(
            "Stops the conveyor, clears the current cycle, and restarts the camera."
        )

        self.btn_direction = QPushButton("â†”  CHANGE DIRECTION")
        self.btn_direction.setObjectName("directionButton")

        self.btn_manual_capture = QPushButton("CAMERA  TAKE PHOTO")
        self.btn_manual_capture.setObjectName("photoButton")

        self.btn_prev = QPushButton("â—€ Previous")
        self.btn_next = QPushButton("Next â–¶")
        self.btn_copy_current = QPushButton("Save This to USB")
        self.btn_copy_all = QPushButton("Save All to USB")
        self.btn_delete = QPushButton("Delete Photo")
        self.btn_exit = QPushButton("Exit")

        self.btn_open_photos = QPushButton("PHOTOS")
        self.btn_open_photos.setObjectName("photosButton")
        self.btn_open_photos.setToolTip("Open saved-photo management.")

        self.btn_back_to_main = QPushButton("â—€  BACK TO CONTROLS")
        self.btn_back_to_main.setObjectName("backButton")

        self.message_label = QLabel(
            "Ready! Press START BELT when everyone is clear of the conveyor."
        )
        self.message_label.setObjectName("messageBanner")
        self.message_label.setAlignment(Qt.AlignCenter)
        self.message_label.setWordWrap(True)

        self.auto_mode_label = QLabel(
            f"Auto mode: detect stem â†’ move {SENSOR_TO_STOP_DELAY_SEC:.1f} s "
            f"â†’ stop belt â†’ settle {BELT_SETTLE_DELAY_SEC:.1f} s "
            f"â†’ photo â†’ restart. No detection for "
            f"{NO_DETECTION_TIMEOUT_SEC:.0f} s â†’ belt stops."
        )
        self.auto_mode_label.setObjectName("smallHint")
        self.auto_mode_label.setAlignment(Qt.AlignCenter)

    def _connect_ui_signals(self):
        self.btn_start.clicked.connect(self.start_system)
        self.btn_stop.clicked.connect(self.stop_system)
        self.btn_reset.clicked.connect(self.reset_system)
        self.btn_direction.clicked.connect(self.toggle_direction)
        self.btn_manual_capture.clicked.connect(self.manual_capture)

        self.btn_prev.clicked.connect(self.previous_image)
        self.btn_next.clicked.connect(self.next_image)
        self.btn_copy_current.clicked.connect(self.copy_current_to_usb)
        self.btn_copy_all.clicked.connect(self.copy_all_to_usb)
        self.btn_delete.clicked.connect(self.delete_current_image)
        self.btn_exit.clicked.connect(self.close)
        self.image_list.itemClicked.connect(self.select_image_from_list)

        self.btn_open_photos.clicked.connect(self._show_photo_page)
        self.btn_back_to_main.clicked.connect(self._show_control_page)

    def _build_desktop_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        root.addWidget(self.header)
        root.addWidget(self.subtitle)
        root.addLayout(self._make_status_row(10))

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
    # SYSTEM CONTROL
    # ========================================================

    def start_system(self):
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

        self._say("Belt stopped.")
        self.update_status_display()

    def reset_system(self):
        """Return the application to a safe known state.

        RESET SYSTEM always stops the conveyor first, cancels software timing,
        clears the current capture cycle, and restarts only the isolated camera
        worker. It never restarts the conveyor automatically. A stuck-ACTIVE
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
            self.btn_direction.setText("â†”  WAIT FOR RESET")
            self.btn_direction.setEnabled(False)
            return

        if self.sensor_fault:
            if self.sensor_fault_kind == "no_detection":
                self.btn_direction.setText("â†”  CHECK SENSOR")
            else:
                self.btn_direction.setText("â†”  SENSOR FAULT")
            self.btn_direction.setEnabled(False)
            return

        if not self.hardware.reverse_configured:
            self.btn_direction.setText("â†”  REVERSE NOT SET UP")
            self.btn_direction.setEnabled(False)
            self.btn_direction.setToolTip(
                "Set REVERSE_RELAY_PIN in config.py after confirming the wiring."
            )
            return

        if self.direction_change_in_progress:
            self.btn_direction.setText("â†”  CHANGING...")
            self.btn_direction.setEnabled(False)
            return

        if self.capture_cycle_in_progress:
            self.btn_direction.setText("â†”  WAIT FOR PHOTO")
            self.btn_direction.setEnabled(False)
            return

        self.btn_direction.setEnabled(True)

        if self.selected_direction == "forward":
            self.btn_direction.setText("â†”  SWITCH TO REVERSE")
        else:
            self.btn_direction.setText("â†”  SWITCH TO FORWARD")

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

        self.session_photo_count += 1
        self.refresh_image_list(preferred_path=save_path)

        if self.restart_after_capture and self.system_running:
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
        # Milliseconds are included so quick captures do not overwrite.
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        save_path = IMAGE_DIR / f"stem_{source}_{timestamp}.jpg"

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
            self.image_preview.setText(
                "No photos yet\n\nPress START BELT to begin."
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
        if not self.image_files or self.current_index < 0:
            QMessageBox.information(
                self,
                "No Photo Selected",
                "Choose a photo first.",
            )
            return

        image_path = self.image_files[self.current_index]

        reply = QMessageBox.question(
            self,
            "Delete Photo?",
            f"Delete this photo?\n\n{image_path.name}",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        if not self.storage.delete_image(image_path):
            QMessageBox.warning(
                self,
                "Delete Problem",
                "The photo could not be deleted.",
            )
            return

        self.current_index = -1
        self.refresh_image_list()
        self._say("Photo deleted.")

    # ========================================================
    # USB
    # ========================================================

    def copy_current_to_usb(self):
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
        if not self.image_files:
            QMessageBox.information(
                self,
                "No Photos",
                "There are no photos to copy.",
            )
            return

        try:
            copied = self.storage.copy_all_images_to_usb(self.image_files)

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

        self._say(f"Saved {copied} photos to USB.")

    # ========================================================
    # STATUS
    # ========================================================

    def update_status_display(self):
        # ----------------------------------------------------
        # SYSTEM
        # ----------------------------------------------------

        if self.reset_in_progress:
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
                f"STOPPED â€¢ {self.selected_direction.upper()}"
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

        self.photos_value.setText(str(len(self.image_files)))
        self._set_status_color(self.photos_value, "#2457d6")

        # ----------------------------------------------------
        # USB
        # ----------------------------------------------------

        usb_found = self.storage.find_usb_mount() is not None

        if usb_found:
            self.usb_value.setText("READY")
            self._set_status_color(self.usb_value, "#1f8f62")
        else:
            self.usb_value.setText("NOT FOUND")
            self._set_status_color(self.usb_value, "#718096")

        # Avoid nonessential actions during an automatic cycle.
        self.btn_start.setEnabled(
            self.camera.ready
            and not self.sensor_fault
            and not self.capture_cycle_in_progress
            and not self.reset_in_progress
            and not self.direction_change_in_progress
        )
        self.btn_manual_capture.setEnabled(
            self.camera.ready
            and not self.capture_cycle_in_progress
            and not self.reset_in_progress
            and not self.direction_change_in_progress
        )
        self.btn_reset.setEnabled(not self.reset_in_progress)

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
        self.camera.close()
        self.hardware.cleanup()
        event.accept()