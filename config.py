from pathlib import Path


# ============================================================
# APP
# ============================================================

APP_TITLE = "Stem Imaging Station"
FULLSCREEN = False  # Set True for final Raspberry Pi touchscreen use.


# ============================================================
# IMAGE STORAGE
# ============================================================

IMAGE_DIR = Path.home() / "stem_conveyor" / "images"


# ============================================================
# GPIO CONFIGURATION
# BCM numbering is used.
# ============================================================

PROXIMITY_PIN = 17
FORWARD_RELAY_PIN = 22
REVERSE_RELAY_PIN = 27


# ============================================================
# RELAY CONFIGURATION
# ============================================================

# Keep the current ACTIVE-LOW relay behavior:
#   GPIO LOW  -> logical ON  -> relay energized
#   GPIO HIGH -> logical OFF -> relay de-energized
RELAY_ACTIVE_HIGH = False


# ============================================================
# PROXIMITY SENSOR CONFIGURATION
# ============================================================

# True  -> sensor HIGH means stem detected
# False -> sensor LOW means stem detected
SENSOR_ACTIVE_HIGH = True
SENSOR_PULL_UP = False
SENSOR_BOUNCE_TIME_SEC = 0.05

# If the sensor stays continuously ACTIVE while the automatic system is
# operating, treat it as a fault. This catches a sensor/output stuck HIGH or
# an object that never clears the sensor. The separate no-detection watchdog
# below provides an operational check for the opposite failure direction.
SENSOR_STUCK_ACTIVE_TIMEOUT_SEC = 5.0

# If the belt is moving in an automatic-capture direction and no stem is
# detected for this long, stop the conveyor and latch a NO DETECTION fault.
# This helps catch a disconnected/stuck-CLEAR sensor, but it can also mean
# that no stem was fed onto the belt. RESET SYSTEM is required before restart.
NO_DETECTION_TIMEOUT_SEC = 30.0


# ============================================================
# CONVEYOR DIRECTION
# ============================================================

DEFAULT_DIRECTION = "forward"

# Software dead time for direction changes. Proper electrical or
# mechanical interlocking is still required in the motor-control circuit.
DIRECTION_CHANGE_DEAD_TIME_MS = 800


# ============================================================
# AUTOMATIC IMAGE CAPTURE
# ============================================================

# The measured sensor-to-camera travel time is about 2 seconds.
# Stop slightly earlier because the conveyor has some coast/inertia.
SENSOR_TO_STOP_DELAY_SEC = 1.7

# Wait after stopping before taking the photo.
BELT_SETTLE_DELAY_SEC = 0.2

# Short pause after a successful photo before restarting the belt.
POST_CAPTURE_DELAY_SEC = 0.1

AUTO_CAPTURE_FORWARD = True
AUTO_CAPTURE_REVERSE = False

SENSOR_POLL_MS = 100
STATUS_UPDATE_MS = 500


# ============================================================
# CAMERA
# ============================================================

# Current Arducam CSI setup.
CAMERA_TYPE = "picamera2"

# The camera is isolated in camera_worker.py. If libcamera/Picamera2
# stops responding, the GUI times it out without freezing.
CAMERA_CAPTURE_TIMEOUT_SEC = 15.0
CAMERA_KILL_GRACE_MS = 1000

# When RESET SYSTEM restarts the isolated camera worker, give libcamera a
# short time to release the CSI device before initializing it again.
CAMERA_RESET_RESTART_DELAY_MS = 750

# Camera is initialized once when the application starts and remains ready.
# There is no per-photo camera warm-up delay.

# Use a practical still resolution instead of the 64 MP maximum/default.
# This is about 16 MP and is much faster for repeated conveyor imaging.
PICAMERA_STILL_SIZE = (4624, 3472)

USB_CAMERA_INDEX = 0
USB_CAMERA_WARMUP_SEC = 0.2


# ============================================================
# APPLICATION WATCHDOG
# ============================================================

# Heartbeat is sent from a QTimer running in the MAIN Qt event loop.
# It becomes active automatically when the application is launched by
# a systemd service with WatchdogSec= configured.
APP_WATCHDOG_HEARTBEAT_MS = 1000


# ============================================================
# USB STORAGE
# ============================================================

USB_MOUNT_ROOTS = (
    Path("/media") / Path.home().name,
    Path("/media/pi"),
    Path("/media/raspberrypi"),
    Path("/mnt"),
)

USB_IMAGE_FOLDER = "stem_images"


# ============================================================
# DEVELOPMENT / SAFETY
# ============================================================

# Keep False on the real conveyor. If GPIO initialization fails,
# conveyor start is refused rather than simulated.
ALLOW_GPIO_SIMULATION = False
