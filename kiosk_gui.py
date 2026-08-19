import subprocess
import time

from PySide6.QtCore import Qt, QTimer, QRectF, QSize
from PySide6.QtGui import (
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import camera as camera_module
import config
import gui as gui_module
import hardware as hardware_module
from developer_auth import DeveloperAuth
from gui import StemConveyorGUI
from runtime_settings import RuntimeSettings


def make_power_icon(size=28):
    """Return a vector-drawn power icon that does not depend on a font glyph."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    pen = QPen(QColor("#2e3b55"), max(2, size // 10))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)

    margin = size * 0.18
    circle_rect = QRectF(
        margin,
        size * 0.24,
        size - 2 * margin,
        size - 2 * margin,
    )

    # 270-degree arc leaves the opening at the top.
    painter.drawArc(circle_rect, 135 * 16, 270 * 16)

    center_x = size / 2
    painter.drawLine(
        int(center_x),
        int(size * 0.10),
        int(center_x),
        int(size * 0.48),
    )

    painter.end()
    return QIcon(pixmap)


class ConfigurationDialog(QDialog):
    """Protected operational calibration page.

    GPIO assignments and relay polarity are intentionally not editable here.
    """

    def __init__(self, owner, settings_manager, values):
        super().__init__(owner)
        self.owner = owner
        self.settings_manager = settings_manager
        self.values = dict(values)

        self.setWindowTitle("Developer Configuration")
        self.setModal(True)

        # Keep the dialog inside the usable display area. A fixed 620-pixel
        # height is too tall for a 1024 x 600 touchscreen after window
        # decorations are included.
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            dialog_width = min(760, max(1, available.width() - 40))
            dialog_height = min(620, max(1, available.height() - 70))
            self.resize(dialog_width, dialog_height)
            self.setMinimumSize(
                min(520, dialog_width),
                min(360, dialog_height),
            )
        else:
            self.resize(600, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Only the settings content scrolls. The action buttons remain fixed
        # and visible at the bottom of the dialog on small touchscreens.
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content = QWidget(scroll_area)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 8, 4)
        content_layout.setSpacing(8)
        scroll_area.setWidget(content)
        root.addWidget(scroll_area, 1)

        title = QLabel("DEVELOPER CONFIGURATION")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 800;")
        content_layout.addWidget(title)

        info = QLabel(
            "The conveyor is stopped while this page is open. "
            "Wiring pins and active-low relay polarity are locked in config.py."
        )
        info.setWordWrap(True)
        info.setStyleSheet("padding: 8px; color: #52637a;")
        content_layout.addWidget(info)

        form = QFormLayout()
        form.setSpacing(8)

        self.sensor_polarity = QComboBox()
        self.sensor_polarity.addItem("Active LOW", False)
        self.sensor_polarity.addItem("Active HIGH", True)
        index = self.sensor_polarity.findData(values["sensor_active_high"])
        self.sensor_polarity.setCurrentIndex(max(index, 0))
        form.addRow("Sensor detected state", self.sensor_polarity)

        self.sensor_bounce = self._double_box(
            values["sensor_bounce_time_sec"], 0.0, 1.0, 0.01, 2, " s"
        )
        form.addRow("Sensor debounce", self.sensor_bounce)

        self.stuck_timeout = self._double_box(
            values["sensor_stuck_active_timeout_sec"],
            1.0,
            120.0,
            0.5,
            1,
            " s",
        )
        form.addRow("Sensor stuck-active timeout", self.stuck_timeout)

        self.no_detection_timeout = self._double_box(
            values["no_detection_timeout_sec"],
            5.0,
            600.0,
            5.0,
            1,
            " s",
        )
        form.addRow("No-detection timeout", self.no_detection_timeout)

        self.sensor_to_stop = self._double_box(
            values["sensor_to_stop_delay_sec"], 0.05, 10.0, 0.05, 2, " s"
        )
        form.addRow("Sensor → stop delay", self.sensor_to_stop)

        self.belt_settle = self._double_box(
            values["belt_settle_delay_sec"], 0.0, 5.0, 0.05, 2, " s"
        )
        form.addRow("Belt settle delay", self.belt_settle)

        self.post_capture = self._double_box(
            values["post_capture_delay_sec"], 0.0, 5.0, 0.05, 2, " s"
        )
        form.addRow("Post-photo restart delay", self.post_capture)

        self.direction_dead_time = QSpinBox()
        self.direction_dead_time.setRange(100, 5000)
        self.direction_dead_time.setSingleStep(100)
        self.direction_dead_time.setSuffix(" ms")
        self.direction_dead_time.setValue(
            values["direction_change_dead_time_ms"]
        )
        form.addRow("Direction-change dead time", self.direction_dead_time)

        self.camera_timeout = self._double_box(
            values["camera_capture_timeout_sec"],
            2.0,
            60.0,
            1.0,
            1,
            " s",
        )
        form.addRow("Camera capture timeout", self.camera_timeout)

        content_layout.addLayout(form)

        hardware_summary = QLabel(
            f"Locked hardware: Sensor GPIO {config.PROXIMITY_PIN} • "
            f"Forward GPIO {config.FORWARD_RELAY_PIN} • "
            f"Reverse GPIO {config.REVERSE_RELAY_PIN} • "
            "Relays ACTIVE LOW"
        )
        hardware_summary.setWordWrap(True)
        hardware_summary.setStyleSheet(
            "background: #eef2f7; padding: 8px; border-radius: 6px;"
        )
        content_layout.addWidget(hardware_summary)

        self.sensor_test = QLabel("Sensor input: checking...")
        self.sensor_test.setAlignment(Qt.AlignCenter)
        self.sensor_test.setStyleSheet(
            "font-size: 16px; font-weight: 700; padding: 8px;"
        )
        content_layout.addWidget(self.sensor_test)

        # Keep every action outside the scrolling region so the operator can
        # always save, cancel, or restore defaults without hidden controls.
        action_row = QHBoxLayout()
        restore_button = QPushButton("Restore Defaults")
        restore_button.clicked.connect(self._restore_defaults)
        action_row.addWidget(restore_button)
        action_row.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        action_row.addWidget(buttons)
        root.addLayout(action_row)

        self.sensor_timer = QTimer(self)
        self.sensor_timer.timeout.connect(self._update_sensor_test)
        self.sensor_timer.start(250)
        self._update_sensor_test()

    @staticmethod
    def _double_box(value, minimum, maximum, step, decimals, suffix):
        box = QDoubleSpinBox()
        box.setRange(minimum, maximum)
        box.setSingleStep(step)
        box.setDecimals(decimals)
        box.setSuffix(suffix)
        box.setValue(value)
        return box

    def _restore_defaults(self):
        defaults = self.settings_manager.restore_defaults()
        self.sensor_polarity.setCurrentIndex(
            self.sensor_polarity.findData(defaults["sensor_active_high"])
        )
        self.sensor_bounce.setValue(defaults["sensor_bounce_time_sec"])
        self.stuck_timeout.setValue(
            defaults["sensor_stuck_active_timeout_sec"]
        )
        self.no_detection_timeout.setValue(
            defaults["no_detection_timeout_sec"]
        )
        self.sensor_to_stop.setValue(defaults["sensor_to_stop_delay_sec"])
        self.belt_settle.setValue(defaults["belt_settle_delay_sec"])
        self.post_capture.setValue(defaults["post_capture_delay_sec"])
        self.direction_dead_time.setValue(
            defaults["direction_change_dead_time_ms"]
        )
        self.camera_timeout.setValue(
            defaults["camera_capture_timeout_sec"]
        )

    def _update_sensor_test(self):
        sensor = getattr(self.owner.hardware, "sensor", None)
        if sensor is None:
            self.sensor_test.setText("Sensor input unavailable")
            self.sensor_test.setStyleSheet(
                "font-size: 16px; font-weight: 700; padding: 8px; color: #c44949;"
            )
            return

        try:
            raw_high = bool(sensor.value)
        except Exception:
            self.sensor_test.setText("Sensor input could not be read")
            return

        active_high = bool(self.sensor_polarity.currentData())
        detected = raw_high if active_high else not raw_high
        raw_text = "HIGH" if raw_high else "LOW"
        state_text = "DETECTED" if detected else "CLEAR"
        self.sensor_test.setText(
            f"Live sensor: {raw_text} → {state_text} with selected polarity"
        )
        color = "#c17b16" if detected else "#1f8f62"
        self.sensor_test.setStyleSheet(
            f"font-size: 16px; font-weight: 700; padding: 8px; color: {color};"
        )

    def selected_values(self):
        return {
            "sensor_active_high": bool(self.sensor_polarity.currentData()),
            "sensor_bounce_time_sec": self.sensor_bounce.value(),
            "sensor_stuck_active_timeout_sec": self.stuck_timeout.value(),
            "no_detection_timeout_sec": self.no_detection_timeout.value(),
            "sensor_to_stop_delay_sec": self.sensor_to_stop.value(),
            "belt_settle_delay_sec": self.belt_settle.value(),
            "post_capture_delay_sec": self.post_capture.value(),
            "direction_change_dead_time_ms": self.direction_dead_time.value(),
            "camera_capture_timeout_sec": self.camera_timeout.value(),
        }


class KioskStemConveyorGUI(StemConveyorGUI):
    """Existing proven conveyor GUI plus kiosk/developer controls."""

    def __init__(self):
        self.settings_manager = RuntimeSettings()
        self.runtime_settings = self.settings_manager.load()
        self.developer_auth = DeveloperAuth()
        self._allow_close = False
        self._pin_failures = 0
        self._developer_lockout_until = 0.0

        # The existing working gui.py resolves these names from its module at
        # runtime. Apply persisted calibration BEFORE Hardware/Camera are built.
        self._apply_runtime_settings_to_modules(self.runtime_settings)

        super().__init__()

        self._configure_kiosk_controls()
        self._install_developer_shortcut()
        self._refresh_runtime_labels()

    # ========================================================
    # RUNTIME SETTINGS
    # ========================================================

    @staticmethod
    def _apply_runtime_settings_to_modules(values):
        gui_module.SENSOR_TO_STOP_DELAY_SEC = values[
            "sensor_to_stop_delay_sec"
        ]
        gui_module.BELT_SETTLE_DELAY_SEC = values["belt_settle_delay_sec"]
        gui_module.POST_CAPTURE_DELAY_SEC = values["post_capture_delay_sec"]
        gui_module.SENSOR_STUCK_ACTIVE_TIMEOUT_SEC = values[
            "sensor_stuck_active_timeout_sec"
        ]
        gui_module.NO_DETECTION_TIMEOUT_SEC = values[
            "no_detection_timeout_sec"
        ]
        gui_module.DIRECTION_CHANGE_DEAD_TIME_MS = values[
            "direction_change_dead_time_ms"
        ]

        hardware_module.SENSOR_ACTIVE_HIGH = values["sensor_active_high"]
        hardware_module.SENSOR_BOUNCE_TIME_SEC = values[
            "sensor_bounce_time_sec"
        ]

        camera_module.CAMERA_CAPTURE_TIMEOUT_SEC = values[
            "camera_capture_timeout_sec"
        ]

    def _apply_runtime_settings_live(self, values):
        self.runtime_settings = dict(values)
        self._apply_runtime_settings_to_modules(values)

        sensor = getattr(self.hardware, "sensor", None)
        if sensor is not None:
            try:
                sensor.bounce_time = values["sensor_bounce_time_sec"]
            except Exception as error:
                print(
                    "Could not apply sensor debounce live; it will apply on "
                    f"next application start: {error}"
                )

        # Reset edge/fault timers to avoid carrying timing state across a
        # calibration change. The conveyor stays OFF.
        self.sensor_was_active = self.hardware.stem_detected()
        self.sensor_active_since = None
        self.no_detection_since = None
        self.sensor_fault = False
        self.sensor_fault_kind = None
        self.sensor_fault_message = ""

        self._refresh_runtime_labels()
        self.update_status_display()

    def _refresh_runtime_labels(self):
        values = self.runtime_settings
        if hasattr(self, "auto_mode_label"):
            self.auto_mode_label.setText(
                "Auto mode: detect stem → move "
                f"{values['sensor_to_stop_delay_sec']:.2f} s → stop belt → "
                f"settle {values['belt_settle_delay_sec']:.2f} s → photo → "
                "restart. No detection for "
                f"{values['no_detection_timeout_sec']:.0f} s → belt stops."
            )

    # ========================================================
    # KIOSK CONTROLS
    # ========================================================

    def _configure_kiosk_controls(self):
        # Replace the old Exit button. A normal user never lands on the OS.
        try:
            self.btn_exit.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass

        self.btn_exit.setText("SHUT DOWN")
        self.btn_exit.setIcon(make_power_icon(28))
        self.btn_exit.setIconSize(QSize(28, 28))
        self.btn_exit.setToolTip("Safely stop the conveyor and power off the imaging station.")
        self.btn_exit.clicked.connect(self.shutdown_system)

    def _install_developer_shortcut(self):
        self.developer_shortcut = QShortcut(
            QKeySequence(config.DEVELOPER_SHORTCUT),
            self,
        )
        self.developer_shortcut.setContext(Qt.ApplicationShortcut)
        self.developer_shortcut.activated.connect(self._request_developer_access)

    # ========================================================
    # SHUTDOWN
    # ========================================================

    def shutdown_system(self):
        reply = QMessageBox.question(
            self,
            "Shut Down Imaging Station?",
            "Shut down the entire imaging station?\n\n"
            "The conveyor will be stopped first. Wait until the screen goes "
            "dark before removing power.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        self.emergency_stop()
        self._say("Shutting down... Conveyor is OFF.")
        QApplication.processEvents()

        try:
            result = subprocess.run(
                list(config.POWER_OFF_COMMAND),
                capture_output=True,
                text=True,
                timeout=config.POWER_OFF_COMMAND_TIMEOUT_SEC,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            QMessageBox.critical(
                self,
                "Shutdown Failed",
                "The conveyor is OFF, but the shutdown command "
                f"failed.\n\n{error}",
            )
            return

        if result.returncode != 0:
            details = (result.stderr or result.stdout or "Permission denied.").strip()
            QMessageBox.critical(
                self,
                "Shutdown Failed",
                "The conveyor is OFF, but the system could not be "
                "powered off. Check the kiosk sudoers installation.\n\n"
                f"{details}",
            )
            return

        self._allow_close = True
        QApplication.instance().quit()

    # ========================================================
    # DEVELOPER AUTHENTICATION
    # ========================================================

    def _request_developer_access(self):
        remaining = self._developer_lockout_until - time.monotonic()
        if remaining > 0:
            QMessageBox.warning(
                self,
                "Developer Access Locked",
                f"Too many incorrect PIN attempts. Try again in {remaining:.0f} seconds.",
            )
            return

        # Entering developer authentication always forces the machine into a
        # safe stopped state first. Even a cancelled/incorrect login leaves
        # the conveyor OFF rather than continuing unattended behind a dialog.
        self.emergency_stop()
        self._say("Developer authentication. Conveyor is OFF.")
        self.update_status_display()

        if not self.developer_auth.has_pin():
            if not self._create_first_developer_pin():
                return

        pin, accepted = QInputDialog.getText(
            self,
            "Developer Access",
            "Developer PIN:",
            QLineEdit.Password,
        )

        if not accepted:
            return

        if not self.developer_auth.verify(pin):
            self._pin_failures += 1

            if self._pin_failures >= config.DEVELOPER_MAX_PIN_ATTEMPTS:
                self._pin_failures = 0
                self._developer_lockout_until = (
                    time.monotonic() + config.DEVELOPER_LOCKOUT_SEC
                )
                QMessageBox.warning(
                    self,
                    "Developer Access Locked",
                    f"Incorrect PIN. Developer access is locked for "
                    f"{config.DEVELOPER_LOCKOUT_SEC} seconds.",
                )
            else:
                attempts_left = (
                    config.DEVELOPER_MAX_PIN_ATTEMPTS - self._pin_failures
                )
                QMessageBox.warning(
                    self,
                    "Incorrect PIN",
                    f"Developer PIN is incorrect. {attempts_left} attempt(s) left.",
                )
            return

        self._pin_failures = 0
        self._developer_lockout_until = 0.0

        self._say("Developer mode. Conveyor is OFF.")
        self.update_status_display()
        self._show_developer_menu()

    def _create_first_developer_pin(self):
        QMessageBox.information(
            self,
            "Create Developer PIN",
            "No developer PIN exists yet. Create a 4–12 digit PIN now. "
            "The PIN itself will not be stored; only a salted hash is saved.",
        )

        first, accepted = QInputDialog.getText(
            self,
            "Create Developer PIN",
            "New 4–12 digit PIN:",
            QLineEdit.Password,
        )
        if not accepted:
            return False

        if not self.developer_auth.valid_pin_format(first):
            QMessageBox.warning(
                self,
                "Invalid PIN",
                "Use 4 to 12 digits only.",
            )
            return False

        second, accepted = QInputDialog.getText(
            self,
            "Confirm Developer PIN",
            "Enter the PIN again:",
            QLineEdit.Password,
        )
        if not accepted:
            return False

        if first != second:
            QMessageBox.warning(
                self,
                "PINs Do Not Match",
                "The two PIN entries did not match.",
            )
            return False

        try:
            self.developer_auth.set_pin(first)
        except (OSError, ValueError) as error:
            QMessageBox.critical(
                self,
                "PIN Setup Failed",
                str(error),
            )
            return False

        QMessageBox.information(
            self,
            "Developer PIN Created",
            "Developer PIN created successfully.",
        )
        return True

    # ========================================================
    # DEVELOPER MENU / CONFIGURATION
    # ========================================================

    def _show_developer_menu(self):
        menu = QMessageBox(self)
        menu.setWindowTitle("Developer Menu")
        menu.setText("Developer access granted. Conveyor is OFF.")
        menu.setInformativeText("Choose an action.")

        config_button = menu.addButton(
            "Configuration",
            QMessageBox.ActionRole,
        )
        os_button = menu.addButton(
            "Exit to Desktop",
            QMessageBox.DestructiveRole,
        )
        menu.addButton("Cancel", QMessageBox.RejectRole)

        menu.exec()
        clicked = menu.clickedButton()

        if clicked is config_button:
            self.open_developer_configuration()
        elif clicked is os_button:
            self.exit_to_desktop()

    def open_developer_configuration(self):
        self.emergency_stop()
        self.system_running = False
        self.conveyor_running = False

        dialog = ConfigurationDialog(
            self,
            self.settings_manager,
            self.runtime_settings,
        )

        if dialog.exec() != QDialog.Accepted:
            self._say("Developer configuration closed. Belt remains stopped.")
            return

        try:
            saved = self.settings_manager.save(dialog.selected_values())
        except (OSError, ValueError) as error:
            QMessageBox.critical(
                self,
                "Settings Could Not Be Saved",
                str(error),
            )
            return

        self._apply_runtime_settings_live(saved)
        self._say(
            "Configuration saved. Belt remains stopped. Verify the live sensor "
            "state, then press START BELT when ready."
        )

        QMessageBox.information(
            self,
            "Configuration Saved",
            "Settings were saved to ~/stem_conveyor/settings.json and applied.\n\n"
            "The conveyor remains OFF and will not restart automatically.",
        )

    def exit_to_desktop(self):
        reply = QMessageBox.question(
            self,
            "Exit to Desktop?",
            "Exit the imaging application and show the desktop?\n\n"
            "The conveyor will remain OFF. The application will start again "
            "automatically on the next system startup/login.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        self.emergency_stop()
        self._allow_close = True
        QApplication.instance().quit()

    # ========================================================
    # CLOSE PROTECTION
    # ========================================================

    def allow_external_close(self):
        """Used by SIGTERM/systemd so normal OS shutdown is never blocked."""
        self._allow_close = True

    def closeEvent(self, event):
        # Blocks Alt+F4 and ordinary window-close requests. Only an authorized
        # developer exit, shutdown action, or system signal sets _allow_close.
        if not self._allow_close:
            event.ignore()
            self._say(
                "Use SHUT DOWN to power off. Developer access is available "
                "through the protected shortcut."
            )
            return

        super().closeEvent(event)
