import sys
import time
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QMessageBox, QListWidget, QListWidgetItem
)

# =========================
# USER CONFIGURATION
# =========================

IMAGE_DIR = Path.home() / "stem_conveyor" / "images"

# GPIO pins using BCM numbering
PROXIMITY_PIN = 17
RELAY_PIN = 27

# Change depending on your relay module
# If relay turns ON when GPIO is HIGH, use True.
# If relay turns ON when GPIO is LOW, use False.
RELAY_ACTIVE_HIGH = True

# Change depending on your proximity sensor interface
# If sensor output is HIGH when stem is detected, use True.
# If sensor output is LOW when stem is detected, use False.
SENSOR_ACTIVE_HIGH = True

# Delay after stopping conveyor before taking picture
CAPTURE_DELAY_SEC = 0.5

# Camera type:
# "usb" for USB webcam
# "picamera2" for Raspberry Pi CSI camera
CAMERA_TYPE = "usb"

USB_CAMERA_INDEX = 0


# =========================
# HARDWARE WRAPPERS
# =========================

class Hardware:
    def __init__(self):
        self.gpio_available = False
        self.relay = None
        self.sensor = None

        try:
            from gpiozero import OutputDevice, DigitalInputDevice

            self.relay = OutputDevice(
                RELAY_PIN,
                active_high=RELAY_ACTIVE_HIGH,
                initial_value=False
            )

            self.sensor = DigitalInputDevice(
                PROXIMITY_PIN,
                pull_up=False
            )

            self.gpio_available = True
            print("GPIO initialized.")

        except Exception as e:
            print("GPIO not available. Running in simulation mode.")
            print(e)

    def conveyor_on(self):
        if self.gpio_available:
            self.relay.on()
        print("Conveyor ON")

    def conveyor_off(self):
        if self.gpio_available:
            self.relay.off()
        print("Conveyor OFF")

    def stem_detected(self):
        if not self.gpio_available:
            return False

        value = self.sensor.value
        return bool(value) if SENSOR_ACTIVE_HIGH else not bool(value)


class Camera:
    def __init__(self):
        self.picam2 = None

        if CAMERA_TYPE == "picamera2":
            try:
                from picamera2 import Picamera2
                self.picam2 = Picamera2()
                config = self.picam2.create_still_configuration()
                self.picam2.configure(config)
                self.picam2.start()
                time.sleep(1)
                print("Picamera2 initialized.")
            except Exception as e:
                print("Picamera2 initialization failed.")
                print(e)

    def capture(self, save_path: Path):
        save_path.parent.mkdir(parents=True, exist_ok=True)

        if CAMERA_TYPE == "picamera2" and self.picam2 is not None:
            self.picam2.capture_file(str(save_path))
            return True

        # USB camera fallback
        try:
            import cv2
            cap = cv2.VideoCapture(USB_CAMERA_INDEX)

            if not cap.isOpened():
                print("USB camera could not be opened.")
                return False

            time.sleep(0.2)
            ret, frame = cap.read()
            cap.release()

            if not ret:
                print("Failed to capture image from USB camera.")
                return False

            cv2.imwrite(str(save_path), frame)
            return True

        except Exception as e:
            print("Camera capture failed.")
            print(e)
            return False


# =========================
# MAIN GUI
# =========================

class StemConveyorGUI(QWidget):
    def __init__(self):
        super().__init__()

        IMAGE_DIR.mkdir(parents=True, exist_ok=True)

        self.hardware = Hardware()
        self.camera = Camera()

        self.system_running = False
        self.conveyor_running = False
        self.capture_in_progress = False

        self.image_files = []
        self.current_index = -1

        self.setWindowTitle("Stem Conveyor Imaging System")
        self.resize(1000, 650)

        self.build_ui()
        self.refresh_image_list()

        self.sensor_timer = QTimer()
        self.sensor_timer.timeout.connect(self.check_sensor)
        self.sensor_timer.start(100)

        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status_display)
        self.status_timer.start(500)

    def build_ui(self):
        main_layout = QVBoxLayout()

        title = QLabel("Stem Conveyor Imaging System")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 28px; font-weight: bold; padding: 10px;")
        main_layout.addWidget(title)

        # Status row
        status_layout = QGridLayout()

        self.system_status = QLabel("System: STOPPED")
        self.conveyor_status = QLabel("Conveyor: OFF")
        self.sensor_status = QLabel("Sensor: CLEAR")
        self.usb_status = QLabel("USB: NOT DETECTED")
        self.image_status = QLabel("Images: 0")

        for label in [
            self.system_status, self.conveyor_status, self.sensor_status,
            self.usb_status, self.image_status
        ]:
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(
                "font-size: 18px; padding: 8px; border: 1px solid gray; "
                "border-radius: 6px; background-color: #eeeeee;"
            )

        status_layout.addWidget(self.system_status, 0, 0)
        status_layout.addWidget(self.conveyor_status, 0, 1)
        status_layout.addWidget(self.sensor_status, 0, 2)
        status_layout.addWidget(self.usb_status, 0, 3)
        status_layout.addWidget(self.image_status, 0, 4)

        main_layout.addLayout(status_layout)

        # Middle section: image preview + file list
        middle_layout = QHBoxLayout()

        self.image_preview = QLabel("No image selected")
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.image_preview.setMinimumSize(620, 380)
        self.image_preview.setStyleSheet(
            "border: 2px solid black; background-color: #222222; "
            "color: white; font-size: 22px;"
        )

        self.image_list = QListWidget()
        self.image_list.setMinimumWidth(300)
        self.image_list.itemClicked.connect(self.select_image_from_list)

        middle_layout.addWidget(self.image_preview, stretch=3)
        middle_layout.addWidget(self.image_list, stretch=1)

        main_layout.addLayout(middle_layout)

        # Control buttons
        button_layout_1 = QHBoxLayout()

        self.btn_start = QPushButton("START SYSTEM")
        self.btn_stop = QPushButton("STOP SYSTEM")
        self.btn_resume = QPushButton("RESUME CONVEYOR")
        self.btn_manual_capture = QPushButton("MANUAL CAPTURE")

        self.btn_start.clicked.connect(self.start_system)
        self.btn_stop.clicked.connect(self.stop_system)
        self.btn_resume.clicked.connect(self.resume_conveyor)
        self.btn_manual_capture.clicked.connect(self.manual_capture)

        for btn in [
            self.btn_start, self.btn_stop, self.btn_resume, self.btn_manual_capture
        ]:
            btn.setMinimumHeight(60)
            btn.setStyleSheet("font-size: 18px; font-weight: bold;")

        button_layout_1.addWidget(self.btn_start)
        button_layout_1.addWidget(self.btn_stop)
        button_layout_1.addWidget(self.btn_resume)
        button_layout_1.addWidget(self.btn_manual_capture)

        main_layout.addLayout(button_layout_1)

        # Image action buttons
        button_layout_2 = QHBoxLayout()

        self.btn_prev = QPushButton("PREVIOUS")
        self.btn_next = QPushButton("NEXT")
        self.btn_copy_current = QPushButton("COPY CURRENT TO USB")
        self.btn_copy_all = QPushButton("COPY ALL TO USB")
        self.btn_delete = QPushButton("DELETE IMAGE")
        self.btn_exit = QPushButton("EXIT")

        self.btn_prev.clicked.connect(self.previous_image)
        self.btn_next.clicked.connect(self.next_image)
        self.btn_copy_current.clicked.connect(self.copy_current_to_usb)
        self.btn_copy_all.clicked.connect(self.copy_all_to_usb)
        self.btn_delete.clicked.connect(self.delete_current_image)
        self.btn_exit.clicked.connect(self.close)

        for btn in [
            self.btn_prev, self.btn_next, self.btn_copy_current,
            self.btn_copy_all, self.btn_delete, self.btn_exit
        ]:
            btn.setMinimumHeight(55)
            btn.setStyleSheet("font-size: 15px;")

        button_layout_2.addWidget(self.btn_prev)
        button_layout_2.addWidget(self.btn_next)
        button_layout_2.addWidget(self.btn_copy_current)
        button_layout_2.addWidget(self.btn_copy_all)
        button_layout_2.addWidget(self.btn_delete)
        button_layout_2.addWidget(self.btn_exit)

        main_layout.addLayout(button_layout_2)

        self.message_label = QLabel("Ready.")
        self.message_label.setAlignment(Qt.AlignCenter)
        self.message_label.setStyleSheet("font-size: 18px; padding: 8px;")
        main_layout.addWidget(self.message_label)

        self.setLayout(main_layout)

    # =========================
    # SYSTEM CONTROL
    # =========================

    def start_system(self):
        self.system_running = True
        self.conveyor_running = True
        self.hardware.conveyor_on()
        self.message_label.setText("System started. Conveyor running.")

    def stop_system(self):
        self.system_running = False
        self.conveyor_running = False
        self.hardware.conveyor_off()
        self.message_label.setText("System stopped. Conveyor off.")

    def resume_conveyor(self):
        if not self.system_running:
            self.message_label.setText("Start system first.")
            return

        self.conveyor_running = True
        self.hardware.conveyor_on()
        self.message_label.setText("Conveyor resumed.")

    def check_sensor(self):
        if not self.system_running:
            return

        if self.capture_in_progress:
            return

        if self.hardware.stem_detected():
            self.capture_sequence()

    def capture_sequence(self):
        self.capture_in_progress = True

        self.conveyor_running = False
        self.hardware.conveyor_off()
        self.message_label.setText("Stem detected. Conveyor stopped. Capturing image...")

        QApplication.processEvents()
        time.sleep(CAPTURE_DELAY_SEC)

        self.capture_image()

        # Safer behavior: keep conveyor stopped until user presses RESUME.
        self.message_label.setText("Image captured. Remove stem and press RESUME CONVEYOR.")
        self.capture_in_progress = False

    def manual_capture(self):
        self.message_label.setText("Manual capture started...")
        QApplication.processEvents()
        self.capture_image()

    # =========================
    # IMAGE HANDLING
    # =========================

    def capture_image(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = IMAGE_DIR / f"stem_{timestamp}.jpg"

        success = self.camera.capture(save_path)

        if success:
            self.refresh_image_list()
            self.current_index = self.image_files.index(save_path)
            self.show_current_image()
            self.message_label.setText(f"Saved: {save_path.name}")
        else:
            self.message_label.setText("Camera capture failed.")
            QMessageBox.warning(self, "Camera Error", "Failed to capture image.")

    def refresh_image_list(self):
        self.image_files = sorted(IMAGE_DIR.glob("*.jpg"))

        self.image_list.clear()
        for img in self.image_files:
            item = QListWidgetItem(img.name)
            self.image_list.addItem(item)

        if self.image_files and self.current_index < 0:
            self.current_index = len(self.image_files) - 1
            self.show_current_image()

        self.update_status_display()

    def show_current_image(self):
        if not self.image_files or self.current_index < 0:
            self.image_preview.setText("No image selected")
            return

        image_path = self.image_files[self.current_index]

        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            self.image_preview.setText("Could not load image")
            return

        scaled = pixmap.scaled(
            self.image_preview.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        self.image_preview.setPixmap(scaled)
        self.image_list.setCurrentRow(self.current_index)

    def select_image_from_list(self, item):
        row = self.image_list.row(item)
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

        self.current_index = min(len(self.image_files) - 1, self.current_index + 1)
        self.show_current_image()

    def delete_current_image(self):
        if not self.image_files or self.current_index < 0:
            return

        image_path = self.image_files[self.current_index]

        reply = QMessageBox.question(
            self,
            "Delete Image",
            f"Delete {image_path.name}?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            image_path.unlink(missing_ok=True)
            self.current_index = -1
            self.refresh_image_list()
            self.message_label.setText("Image deleted.")

    # =========================
    # USB COPY
    # =========================

    def find_usb_mount(self):
        possible_roots = [
            Path("/media") / "pi",
            Path("/media") / "raspberrypi",
            Path("/mnt")
        ]

        for root in possible_roots:
            if root.exists():
                mounts = [p for p in root.iterdir() if p.is_dir()]
                if mounts:
                    return mounts[0]

        return None

    def copy_current_to_usb(self):
        if not self.image_files or self.current_index < 0:
            QMessageBox.information(self, "No Image", "No image selected.")
            return

        usb_path = self.find_usb_mount()
        if usb_path is None:
            QMessageBox.warning(self, "USB Not Found", "Please insert a USB drive.")
            return

        src = self.image_files[self.current_index]
        dst_dir = usb_path / "stem_images"
        dst_dir.mkdir(parents=True, exist_ok=True)

        shutil.copy2(src, dst_dir / src.name)
        self.message_label.setText(f"Copied {src.name} to USB.")

    def copy_all_to_usb(self):
        if not self.image_files:
            QMessageBox.information(self, "No Images", "No images to copy.")
            return

        usb_path = self.find_usb_mount()
        if usb_path is None:
            QMessageBox.warning(self, "USB Not Found", "Please insert a USB drive.")
            return

        dst_dir = usb_path / "stem_images"
        dst_dir.mkdir(parents=True, exist_ok=True)

        for src in self.image_files:
            shutil.copy2(src, dst_dir / src.name)

        self.message_label.setText(f"Copied {len(self.image_files)} images to USB.")

    # =========================
    # STATUS DISPLAY
    # =========================

    def update_status_display(self):
        if self.system_running:
            self.system_status.setText("System: RUNNING")
            self.system_status.setStyleSheet(self.green_style())
        else:
            self.system_status.setText("System: STOPPED")
            self.system_status.setStyleSheet(self.red_style())

        if self.conveyor_running:
            self.conveyor_status.setText("Conveyor: ON")
            self.conveyor_status.setStyleSheet(self.green_style())
        else:
            self.conveyor_status.setText("Conveyor: OFF")
            self.conveyor_status.setStyleSheet(self.red_style())

        if self.hardware.stem_detected():
            self.sensor_status.setText("Sensor: STEM DETECTED")
            self.sensor_status.setStyleSheet(self.yellow_style())
        else:
            self.sensor_status.setText("Sensor: CLEAR")
            self.sensor_status.setStyleSheet(self.green_style())

        if self.find_usb_mount() is not None:
            self.usb_status.setText("USB: DETECTED")
            self.usb_status.setStyleSheet(self.green_style())
        else:
            self.usb_status.setText("USB: NOT DETECTED")
            self.usb_status.setStyleSheet(self.gray_style())

        self.image_status.setText(f"Images: {len(self.image_files)}")
        self.image_status.setStyleSheet(self.gray_style())

    def green_style(self):
        return "font-size: 18px; padding: 8px; border-radius: 6px; background-color: #8fd19e;"

    def red_style(self):
        return "font-size: 18px; padding: 8px; border-radius: 6px; background-color: #f5a3a3;"

    def yellow_style(self):
        return "font-size: 18px; padding: 8px; border-radius: 6px; background-color: #ffe08a;"

    def gray_style(self):
        return "font-size: 18px; padding: 8px; border-radius: 6px; background-color: #eeeeee;"

    def resizeEvent(self, event):
        self.show_current_image()
        super().resizeEvent(event)

    def closeEvent(self, event):
        self.hardware.conveyor_off()
        event.accept()


# =========================
# RUN APP
# =========================

if __name__ == "__main__":
    app = QApplication(sys.argv)

    gui = StemConveyorGUI()

    # for touchscreen final deployment:
    # gui.showFullScreen()

    # while testing:
    gui.show()

    sys.exit(app.exec())
