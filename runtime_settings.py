import json
import os
from copy import deepcopy
from pathlib import Path

import config


class RuntimeSettings:
    """Persistent, developer-editable calibration settings.

    Hardware wiring and relay polarity deliberately remain in config.py.
    Only operational calibration values are written to settings.json.
    """

    def __init__(self, path=None):
        self.path = Path(path or config.RUNTIME_SETTINGS_FILE)
        self.defaults = {
            "sensor_active_high": bool(config.SENSOR_ACTIVE_HIGH),
            "sensor_bounce_time_sec": float(config.SENSOR_BOUNCE_TIME_SEC),
            "sensor_stuck_active_timeout_sec": float(
                config.SENSOR_STUCK_ACTIVE_TIMEOUT_SEC
            ),
            "no_detection_timeout_sec": float(config.NO_DETECTION_TIMEOUT_SEC),
            "sensor_to_stop_delay_sec": float(config.SENSOR_TO_STOP_DELAY_SEC),
            "belt_settle_delay_sec": float(config.BELT_SETTLE_DELAY_SEC),
            "post_capture_delay_sec": float(config.POST_CAPTURE_DELAY_SEC),
            "direction_change_dead_time_ms": int(
                config.DIRECTION_CHANGE_DEAD_TIME_MS
            ),
            "camera_capture_timeout_sec": float(
                config.CAMERA_CAPTURE_TIMEOUT_SEC
            ),
        }
        self.values = deepcopy(self.defaults)

    def load(self):
        self.values = deepcopy(self.defaults)

        if not self.path.exists():
            return deepcopy(self.values)

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            print(f"Could not load runtime settings; using defaults: {error}")
            return deepcopy(self.values)

        if not isinstance(stored, dict):
            return deepcopy(self.values)

        candidate = deepcopy(self.defaults)
        candidate.update(stored)
        self.values = self.validate(candidate)
        return deepcopy(self.values)

    def validate(self, values):
        def clamp_float(name, low, high):
            value = float(values[name])
            return max(low, min(high, value))

        def clamp_int(name, low, high):
            value = int(values[name])
            return max(low, min(high, value))

        return {
            "sensor_active_high": bool(values["sensor_active_high"]),
            "sensor_bounce_time_sec": clamp_float(
                "sensor_bounce_time_sec", 0.0, 1.0
            ),
            "sensor_stuck_active_timeout_sec": clamp_float(
                "sensor_stuck_active_timeout_sec", 1.0, 120.0
            ),
            "no_detection_timeout_sec": clamp_float(
                "no_detection_timeout_sec", 5.0, 600.0
            ),
            "sensor_to_stop_delay_sec": clamp_float(
                "sensor_to_stop_delay_sec", 0.05, 10.0
            ),
            "belt_settle_delay_sec": clamp_float(
                "belt_settle_delay_sec", 0.0, 5.0
            ),
            "post_capture_delay_sec": clamp_float(
                "post_capture_delay_sec", 0.0, 5.0
            ),
            "direction_change_dead_time_ms": clamp_int(
                "direction_change_dead_time_ms", 100, 5000
            ),
            "camera_capture_timeout_sec": clamp_float(
                "camera_capture_timeout_sec", 2.0, 60.0
            ),
        }

    def save(self, values):
        validated = self.validate(values)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(validated, handle, indent=2, sort_keys=True)
            handle.write("\n")

        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

        self.values = validated
        return deepcopy(validated)

    def restore_defaults(self):
        return deepcopy(self.defaults)
