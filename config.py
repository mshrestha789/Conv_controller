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

# Your original project used GPIO 27 for the conveyor relay.
FORWARD_RELAY_PIN = 27

# IMPORTANT:
# Set this to the BCM GPIO pin connected to the REVERSE control
# input of your properly interlocked motor-control hardware.
#
# Example only:
# REVERSE_RELAY_PIN = 22
#
# Leave as None until the actual wiring is confirmed.
REVERSE_RELAY_PIN = None


# ============================================================
# RELAY CONFIGURATION
# ============================================================

# True  -> relay/input turns ON when GPIO is HIGH
# False -> relay/input turns ON when GPIO is LOW
RELAY_ACTIVE_HIGH = False


# ============================================================
# PROXIMITY SENSOR CONFIGURATION
# ============================================================

# True  -> sensor HIGH means stem detected
# False -> sensor LOW means stem detected
SENSOR_ACTIVE_HIGH = True

# Use True only if your sensor interface needs the Pi's pull-up.
SENSOR_PULL_UP = False

# Helps reject rapid electrical chatter/noise.
SENSOR_BOUNCE_TIME_SEC = 0.05


# ============================================================
# CONVEYOR DIRECTION
# ============================================================

DEFAULT_DIRECTION = "forward"

# Motor must be stopped before changing direction.
# This is a software delay only. Proper electrical/mechanical
# interlocking is still required in the motor-control hardware.
DIRECTION_CHANGE_DEAD_TIME_MS = 800


# ============================================================
# AUTOMATIC IMAGE CAPTURE
# ============================================================

# Time for the stem to travel from the proximity sensor
# to the camera position while the conveyor keeps moving.
#
# Tune this experimentally.
SENSOR_TO_CAMERA_DELAY_SEC = 2.0

# Normally the sensor-to-camera geometry is calibrated for
# forward travel. Reverse auto-capture is disabled by default.
AUTO_CAPTURE_FORWARD = True
AUTO_CAPTURE_REVERSE = False

SENSOR_POLL_MS = 100
STATUS_UPDATE_MS = 500


# ============================================================
# CAMERA
# ============================================================

# "usb" or "picamera2"
CAMERA_TYPE = "usb"

USB_CAMERA_INDEX = 0
USB_CAMERA_WARMUP_SEC = 0.2


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
