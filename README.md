# Stem Conveyor Imaging System

This Raspberry Pi application controls a conveyor for automatic plant-stem imaging. It uses a proximity sensor to detect one stem at a time, stops the conveyor before photography, captures the image with a persistent isolated Arducam/Picamera2 worker, and then restarts the belt.

This revision includes two sensor-related fault checks:

1. **Stuck-ACTIVE watchdog**: stops the belt if the proximity sensor remains continuously ACTIVE too long.
2. **No-detection watchdog**: stops the belt if automatic operation runs too long without detecting any stem.

Both faults are fail-closed: the conveyor stops and does not restart automatically.

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

## Normal automatic workflow

```text
START BELT
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

- no latched sensor/no-detection fault;
- the proximity sensor input to be CLEAR;
- camera worker READY;
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

A stuck-ACTIVE fault remains latched if the sensor is still ACTIVE after reset. A no-detection fault can clear after RESET when the sensor is CLEAR, but the conveyor still does **not** restart automatically. The operator must press `START BELT` again.

## Persistent camera worker

`camera_worker.py` initializes Picamera2 once when the application starts and keeps it ready. Normal captures do not restart libcamera for every image, avoiding the multi-second pause seen in the earlier version.

`camera.py` communicates with the worker through `QProcess`. The GUI main event loop is therefore not blocked by `Picamera2.capture_file()`.

If capture exceeds:

```python
CAMERA_CAPTURE_TIMEOUT_SEC = 15.0
```

the camera worker is terminated, the belt stays OFF, and the operator is instructed to use RESET SYSTEM after checking the camera.

Do not unplug/reconnect the CSI ribbon while the Raspberry Pi is powered.

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
├── hardware.py
├── camera.py
├── camera_worker.py
├── watchdog.py
├── storage.py
├── README.md
└── systemd/
    ├── stem-conveyor.service
    ├── 10-hardware-watchdog.conf
    └── config.txt-snippet
```

`storage.py` remains unchanged by this amendment.

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
