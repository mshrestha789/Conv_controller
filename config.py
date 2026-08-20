from pathlib import Path


# ============================================================
# APP
# ============================================================

APP_TITLE = "Stem Imaging Station"
FULLSCREEN = True  # Final Raspberry Pi touchscreen / kiosk use.


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

# Tested ACTIVE-LOW relay behavior:
#   GPIO LOW  -> logical ON  -> relay energized
#   GPIO HIGH -> logical OFF -> relay de-energized
RELAY_ACTIVE_HIGH = False


# ============================================================
# PROXIMITY SENSOR CONFIGURATION
# ============================================================

# Tested configuration: sensor is ACTIVE LOW.
# True  -> sensor HIGH means stem detected
# False -> sensor LOW means stem detected
SENSOR_ACTIVE_HIGH = False
SENSOR_PULL_UP = False
SENSOR_BOUNCE_TIME_SEC = 0.05

# Sensor fault watchdogs.
SENSOR_STUCK_ACTIVE_TIMEOUT_SEC = 5.0
NO_DETECTION_TIMEOUT_SEC = 30.0


# ============================================================
# CONVEYOR DIRECTION
# ============================================================

DEFAULT_DIRECTION = "forward"
DIRECTION_CHANGE_DEAD_TIME_MS = 800


# ============================================================
# AUTOMATIC IMAGE CAPTURE
# ============================================================

SENSOR_TO_STOP_DELAY_SEC = 1.7
BELT_SETTLE_DELAY_SEC = 0.2
POST_CAPTURE_DELAY_SEC = 0.1

AUTO_CAPTURE_FORWARD = True
AUTO_CAPTURE_REVERSE = False

SENSOR_POLL_MS = 100
STATUS_UPDATE_MS = 500


# ============================================================
# CAMERA
# ============================================================

CAMERA_TYPE = "picamera2"
CAMERA_CAPTURE_TIMEOUT_SEC = 15.0
CAMERA_KILL_GRACE_MS = 1000
CAMERA_RESET_RESTART_DELAY_MS = 750
PICAMERA_STILL_SIZE = (4624, 3472)

USB_CAMERA_INDEX = 0
USB_CAMERA_WARMUP_SEC = 0.2


# ============================================================
# APPLICATION WATCHDOG
# ============================================================

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
# DEVELOPER / KIOSK MODE
# ============================================================

# Developer access is available by holding the main title on the touchscreen
# or by using the hidden keyboard shortcut. The PIN is NOT stored here. On
# first use the developer creates a PIN, which is stored as a salted PBKDF2
# hash in DEVELOPER_AUTH_FILE.
DEVELOPER_SHORTCUT = "Ctrl+Alt+Shift+D"
DEVELOPER_TOUCH_HOLD_MS = 5000
DEVELOPER_AUTH_FILE = Path.home() / "stem_conveyor" / "developer_auth.json"
RUNTIME_SETTINGS_FILE = Path.home() / "stem_conveyor" / "settings.json"

# Three failed PIN attempts temporarily lock developer authentication.
DEVELOPER_MAX_PIN_ATTEMPTS = 3
DEVELOPER_LOCKOUT_SEC = 30

# The installer creates a narrow sudoers rule allowing only this command.
POWER_OFF_COMMAND = (
    "/usr/bin/sudo",
    "-n",
    "/usr/bin/systemctl",
    "poweroff",
)
POWER_OFF_COMMAND_TIMEOUT_SEC = 5


# ============================================================
# DEVELOPMENT / SAFETY
# ============================================================

ALLOW_GPIO_SIMULATION = False
