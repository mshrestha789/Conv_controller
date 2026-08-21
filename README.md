# Stem Conveyor Imaging System

This Raspberry Pi application controls a conveyor for barcode-organized plant-stem imaging. It uses a USB barcode scanner to open one batch, a proximity sensor to detect one stem at a time, and a persistent isolated Arducam/Picamera2 worker to save every image under the correct barcode session.

The operator always controls conveyor motion: scanning a barcode creates and
locks the batch, but **never starts the belt**. This revision also includes two
sensor-related fault checks:

1. **Stuck-ACTIVE watchdog**: stops the belt if the proximity sensor remains continuously ACTIVE too long.
2. **No-detection watchdog**: stops the belt if automatic operation runs too long without detecting any stem.

Both faults are fail-closed: the conveyor stops and does not restart automatically.

## Barcode batch workflow

The scanner is used in its normal USB HID keyboard mode. Connect its USB cable
to the Raspberry Pi; the application receives the characters and the scanner's
Enter suffix. The scanner's red/black external trigger wires are not used by
this application. Insulate them separately so they cannot short accidentally.

1. With the belt stopped, scan the batch barcode once.
2. Confirm that the BATCH field shows the correct value.
3. Optionally tap **EXPECTED SAMPLES — NOT SET** and enter the total using the
   touch keypad. Presets are provided for 10, 50, and 100. Leave it as
   **NOT SET** when the total is not known in advance.
4. Load samples in the physical groups required by the process (for example,
   seven at a time), then press **START BELT**.
5. Use **STOP / PAUSE** whenever more samples must be loaded. The barcode batch
   and saved count remain active; press **START BELT** to continue.
6. When all samples for that barcode are finished, stop the belt and press
   **COMPLETE BATCH**. Confirm the displayed count.
7. Scan the next barcode and repeat.

When an expected total is set, its button shows live progress such as
`23 / 50`. The button is disabled until a barcode batch exists and while the
belt is moving. Reaching the expected total keeps the belt stopped, but the
operator must still confirm **COMPLETE BATCH**.

The batch locks after the first valid scan. Rapid duplicate scans and all
later scans—whether the same or a different code—are ignored until the operator
completes or cancels the current batch. Barcode input never calls the conveyor
start function. Buttons and the photo list do not take keyboard focus, so the
scanner's Enter suffix cannot accidentally activate the last touched button.

**CANCEL BATCH** is for an abandoned or incorrect batch. It preserves existing
images and marks the manifest `cancelled`; it does not delete evidence. A
completed or cancelled barcode may be reused later because each run receives a
unique session ID.

## Image storage and recovery

Images are stored under `~/stem_conveyor/images` in this structure:

```text
stem_conveyor/images/
└── BARCODE_SAFE_NAME__hash/
    └── YYYYMMDD_HHMMSS_sessionid/
        ├── manifest.json
        ├── sample_0001_auto_timestamp.jpg
        └── sample_0002_auto_timestamp.jpg
```

The manifest retains the original barcode, session ID, expected and actual
counts, image sequence, capture source, timestamps, deletions, and completion
or cancellation state. The safe folder name includes a hash, preventing path
characters or similar-looking sanitized barcodes from colliding.

The active batch pointer is written atomically and survives an application or
Pi restart. On startup, the operator must explicitly choose whether to resume
the incomplete batch, close it as incomplete, or decide later. The belt remains
stopped during this decision. If manifest and image files disagree, the system
locks imaging for developer inspection instead of guessing.

Camera output is first written as a partial file, checked as nonempty, and then
atomically renamed. The application checks that storage is writable and has at
least 1 GiB free before creating a batch or image. A camera or storage error
stops the belt and prevents automatic restart.

**Save Batch to USB** copies the selected session's saved images and manifest,
preserving the barcode/session folders, and verifies each copy by size and
SHA-256 checksum. Full-batch export runs in a separate process so the GUI and
its application watchdog remain responsive. Conveyor motion, barcode changes,
batch editing, and shutdown are locked until export finishes.
Removing the USB before the success message is shown can leave an incomplete
export; the Raspberry Pi copy remains intact.

## Tested hardware/config values

The supplied `config.py` preserves the values from the configuration tested with the previous working version:

```python
PROXIMITY_PIN = 17
FORWARD_RELAY_PIN = 22
REVERSE_RELAY_PIN = 27
RELAY_ACTIVE_HIGH = False

SENSOR_ACTIVE_HIGH = False
SENSOR_PULL_UP = False
SENSOR_BOUNCE_TIME_SEC = 0.05
SENSOR_STUCK_ACTIVE_TIMEOUT_SEC = 5.0
NO_DETECTION_TIMEOUT_SEC = 30.0

SENSOR_TO_STOP_DELAY_SEC = 1.7
BELT_SETTLE_DELAY_SEC = 0.2
POST_CAPTURE_DELAY_SEC = 0.1

CAMERA_TYPE = "picamera2"
PICAMERA_STILL_SIZE = (4624, 3472)
CAMERA_CAPTURE_TIMEOUT_SEC = 15.0
```

Do not change the GPIO pin numbers merely because older project versions used different pins.

## Active-low relay behavior

The direction relays remain active-low:

```text
GPIO LOW  -> relay energized -> selected conveyor direction runs
GPIO HIGH -> relay de-energized -> direction output OFF
```

Both forward and reverse outputs are commanded OFF before another direction is selected. Proper electrical/mechanical interlocking and an independent physical E-stop are still required; software is not a personnel-safety device.

## Automatic imaging inside an active batch

```text
Scan barcode and verify batch
    ↓
Press START BELT
    ↓
Start no-detection timer
    ↓
Sensor detects one stem
    ↓
Cancel no-detection timer
    ↓
Move for SENSOR_TO_STOP_DELAY_SEC
    ↓
CONVEYOR OFF
    ↓
Wait BELT_SETTLE_DELAY_SEC
    ↓
Capture using already-initialized camera worker
    ↓
Photo succeeds? ───────────── No / timeout
    ↓                           ↓
Wait POST_CAPTURE_DELAY_SEC    BELT STAYS OFF
    ↓                           ↓
Restart belt                   RESET SYSTEM required
    ↓
Start a fresh no-detection timer
```

The workflow assumes **one stem at a time between the proximity sensor and camera**.

## Stuck-ACTIVE sensor watchdog

While the automatic system is operating, if the proximity sensor stays continuously ACTIVE longer than:

```python
SENSOR_STUCK_ACTIVE_TIMEOUT_SEC = 5.0
```

the program latches a sensor fault:

```text
Sensor remains ACTIVE too long
        ↓
SENSOR FAULT
        ↓
Cancel positioning/photo/restart sequence
        ↓
Forward and reverse outputs OFF
        ↓
Belt remains stopped
        ↓
START BELT disabled
        ↓
Check/remove obstruction or inspect sensor/wiring
        ↓
Press RESET SYSTEM
        ↓
Fault clears only if sensor input is CLEAR
```

The SYSTEM card shows `SENSOR FAULT` and the SENSOR card shows `FAULT` while this fault is latched.

## No-detection watchdog

When the conveyor is actually moving in a direction where automatic capture is enabled, the application starts a separate timer. If no stem is detected for:

```python
NO_DETECTION_TIMEOUT_SEC = 30.0
```

the belt stops and a `NO DETECTION` fault is latched:

```text
Belt running
    ↓
No stem detection for 30 s
    ↓
NO DETECTION
    ↓
Forward and reverse outputs OFF
    ↓
Belt remains stopped
    ↓
Check stem feed and proximity sensor/wiring
    ↓
Press RESET SYSTEM
    ↓
Belt still remains stopped
    ↓
Press START BELT when ready
```

This does **not prove** that the sensor has failed. The same condition can occur simply because no stem was placed on the conveyor. The timeout is therefore an operational fail-safe that prevents the belt from running indefinitely when no sensor activity is observed.

The timer is paused/reset while the belt is intentionally stopped for a photo, during a direction change, during RESET SYSTEM, and while automatic capture is disabled for the selected direction. A fresh timer starts after each successful photo when the belt restarts.

## START BELT checks

`START BELT` requires:

- an active, confirmed barcode batch;
- no latched sensor/no-detection fault;
- the proximity sensor input to be CLEAR;
- camera worker READY;
- writable storage with the configured free-space reserve;
- the expected sample count not already reached;
- no reset, direction change, or capture cycle already in progress.

If the sensor is already ACTIVE, the belt is not started.

## RESET SYSTEM

RESET SYSTEM is fail-closed. It:

1. stops the conveyor immediately;
2. cancels direction/capture timers;
3. cancels the no-detection timer;
4. clears the current imaging cycle;
5. restarts the isolated camera worker;
6. keeps the belt stopped;
7. checks the proximity sensor;
8. clears the fault latch when the reset completes and the sensor is physically CLEAR.

A stuck-ACTIVE fault remains latched if the sensor is still ACTIVE after reset. A no-detection fault can clear after RESET when the sensor is CLEAR, but the conveyor still does **not** restart automatically. The operator must press `START BELT` again. RESET preserves the active barcode batch and all of its saved images.

## Persistent camera worker

`camera_worker.py` initializes Picamera2 once when the application starts and keeps it ready. Normal captures do not restart libcamera for every image, avoiding the multi-second pause seen in the earlier version.

`camera.py` communicates with the worker through `QProcess`. The GUI main event loop is therefore not blocked by `Picamera2.capture_file()`.

If capture exceeds:

```python
CAMERA_CAPTURE_TIMEOUT_SEC = 15.0
```

the camera worker is terminated, the belt stays OFF, and the operator is instructed to use RESET SYSTEM after checking the camera.

Do not unplug/reconnect the CSI ribbon while the Raspberry Pi is powered.

## 7-inch touchscreen layout

The GUI automatically selects a compact touchscreen layout when the primary
display is 1100 pixels wide or less, or 650 pixels high or less. This includes
the common 1024 × 600 7-inch display.

Compact mode keeps the machine-status cards, latest-photo preview, message
banner, and five conveyor/camera controls on the main page. Saved-photo
navigation, USB copy actions, and deletion are moved to a separate **PHOTOS**
page so touch targets do not have to be reduced to fit the screen. Use
**BACK TO CONTROLS** to return to the operating page.

Larger monitors continue to use the original desktop layout with the preview,
saved-photo list, and all controls visible together.

## Application watchdog

`watchdog.py` sends systemd watchdog heartbeats from a `QTimer` in the **main Qt event loop**. When the supplied service is used:

```ini
WatchdogSec=5s
Restart=on-failure
```

If the GUI event loop freezes and stops heartbeating, systemd can restart the application. This is a recovery mechanism, not an emergency stop.

## Raspberry Pi hardware watchdog

The supplied files include:

```text
systemd/10-hardware-watchdog.conf
systemd/config.txt-snippet
```

The boot snippet uses the tested active-low relay pins:

```text
gpio=22=op,dh
gpio=27=op,dh
kernel_watchdog_timeout=15
```

`dh` requests HIGH, which is the relay-OFF state for this active-low setup.

## Project structure

```text
Conv_controller/
├── main.py
├── config.py
├── gui.py
├── kiosk_gui.py
├── hardware.py
├── camera.py
├── camera_worker.py
├── usb_export_worker.py
├── watchdog.py
├── storage.py
├── runtime_settings.py
├── developer_auth.py
├── install_kiosk.sh
├── KIOSK_SETUP.md
├── README.md
├── tests/
│   └── test_storage.py
└── systemd/
    ├── stem-conveyor.service
    ├── stem-conveyor-autostart.desktop
    ├── start-kiosk.sh
    ├── 10-hardware-watchdog.conf
    └── config.txt-snippet
```

## Install on a new Raspberry Pi

These instructions match the supplied kiosk service and autostart files.

> **Required username:** create the Raspberry Pi OS user as `conveyer`.
> The current service files use the absolute path
> `/home/conveyer/Conv_controller`. A different username will prevent kiosk
> startup unless the paths in `systemd/stem-conveyor.service` and
> `systemd/stem-conveyor-autostart.desktop` are changed before installation.

### 1. Install Raspberry Pi OS

Use Raspberry Pi Imager to install the current **64-bit Raspberry Pi OS with
Desktop**. In the Imager settings:

- set the username to `conveyer`;
- configure Wi-Fi and locale if required;
- optionally enable SSH for maintenance;
- boot to the graphical desktop and complete the initial setup.

The desktop version is required because the kiosk service starts after the
graphical desktop session becomes available.

### 2. Update the OS and install dependencies

Open a terminal and run:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y \
    git \
    python3-gpiozero \
    python3-picamera2 \
    python3-pyside6.qtwidgets \
    raspi-utils \
    rpicam-apps
```

The `python3-pyside6.qtwidgets` package installs the required QtCore and QtGui
dependencies. This project runs with `/usr/bin/python3`, so use OS packages
instead of installing packages into an unrelated Python virtual environment.
If `apt` cannot locate `python3-pyside6.qtwidgets`, the OS image is too old for
this installation procedure. Install the current 64-bit Raspberry Pi OS with
Desktop rather than replacing PySide6 with PyQt, because this source code
imports PySide6 directly.

Verify the Python modules:

```bash
python3 - <<'PY'
from PySide6.QtWidgets import QApplication
from gpiozero import DigitalInputDevice, OutputDevice
from picamera2 import Picamera2

print("Python dependencies are available.")
PY
```

### 3. Connect and verify the camera

Shut down and disconnect power before connecting or moving the CSI ribbon.
For the Arducam 64 MP camera, install the overlay/tuning configuration required
by that specific Arducam model. Do not remove working Arducam overlay lines
from `/boot/firmware/config.txt`.

After reconnecting power, verify camera detection:

```bash
rpicam-hello --list-cameras
rpicam-jpeg -o ~/camera-test.jpg
```

Do not continue to motor testing until the camera is listed and the test image
is created successfully.

### 4. Clone the application

```bash
cd ~
git clone https://github.com/mshrestha789/Conv_controller.git
cd ~/Conv_controller
```

If the directory already exists, do not clone over it. Update it instead:

```bash
cd ~/Conv_controller
git pull
```

### 5. Configure safe GPIO startup and the hardware watchdog

Open the boot configuration:

```bash
sudo nano /boot/firmware/config.txt
```

Keep the existing camera configuration and add these lines once:

```text
gpio=22=op,dh
gpio=27=op,dh
kernel_watchdog_timeout=15
```

For this active-low relay configuration, `dh` requests GPIO HIGH, which is the
relay-OFF state during boot.

Install the systemd hardware-watchdog configuration:

```bash
sudo install -D -m 0644 \
    systemd/10-hardware-watchdog.conf \
    /etc/systemd/system.conf.d/10-hardware-watchdog.conf
```

### 6. Enable desktop autologin and install kiosk startup

Run:

```bash
sudo raspi-config
```

Select **System Options -> Boot / Auto Login -> Desktop Autologin**, then exit
without rebooting yet.

Install the user service, desktop autostart entry, and restricted shutdown
permission:

```bash
cd ~/Conv_controller
chmod +x install_kiosk.sh systemd/start-kiosk.sh
./install_kiosk.sh
```

The installer creates:

```text
~/.config/systemd/user/stem-conveyor.service
~/.config/autostart/stem-conveyor.desktop
/etc/sudoers.d/stem-conveyor-poweroff
```

The service is intentionally started by desktop autostart rather than enabled
directly. Therefore, desktop autologin must work for automatic kiosk startup.

### 7. Verify before connecting motor power

Keep the motor/high-power circuit disabled if possible. Check Python syntax and
start the GUI manually:

```bash
cd ~/Conv_controller
python3 -m py_compile *.py
python3 main.py
```

Verify the following before enabling motor power:

1. the GUI fits the touchscreen;
2. the camera status becomes ready;
3. the sensor CLEAR/DETECTED state matches the physical input;
4. forward and reverse relay outputs are never active together;
5. active-low relay OFF corresponds to GPIO HIGH;
6. holding `STEM IMAGING STATION` for 5 seconds opens the on-screen developer
   PIN keypad after the title is released.

Use the protected developer menu to exit the manually started application, or
press `Ctrl+C` in the launch terminal.

### 8. Reboot and verify automatic startup

```bash
sudo reboot
```

After login, the application should open automatically in full-screen kiosk
mode. If it does not, inspect the user service:

```bash
systemctl --user status stem-conveyor.service --no-pager
journalctl --user -u stem-conveyor.service -b -n 100 --no-pager
```

After pulling later code updates, restart the running application with:

```bash
cd ~/Conv_controller
git pull
systemctl --user restart stem-conveyor.service
```

## Run manually

```bash
cd ~/Conv_controller
python3 main.py
```

## Suggested tests before student use

With motor/high-power operation disabled if possible, verify:

1. sensor CLEAR permits START;
2. a normal stem produces one imaging cycle;
3. a normal detection resets the 30-second no-detection interval;
4. holding the sensor ACTIVE longer than 5 seconds stops and locks the system;
5. after RESET with sensor CLEAR, the system returns READY but the belt stays stopped;
6. start the belt and provide **no stem** for 30 seconds; the system should show `NO DETECTION` and stop the conveyor;
7. `START BELT` should remain unavailable until RESET SYSTEM is completed;
8. after RESET, the belt should still remain stopped until START BELT is pressed;
9. camera timeout/failure still leaves the belt OFF;
10. forward and reverse relays never energize together.

## Physical safety

The software watchdogs, camera timeout, systemd watchdog, and Pi hardware watchdog are fault-recovery layers. The final student-operated machine should still have a physical emergency stop and properly rated/interlocked motor-control hardware that can remove motor power independently of Python, Qt, Linux, and the Raspberry Pi.
