# Stem Conveyor Imaging System - Stop/Capture + Fail-Safe + Watchdog Version

This Raspberry Pi application detects a stem with a proximity sensor, moves it
toward the camera, **stops the conveyor before photography**, waits for motion
to settle, captures the image, and restarts the conveyor automatically.

The current relay remains **active-low**.

```text
GPIO LOW  -> relay ON  -> conveyor runs
GPIO HIGH -> relay OFF -> conveyor stops
```

## Automatic workflow

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
Launch isolated camera worker
    ↓
Photo succeeds? ──────────────── No / timeout
    ↓                                  ↓
Short pause                         BELT STAYS OFF
    ↓                                  ↓
Restart belt                    Show camera fault
```

The software assumes **one stem at a time between the sensor and camera**.
Stopping the belt for one stem invalidates purely time-based positioning for a
second closely following stem.

## Important settings

`config.py` starts with:

```python
SENSOR_TO_STOP_DELAY_SEC = 1.7
BELT_SETTLE_DELAY_SEC = 0.2
POST_CAPTURE_DELAY_SEC = 0.1

CAMERA_TYPE = "picamera2"
CAMERA_CAPTURE_TIMEOUT_SEC = 15.0

RELAY_ACTIVE_HIGH = False
APP_WATCHDOG_HEARTBEAT_MS = 1000
```

### Position calibration

The physical sensor-to-camera travel time is approximately 2 seconds. The stop
command starts earlier because the belt can coast after relay/contactor release.

- Stem stops before the camera center: increase `SENSOR_TO_STOP_DELAY_SEC`.
- Stem stops beyond the camera center: decrease it.
- Do not use `BELT_SETTLE_DELAY_SEC` to correct position. It only controls how
  long the system waits for vibration/motion to die down before photography.

## Camera-hang protection

`camera_worker.py` owns Picamera2 for each photograph. `camera.py` launches it
with `QProcess`, so a blocked camera call does **not** run in the Qt GUI thread.

If capture exceeds `CAMERA_CAPTURE_TIMEOUT_SEC`:

1. the camera worker is killed;
2. the conveyor remains OFF;
3. automatic restart is disabled;
4. the GUI reports a camera fault;
5. if the worker cannot be reaped promptly, camera use is locked out for that
   application run and the operator is told to check the cable/reboot.

Do not intentionally unplug/reconnect a CSI ribbon while the Pi is powered.
Test failure handling by booting once without the camera, or by temporarily
making the worker sleep longer than the timeout.

## Application watchdog

`watchdog.py` implements systemd notify/watchdog messages using only the Python
standard library. No `python3-systemd` package is required.

The heartbeat uses a `QTimer` in the **main Qt event loop**. This is important:
if the GUI event loop freezes, the heartbeat also stops.

When the program is run manually with:

```bash
python3 main.py
```

the systemd watchdog is inactive and the program works normally.

When launched through the supplied systemd user service, the service uses:

```ini
WatchdogSec=5s
Restart=on-failure
```

If the main event loop stops heartbeating for about 5 seconds, systemd treats
the service as failed and restarts it.

### Install the user service

The supplied service assumes:

```text
user: conveyer
repo: /home/conveyer/Conv_controller
```

Copy it:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/stem-conveyor.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable stem-conveyor.service
systemctl --user start stem-conveyor.service
```

Check status/logs:

```bash
systemctl --user status stem-conveyor.service
journalctl --user -u stem-conveyor.service -f
```

Stop it normally:

```bash
systemctl --user stop stem-conveyor.service
```

The service includes:

```ini
ExecStartPre=/usr/bin/pinctrl 27 op dh
ExecStopPost=/usr/bin/pinctrl 27 op dh
```

For the active-low relay this requests **GPIO27 HIGH = relay OFF** before the
program starts and after the service stops/fails. Test the command manually
before enabling the service:

```bash
pinctrl 27 op dh
```

Confirm physically that the relay/contactors are OFF. If `pinctrl` is not
available to the user, comment the two service lines until that is corrected.

## Raspberry Pi hardware watchdog

The application watchdog only covers the application. A separate Raspberry Pi
hardware watchdog can recover the whole Pi if the operating system stops
servicing the hardware watchdog.

The supplied file `systemd/config.txt-snippet` contains:

```text
gpio=27=op,dh
kernel_watchdog_timeout=15
```

Add those settings to `/boot/firmware/config.txt` while preserving your existing
Arducam configuration.

The supplied file `systemd/10-hardware-watchdog.conf` contains:

```ini
[Manager]
RuntimeWatchdogSec=15s
```

Install it as a systemd manager drop-in:

```bash
sudo mkdir -p /etc/systemd/system.conf.d
sudo cp systemd/10-hardware-watchdog.conf \
  /etc/systemd/system.conf.d/10-hardware-watchdog.conf
```

Then reboot:

```bash
sudo reboot
```

After reboot, verify the watchdog configuration before deliberately testing a
hang. Do not perform a destructive watchdog test while the conveyor motor power
is connected.

## Active-low relay fail-safe layers

This version intentionally keeps:

```python
RELAY_ACTIVE_HIGH = False
```

The software uses several layers:

1. `Hardware()` creates the relay with `initial_value=False`, which is logical
   OFF and therefore physical HIGH for an active-low relay.
2. `Hardware()` immediately commands all conveyor outputs OFF at startup.
3. Every camera capture occurs only after the conveyor has already been stopped.
4. Camera capture runs in a killable worker process with a hard timeout.
5. Camera failure/time-out is fail-closed: the belt does not restart.
6. STOP, close, SIGINT, SIGTERM, normal Python exit, and uncaught Python errors
   all make a best-effort conveyor-stop request.
7. The systemd service watchdog restarts a frozen GUI.
8. `ExecStartPre`/`ExecStopPost` optionally force GPIO27 HIGH with `pinctrl`.
9. `gpio=27=op,dh` requests HIGH/OFF during Raspberry Pi boot configuration.
10. The Raspberry Pi hardware watchdog can reset the Pi after an OS hang.

## Critical hardware limitation

Software watchdogs are **fault-recovery features, not personnel-safety devices**.
Raspberry Pi documentation notes that boot-time GPIO configuration is not
instantaneous. The active-low relay/control interface should therefore have an
appropriate hardware-defined OFF state (for example, a correctly designed
pull-up/interface appropriate to the exact relay module), and the physical
emergency stop / contactor interlock must operate independently of Python,
Qt, Linux, and the Raspberry Pi.

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

## First safe test

Keep motor/high-power output disconnected first.

1. Run `python3 main.py`.
2. Confirm GPIO/relay initializes OFF.
3. Test proximity detection.
4. Test manual photo.
5. Test automatic stop -> settle -> photo -> restart logic using only low-voltage
   control indicators if possible.
6. Simulate a camera failure and verify the GUI stays responsive and belt output
   remains OFF.
7. Only after those checks, test with the real conveyor under appropriate
   physical safety controls.


## Fast Persistent Camera Worker

The CSI camera is initialized once when the application starts and remains running in an isolated camera worker process. Each photo request is sent to that existing worker, avoiding the several-second Picamera2/libcamera startup cost before every image.

If a capture hangs, the GUI timeout kills the camera worker, keeps the conveyor OFF, and locks camera use until the application/Pi is restarted. This preserves the fail-closed behavior while making normal captures much faster.


## RESET SYSTEM

The GUI includes a **RESET SYSTEM** button for recovery from camera or software
faults without rebooting the Raspberry Pi.

Reset behavior is deliberately fail-closed:

```text
RESET SYSTEM
     ↓
Conveyor OFF immediately
     ↓
Cancel travel / settle / restart timers
     ↓
Clear the current imaging cycle
     ↓
Kill the isolated camera worker if needed
     ↓
Wait briefly for CSI/libcamera resources to release
     ↓
Start a fresh camera worker
     ↓
Camera READY
     ↓
Belt remains STOPPED
     ↓
Operator must press START BELT
```

RESET SYSTEM does **not** delete saved images, reset USB storage, change the
selected direction, reboot Linux, or automatically restart the conveyor.

If the camera cable is disconnected or the camera cannot be initialized, reset
fails safely and the conveyor remains OFF. Check the camera connection and try
RESET SYSTEM again. Power down the Raspberry Pi before physically removing or
reconnecting the CSI ribbon cable.
