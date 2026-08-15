# Stem Conveyor Imaging System

This Raspberry Pi application controls a conveyor for automatic plant-stem imaging.
It uses a proximity sensor to position one stem at a time, stops the conveyor before
photography, captures the image with a persistent isolated Arducam/Picamera2 worker,
and then restarts the belt.

This revision adds a **latched proximity-sensor stuck-ACTIVE watchdog**.

## Tested hardware/config values

The supplied `config.py` preserves the values from the configuration that was tested
with the previous working version:

```python
PROXIMITY_PIN = 17
FORWARD_RELAY_PIN = 22
REVERSE_RELAY_PIN = 27
RELAY_ACTIVE_HIGH = False

SENSOR_ACTIVE_HIGH = True
SENSOR_PULL_UP = False
SENSOR_BOUNCE_TIME_SEC = 0.05
SENSOR_STUCK_ACTIVE_TIMEOUT_SEC = 5.0

SENSOR_TO_STOP_DELAY_SEC = 1.7
BELT_SETTLE_DELAY_SEC = 0.2
POST_CAPTURE_DELAY_SEC = 0.1

CAMERA_TYPE = "picamera2"
PICAMERA_STILL_SIZE = (4624, 3472)
CAMERA_CAPTURE_TIMEOUT_SEC = 15.0
```

Do not change the GPIO pin numbers merely because older project versions used
different pins.

## Active-low relay behavior

The current direction relays remain active-low:

```text
GPIO LOW  -> relay energized -> selected conveyor direction runs
GPIO HIGH -> relay de-energized -> direction output OFF
```

Both forward and reverse outputs are commanded OFF before another direction is
selected. Proper electrical/mechanical interlocking and an independent physical
E-stop are still required; software is not a personnel-safety device.

## Normal automatic workflow

```text
START BELT
    ↓
Sensor detects one stem
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
```

The workflow assumes **one stem at a time between the proximity sensor and camera**.

## Proximity-sensor watchdog

The GUI polls the sensor continuously. While automatic operation is active, if the
sensor remains continuously ACTIVE for longer than:

```python
SENSOR_STUCK_ACTIVE_TIMEOUT_SEC = 5.0
```

the program treats that as a fault.

```text
Sensor remains ACTIVE too long
        ↓
SENSOR FAULT latched
        ↓
Cancel positioning/photo/restart sequence
        ↓
FORWARD and REVERSE outputs OFF
        ↓
Belt remains stopped
        ↓
START BELT disabled
        ↓
Check sensor / remove obstruction
        ↓
Press RESET SYSTEM
        ↓
Fault clears only if sensor input is CLEAR
```

The SYSTEM card shows `SENSOR FAULT` and the SENSOR card shows `FAULT` while the
fault is latched.

### Important limitation: stuck-CLEAR faults

A single ordinary digital sensor cannot reliably distinguish these two conditions:

```text
no stem has reached the sensor
```

and

```text
sensor/wire failed in a way that leaves the input permanently CLEAR
```

Therefore this software watchdog reliably detects **stuck-ACTIVE** behavior, but it
cannot prove that a continuously CLEAR sensor is healthy. Detecting open-wire or
stuck-CLEAR faults requires diagnosable sensor/interface hardware, plausibility
checking with another sensor, or other independent feedback.

## START BELT sensor check

`START BELT` now requires the sensor input to be CLEAR. If the sensor is already
ACTIVE, the belt is not started. This prevents an already-stuck sensor or a stem
left in front of the sensor from being silently accepted at startup.

## RESET SYSTEM

RESET SYSTEM is fail-closed. It:

1. stops the conveyor immediately;
2. cancels direction/capture timers;
3. clears the current imaging cycle;
4. restarts the isolated camera worker;
5. keeps the belt stopped;
6. checks the proximity sensor;
7. clears a sensor-fault latch only if the sensor is physically CLEAR.

If the sensor is still ACTIVE after reset, the camera may recover but the conveyor
remains locked OFF. Remove any obstruction/check the sensor and press RESET SYSTEM
again.

RESET SYSTEM never automatically starts the conveyor.

## Persistent camera worker

`camera_worker.py` initializes Picamera2 once when the application starts and keeps
it ready. Normal captures do not restart libcamera for every image, which avoids the
multi-second pause seen in the earlier version.

`camera.py` communicates with the worker through `QProcess`. The GUI main event loop
is therefore not blocked by `Picamera2.capture_file()`.

If capture exceeds:

```python
CAMERA_CAPTURE_TIMEOUT_SEC = 15.0
```

the camera worker is killed, the belt stays OFF, and the operator is instructed to
use RESET SYSTEM after checking the camera.

Do not unplug/reconnect the CSI ribbon while the Raspberry Pi is powered.

## Application watchdog

`watchdog.py` sends systemd watchdog heartbeats from a `QTimer` in the **main Qt
event loop**. When the supplied service is used:

```ini
WatchdogSec=5s
Restart=on-failure
```

If the GUI event loop freezes and stops heartbeating, systemd can restart the
application. This is a recovery mechanism, not an emergency stop.

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

The systemd service also contains optional `pinctrl` OFF commands for **both** GPIO22
and GPIO27 before application start and after service stop/failure. Test those
commands manually before relying on them.

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

`storage.py` is unchanged by the sensor-watchdog amendment.

## Run manually

```bash
cd ~/Conv_controller
python3 main.py
```

For a first test after this amendment, keep motor/high-power operation disabled if
possible and verify:

1. sensor CLEAR permits START;
2. sensor detection starts one normal imaging cycle;
3. a normal stem clears the sensor before 5 seconds and does not create a fault;
4. holding the sensor ACTIVE for more than 5 seconds stops/locks the system;
5. START BELT remains disabled while the fault is latched;
6. RESET SYSTEM with the sensor still ACTIVE does **not** clear the fault;
7. clear the sensor and press RESET SYSTEM again; the system returns READY with the
   belt still stopped;
8. camera timeout/failure still leaves the belt OFF;
9. direction relays never energize together.

## Physical safety

The software watchdogs, camera timeout, systemd watchdog, and Pi hardware watchdog
are fault-recovery layers. The final student-operated machine should still have a
physical emergency stop and properly rated/interlocked motor-control hardware that
can remove motor power independently of Python, Qt, Linux, and the Raspberry Pi.
