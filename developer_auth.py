import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

import config


class DeveloperAuth:
    """Salted PBKDF2 developer-PIN storage and verification."""

    ALGORITHM = "sha256"
    ITERATIONS = 310_000

    def __init__(self, path=None):
        self.path = Path(path or config.DEVELOPER_AUTH_FILE)

    def has_pin(self):
        return self.path.exists()

    @staticmethod
    def valid_pin_format(pin):
        return pin.isdigit() and 4 <= len(pin) <= 12

    def set_pin(self, pin):
        if not self.valid_pin_format(pin):
            raise ValueError("PIN must contain 4 to 12 digits.")

        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            self.ALGORITHM,
            pin.encode("utf-8"),
            salt,
            self.ITERATIONS,
        )

        payload = {
            "algorithm": self.ALGORITHM,
            "iterations": self.ITERATIONS,
            "salt": salt.hex(),
            "digest": digest.hex(),
        }

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")

        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")

        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def verify(self, pin):
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)

            salt = bytes.fromhex(payload["salt"])
            expected = bytes.fromhex(payload["digest"])
            iterations = int(payload.get("iterations", self.ITERATIONS))
            algorithm = str(payload.get("algorithm", self.ALGORITHM))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return False

        actual = hashlib.pbkdf2_hmac(
            algorithm,
            pin.encode("utf-8"),
            salt,
            iterations,
        )
        return hmac.compare_digest(actual, expected)
