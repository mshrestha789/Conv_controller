import hashlib
import json
import os
import re
import shutil
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import (
    ACTIVE_BATCH_STATE_FILE,
    BARCODE_MAX_LENGTH,
    IMAGE_DIR,
    MIN_FREE_STORAGE_BYTES,
    USB_IMAGE_FOLDER,
    USB_MOUNT_ROOTS,
)


class StorageError(RuntimeError):
    """Raised when a batch or image cannot be stored safely."""


def utc_now_text():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class StorageManager:
    """Persistent barcode-batch image storage and verified USB export."""

    MANIFEST_NAME = "manifest.json"

    def __init__(
        self,
        image_dir=IMAGE_DIR,
        active_state_file=ACTIVE_BATCH_STATE_FILE,
        usb_mount_roots=USB_MOUNT_ROOTS,
        min_free_bytes=MIN_FREE_STORAGE_BYTES,
    ):
        self.image_dir = Path(image_dir).expanduser()
        self.active_state_file = Path(active_state_file).expanduser()
        self.usb_mount_roots = tuple(
            Path(root).expanduser() for root in usb_mount_roots
        )
        self.min_free_bytes = max(0, int(min_free_bytes))

        self.active_manifest = None
        self.current_manifest = None
        self.current_session_dir = None
        self.recovery_error = ""

        self.ensure_image_directory()
        self._load_active_batch()

    # ========================================================
    # JSON / PATH SAFETY
    # ========================================================

    @staticmethod
    def _atomic_write_json(path, payload):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{uuid.uuid4().hex}.tmp"
        )

        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def validate_barcode(barcode):
        value = str(barcode or "").strip()

        if not value:
            raise StorageError("The scanned barcode is empty.")
        if len(value) > BARCODE_MAX_LENGTH:
            raise StorageError(
                f"The barcode is longer than {BARCODE_MAX_LENGTH} characters."
            )
        if any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        ):
            raise StorageError(
                "The barcode contains unsupported control characters."
            )

        return value

    @classmethod
    def safe_barcode_folder(cls, barcode):
        original = cls.validate_barcode(barcode)
        normalized = unicodedata.normalize("NFKC", original)
        slug = re.sub(
            r"[^A-Za-z0-9._-]+",
            "_",
            normalized,
        ).strip("._-")
        slug = slug[:48] or "barcode"
        digest = hashlib.sha256(
            original.encode("utf-8")
        ).hexdigest()[:10]
        return f"{slug}__{digest}"

    @staticmethod
    def _session_id():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{timestamp}_{uuid.uuid4().hex[:8]}"

    def ensure_image_directory(self):
        self.image_dir.mkdir(parents=True, exist_ok=True)

    def _manifest_path(self):
        if self.current_session_dir is None:
            raise StorageError("No batch session is selected.")
        return self.current_session_dir / self.MANIFEST_NAME

    def _resolve_manifest_path(self, manifest_path):
        manifest_path = Path(manifest_path).expanduser().resolve()
        image_root = self.image_dir.resolve()
        if (
            manifest_path.name != self.MANIFEST_NAME
            or image_root not in manifest_path.parents
        ):
            raise StorageError("The batch manifest path is unsafe.")
        return manifest_path

    def load_session_manifest(self, manifest_path):
        manifest_path = self._resolve_manifest_path(manifest_path)
        try:
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            raise StorageError(
                f"The batch manifest could not be read: {error}"
            ) from error

        if not isinstance(manifest, dict):
            raise StorageError("The batch manifest has an invalid format.")
        if manifest.get("session_id") != manifest_path.parent.name:
            raise StorageError(
                "The manifest session ID does not match its folder."
            )
        if manifest.get("safe_barcode_folder") != manifest_path.parent.parent.name:
            raise StorageError(
                "The manifest barcode folder does not match its location."
            )
        self._validate_session_integrity(manifest_path, manifest)
        return manifest_path, manifest

    @staticmethod
    def _mark_export_stale(manifest):
        if manifest.get("last_verified_export_at"):
            manifest["export_needs_refresh"] = True

    def _save_current_manifest(self):
        if self.current_manifest is None:
            raise StorageError("No batch manifest is loaded.")
        self._atomic_write_json(
            self._manifest_path(),
            self.current_manifest,
        )

    def _write_active_pointer(self):
        if self.active_manifest is None or self.current_session_dir is None:
            raise StorageError("No active batch is available to persist.")

        relative_manifest = self._manifest_path().relative_to(
            self.image_dir
        )
        self._atomic_write_json(
            self.active_state_file,
            {
                "manifest": relative_manifest.as_posix(),
                "updated_at": utc_now_text(),
            },
        )

    def _clear_active_pointer(self):
        try:
            self.active_state_file.unlink(missing_ok=True)
        except OSError as error:
            raise StorageError(
                f"Could not clear the active-batch state: {error}"
            ) from error

    def _load_active_batch(self):
        if not self.active_state_file.exists():
            return

        try:
            pointer = json.loads(
                self.active_state_file.read_text(encoding="utf-8")
            )
            relative = Path(pointer["manifest"])

            if relative.is_absolute() or ".." in relative.parts:
                raise StorageError(
                    "The active-batch pointer contains an unsafe path."
                )

            manifest_path = (self.image_dir / relative).resolve()
            image_root = self.image_dir.resolve()
            if image_root not in manifest_path.parents:
                raise StorageError(
                    "The active-batch manifest is outside image storage."
                )

            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            if manifest.get("status") != "active":
                self._clear_active_pointer()
                return

            self._validate_session_integrity(manifest_path, manifest)

            self.current_session_dir = manifest_path.parent
            self.current_manifest = manifest
            self.active_manifest = manifest

        except Exception as error:
            # Preserve damaged files for developer inspection. Starting without
            # trustworthy batch state would risk assigning images incorrectly.
            self.recovery_error = (
                f"Incomplete batch state could not be loaded: {error}"
            )

    @staticmethod
    def _validate_session_integrity(manifest_path, manifest):
        """Refuse use of a session when disk and manifest disagree."""
        session_dir = manifest_path.parent
        required = {
            "actual_count",
            "barcode",
            "events",
            "expected_count",
            "next_sequence",
            "safe_barcode_folder",
            "session_id",
            "images",
        }
        missing_keys = required.difference(manifest)
        if missing_keys:
            raise StorageError(
                "The manifest is missing required fields: "
                + ", ".join(sorted(missing_keys))
            )

        if manifest["session_id"] != session_dir.name:
            raise StorageError(
                "The manifest session ID does not match its folder."
            )
        if manifest["safe_barcode_folder"] != session_dir.parent.name:
            raise StorageError(
                "The manifest barcode folder does not match its location."
            )

        records = manifest.get("images")
        if not isinstance(records, list):
            raise StorageError("The manifest image list is invalid.")
        if not isinstance(manifest.get("events"), list):
            raise StorageError("The manifest event list is invalid.")

        all_recorded = set()
        saved_recorded = set()
        sequences = set()
        for item in records:
            filename = item.get("filename") if isinstance(item, dict) else None
            sequence = item.get("sequence") if isinstance(item, dict) else None
            if (
                not filename
                or Path(filename).name != filename
                or filename in all_recorded
                or not isinstance(sequence, int)
                or sequence < 1
                or sequence in sequences
            ):
                raise StorageError(
                    "The manifest contains an unsafe or duplicate image record."
                )
            all_recorded.add(filename)
            sequences.add(sequence)
            if item.get("status") == "saved":
                saved_recorded.add(filename)

        next_sequence = int(manifest.get("next_sequence") or 0)
        if next_sequence < 1 or (
            sequences and next_sequence <= max(sequences)
        ):
            raise StorageError("The manifest's next image sequence is invalid.")

        disk_images = {
            path.name
            for path in session_dir.glob("*.jpg")
            if path.is_file()
        }
        unexpected = disk_images.difference(saved_recorded)
        missing = saved_recorded.difference(disk_images)
        if unexpected:
            raise StorageError(
                "Untracked or unfinished image files require inspection: "
                + ", ".join(sorted(unexpected))
            )
        if missing:
            raise StorageError(
                "Images recorded in the manifest are missing: "
                + ", ".join(sorted(missing))
            )

        if int(manifest.get("actual_count") or 0) != len(saved_recorded):
            raise StorageError(
                "The manifest sample count does not match its saved images."
            )

    # ========================================================
    # CAPACITY / BATCH LIFECYCLE
    # ========================================================

    def check_storage_ready(self, require_active=False):
        self.ensure_image_directory()

        if require_active and self.active_manifest is None:
            raise StorageError(
                "Scan a batch barcode before starting the conveyor."
            )
        if require_active:
            self._validate_session_integrity(
                self._manifest_path(),
                self.current_manifest,
            )

        target = self.current_session_dir or self.image_dir
        target.mkdir(parents=True, exist_ok=True)

        try:
            usage = shutil.disk_usage(target)
        except OSError as error:
            raise StorageError(
                f"Storage capacity could not be checked: {error}"
            ) from error

        if usage.free < self.min_free_bytes:
            free_mib = usage.free / (1024 * 1024)
            required_mib = self.min_free_bytes / (1024 * 1024)
            raise StorageError(
                f"Only {free_mib:.0f} MiB is free; at least "
                f"{required_mib:.0f} MiB is required before imaging."
            )

        probe = target / f".write_test_{uuid.uuid4().hex}"
        try:
            probe.write_bytes(b"ok")
        except OSError as error:
            raise StorageError(
                f"Image storage is not writable: {error}"
            ) from error
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass

        return usage.free

    def create_batch(self, barcode, expected_count=0):
        if self.active_manifest is not None:
            raise StorageError(
                "Complete or cancel the current batch first."
            )
        if self.recovery_error:
            raise StorageError(self.recovery_error)

        original = self.validate_barcode(barcode)
        expected = int(expected_count or 0)
        if expected < 0 or expected > 10000:
            raise StorageError(
                "Expected sample count must be between 0 and 10000."
            )

        self.check_storage_ready(require_active=False)

        safe_folder = self.safe_barcode_folder(original)
        session_id = self._session_id()
        session_dir = self.image_dir / safe_folder / session_id
        session_dir.mkdir(parents=True, exist_ok=False)

        manifest = {
            "schema_version": 1,
            "status": "active",
            "barcode": original,
            "safe_barcode_folder": safe_folder,
            "session_id": session_id,
            "created_at": utc_now_text(),
            "updated_at": utc_now_text(),
            "completed_at": None,
            "cancelled_at": None,
            "expected_count": expected,
            "actual_count": 0,
            "next_sequence": 1,
            "last_verified_export_at": None,
            "last_verified_export_destination": None,
            "last_verified_export_count": 0,
            "export_needs_refresh": False,
            "images": [],
            "events": [
                {
                    "type": "batch_created",
                    "at": utc_now_text(),
                }
            ],
        }

        self.current_session_dir = session_dir
        self.current_manifest = manifest
        self.active_manifest = manifest

        try:
            self._save_current_manifest()
            self._write_active_pointer()
        except Exception:
            self.active_manifest = None
            self.current_manifest = None
            self.current_session_dir = None
            raise

        return dict(manifest)

    def update_expected_count(self, expected_count):
        if self.active_manifest is None:
            return

        expected = int(expected_count or 0)
        if expected < 0 or expected > 10000:
            raise StorageError(
                "Expected sample count must be between 0 and 10000."
            )

        self.current_manifest["expected_count"] = expected
        self._mark_export_stale(self.current_manifest)
        self.current_manifest["updated_at"] = utc_now_text()
        self._save_current_manifest()
        self._write_active_pointer()

    def complete_batch(self):
        if self.active_manifest is None:
            raise StorageError("There is no active batch to complete.")

        self._validate_session_integrity(
            self._manifest_path(),
            self.current_manifest,
        )

        now = utc_now_text()
        actual = self.current_photo_count()
        expected = int(
            self.current_manifest.get("expected_count") or 0
        )

        self.current_manifest.update(
            {
                "status": "completed",
                "actual_count": actual,
                "completed_at": now,
                "updated_at": now,
                "count_matches_expected": (
                    expected == 0 or expected == actual
                ),
            }
        )
        self._mark_export_stale(self.current_manifest)
        self.current_manifest["events"].append(
            {
                "type": "batch_completed",
                "at": now,
                "actual_count": actual,
            }
        )
        self._save_current_manifest()
        self._clear_active_pointer()
        self.active_manifest = None
        return dict(self.current_manifest)

    def cancel_batch(self, reason="operator_cancelled"):
        if self.active_manifest is None:
            raise StorageError("There is no active batch to cancel.")

        self._validate_session_integrity(
            self._manifest_path(),
            self.current_manifest,
        )

        now = utc_now_text()
        actual = self.current_photo_count()
        self.current_manifest.update(
            {
                "status": "cancelled",
                "actual_count": actual,
                "cancelled_at": now,
                "updated_at": now,
                "cancellation_reason": str(reason),
            }
        )
        self._mark_export_stale(self.current_manifest)
        self.current_manifest["events"].append(
            {
                "type": "batch_cancelled",
                "at": now,
                "actual_count": actual,
                "reason": str(reason),
            }
        )
        self._save_current_manifest()
        self._clear_active_pointer()
        self.active_manifest = None
        return dict(self.current_manifest)

    # ========================================================
    # IMAGE RECORDS
    # ========================================================

    def next_capture_path(self, source="auto"):
        self.check_storage_ready(require_active=True)
        sequence = int(
            self.current_manifest.get("next_sequence") or 1
        )
        safe_source = re.sub(
            r"[^A-Za-z0-9_-]+",
            "_",
            str(source),
        )[:20] or "capture"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        return self.current_session_dir / (
            f"sample_{sequence:04d}_{safe_source}_{timestamp}.jpg"
        )

    def record_capture(self, image_path, source="auto"):
        if self.active_manifest is None:
            raise StorageError(
                "The image was captured without an active batch."
            )

        image_path = Path(image_path).resolve()
        session_dir = self.current_session_dir.resolve()
        if image_path.parent != session_dir:
            raise StorageError(
                "The captured image is outside the active batch folder."
            )
        if not image_path.exists() or image_path.stat().st_size <= 0:
            raise StorageError(
                "The camera did not create a valid image file."
            )

        if any(
            item.get("filename") == image_path.name
            for item in self.current_manifest["images"]
        ):
            raise StorageError(
                "This image has already been recorded in the manifest."
            )

        now = utc_now_text()
        sequence = int(
            self.current_manifest.get("next_sequence") or 1
        )
        self.current_manifest["images"].append(
            {
                "sequence": sequence,
                "filename": image_path.name,
                "source": str(source),
                "captured_at": now,
                "size_bytes": image_path.stat().st_size,
                "status": "saved",
                "deleted_at": None,
            }
        )
        self.current_manifest["next_sequence"] = sequence + 1
        self.current_manifest["actual_count"] = (
            self.current_photo_count()
        )
        self.current_manifest["updated_at"] = now
        self._mark_export_stale(self.current_manifest)
        self._save_current_manifest()
        self._write_active_pointer()
        return sequence

    def get_images(self):
        if self.current_manifest is None or self.current_session_dir is None:
            return []

        images = []
        for item in self.current_manifest.get("images", []):
            if item.get("status") != "saved":
                continue
            path = self.current_session_dir / item.get("filename", "")
            if path.is_file() and path.stat().st_size > 0:
                images.append(path)

        return images

    def current_photo_count(self):
        return len(self.get_images())

    def delete_image(self, image_path):
        if image_path is None or self.current_manifest is None:
            return False

        image_path = Path(image_path)
        record = next(
            (
                item
                for item in self.current_manifest.get("images", [])
                if item.get("filename") == image_path.name
                and item.get("status") == "saved"
            ),
            None,
        )
        if record is None:
            return False

        try:
            image_path.unlink(missing_ok=True)
            now = utc_now_text()
            record["status"] = "deleted"
            record["deleted_at"] = now
            self.current_manifest["actual_count"] = (
                self.current_photo_count()
            )
            expected = int(
                self.current_manifest.get("expected_count") or 0
            )
            self.current_manifest["count_matches_expected"] = (
                expected == 0
                or expected == self.current_manifest["actual_count"]
            )
            self.current_manifest["updated_at"] = now
            self._mark_export_stale(self.current_manifest)
            self.current_manifest["events"].append(
                {
                    "type": "image_deleted",
                    "at": now,
                    "filename": image_path.name,
                }
            )
            self._save_current_manifest()
            if self.active_manifest is not None:
                self._write_active_pointer()
            return True

        except Exception as error:
            raise StorageError(
                f"The photo deletion could not be committed safely: {error}"
            ) from error

    # ========================================================
    # BATCH HISTORY
    # ========================================================

    def list_sessions(self):
        """Return newest-first batch summaries without changing active state."""
        sessions = []
        for manifest_path in self.image_dir.rglob(self.MANIFEST_NAME):
            try:
                safe_path, manifest = self.load_session_manifest(manifest_path)
                sessions.append(
                    {
                        "manifest_path": safe_path,
                        "barcode": str(manifest.get("barcode") or ""),
                        "session_id": str(manifest.get("session_id") or ""),
                        "status": str(manifest.get("status") or "unknown"),
                        "created_at": str(manifest.get("created_at") or ""),
                        "completed_at": manifest.get("completed_at"),
                        "cancelled_at": manifest.get("cancelled_at"),
                        "expected_count": int(
                            manifest.get("expected_count") or 0
                        ),
                        "actual_count": len(
                            self.get_session_images(safe_path)
                        ),
                        "last_verified_export_at": manifest.get(
                            "last_verified_export_at"
                        ),
                        "export_needs_refresh": bool(
                            manifest.get("export_needs_refresh")
                        ),
                        "error": "",
                    }
                )
            except Exception as error:
                sessions.append(
                    {
                        "manifest_path": Path(manifest_path),
                        "barcode": "UNREADABLE BATCH",
                        "session_id": Path(manifest_path).parent.name,
                        "status": "error",
                        "created_at": "",
                        "completed_at": None,
                        "cancelled_at": None,
                        "expected_count": 0,
                        "actual_count": 0,
                        "last_verified_export_at": None,
                        "export_needs_refresh": False,
                        "error": str(error),
                    }
                )

        sessions.sort(
            key=lambda item: (
                item.get("created_at") or "",
                item.get("session_id") or "",
            ),
            reverse=True,
        )
        return sessions

    def get_session_images(self, manifest_path):
        manifest_path, manifest = self.load_session_manifest(manifest_path)
        session_dir = manifest_path.parent
        images = []
        for item in manifest.get("images", []):
            if not isinstance(item, dict) or item.get("status") != "saved":
                continue
            filename = str(item.get("filename") or "")
            if not filename or Path(filename).name != filename:
                raise StorageError(
                    "The manifest contains an unsafe image filename."
                )
            path = session_dir / filename
            if path.is_file() and path.stat().st_size > 0:
                images.append(path)
        return images

    def delete_historical_image(self, manifest_path, image_path):
        """Permanently remove one finalized image and retain an audit event."""
        manifest_path, manifest = self.load_session_manifest(manifest_path)
        if manifest.get("status") == "active":
            raise StorageError(
                "Active-batch photos cannot be deleted from Batch History."
            )
        if manifest.get("status") not in {"completed", "cancelled"}:
            raise StorageError(
                "Only completed or cancelled batches can be edited here."
            )

        image_path = Path(image_path).resolve()
        if image_path.parent != manifest_path.parent:
            raise StorageError("The selected image is outside this batch.")

        record = next(
            (
                item
                for item in manifest.get("images", [])
                if isinstance(item, dict)
                and item.get("filename") == image_path.name
                and item.get("status") == "saved"
            ),
            None,
        )
        if record is None:
            raise StorageError(
                "The selected saved image is not in this manifest."
            )

        deletion_temp = image_path.with_name(
            f".{image_path.name}.delete_{uuid.uuid4().hex}.tmp"
        )
        try:
            os.replace(image_path, deletion_temp)
        except OSError as error:
            raise StorageError(
                f"The image file could not be prepared for deletion: {error}"
            ) from error

        now = utc_now_text()
        record["status"] = "deleted"
        record["deleted_at"] = now
        record["deletion_source"] = "batch_history"
        remaining = sum(
            1
            for item in manifest.get("images", [])
            if isinstance(item, dict) and item.get("status") == "saved"
        )
        expected = int(manifest.get("expected_count") or 0)
        manifest["actual_count"] = remaining
        manifest["count_matches_expected"] = (
            expected == 0 or expected == remaining
        )
        manifest["updated_at"] = now
        self._mark_export_stale(manifest)
        manifest.setdefault("events", []).append(
            {
                "type": "historical_image_deleted",
                "at": now,
                "filename": image_path.name,
                "remaining_count": remaining,
            }
        )

        try:
            self._atomic_write_json(manifest_path, manifest)
        except Exception as error:
            try:
                os.replace(deletion_temp, image_path)
            except OSError as restore_error:
                raise StorageError(
                    "The manifest update failed and the image could not be "
                    "restored automatically. Developer inspection is "
                    f"required: {restore_error}"
                ) from error
            raise StorageError(
                f"The deletion was cancelled because its audit update failed: {error}"
            ) from error

        try:
            deletion_temp.unlink()
        except OSError as error:
            raise StorageError(
                "The photo is marked deleted, but its temporary file could "
                f"not be removed. Developer cleanup is required: {error}"
            ) from error

        if (
            self.current_session_dir is not None
            and manifest_path.parent.resolve()
            == self.current_session_dir.resolve()
        ):
            self.current_manifest = manifest
        return remaining

    def record_verified_usb_export(
        self,
        manifest_path,
        destination,
        copied_count,
    ):
        manifest_path, manifest = self.load_session_manifest(manifest_path)
        now = utc_now_text()
        manifest["last_verified_export_at"] = now
        manifest["last_verified_export_destination"] = str(destination)
        manifest["last_verified_export_count"] = int(copied_count)
        manifest["export_needs_refresh"] = False
        manifest["updated_at"] = now
        manifest.setdefault("events", []).append(
            {
                "type": "usb_export_verified",
                "at": now,
                "destination": str(destination),
                "copied_count": int(copied_count),
            }
        )
        self._atomic_write_json(manifest_path, manifest)

        destination_manifest = Path(destination) / self.MANIFEST_NAME
        try:
            self._copy_and_verify(manifest_path, destination_manifest)
        except Exception as error:
            failure_time = utc_now_text()
            manifest["export_needs_refresh"] = True
            manifest["updated_at"] = failure_time
            manifest.setdefault("events", []).append(
                {
                    "type": "usb_export_manifest_sync_failed",
                    "at": failure_time,
                    "destination": str(destination),
                    "error": str(error),
                }
            )
            self._atomic_write_json(manifest_path, manifest)
            if (
                self.current_session_dir is not None
                and manifest_path.parent.resolve()
                == self.current_session_dir.resolve()
            ):
                self.current_manifest = manifest
                if manifest.get("status") == "active":
                    self.active_manifest = manifest
            raise

        if (
            self.current_session_dir is not None
            and manifest_path.parent.resolve()
            == self.current_session_dir.resolve()
        ):
            self.current_manifest = manifest
            if manifest.get("status") == "active":
                self.active_manifest = manifest
        return now

    # ========================================================
    # USB
    # ========================================================

    def find_usb_mount(self):
        checked = set()

        for root in self.usb_mount_roots:
            try:
                resolved = root.resolve()
            except OSError:
                resolved = root

            if resolved in checked:
                continue
            checked.add(resolved)

            if not root.exists():
                continue

            try:
                candidates = [
                    item for item in root.iterdir() if item.is_dir()
                ]
                if candidates:
                    return candidates[0]
            except (PermissionError, OSError):
                continue

        return None

    def _usb_session_destination(self, usb_mount):
        if self.current_manifest is None or self.current_session_dir is None:
            raise StorageError(
                "There is no batch session to export."
            )

        return (
            Path(usb_mount)
            / USB_IMAGE_FOLDER
            / self.current_manifest["safe_barcode_folder"]
            / self.current_manifest["session_id"]
        )

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _copy_and_verify(cls, source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if (
            not destination.exists()
            or destination.stat().st_size != source.stat().st_size
            or cls._sha256(destination) != cls._sha256(source)
        ):
            raise StorageError(
                f"USB verification failed for {source.name}."
            )

    def copy_image_to_usb(self, image_path):
        usb_mount = self.find_usb_mount()
        if usb_mount is None:
            return None

        image_path = Path(image_path)
        destination = (
            self._usb_session_destination(usb_mount) / image_path.name
        )
        self._copy_and_verify(image_path, destination)
        return destination

    def copy_current_session_to_usb(self):
        usb_mount = self.find_usb_mount()
        if usb_mount is None:
            return None

        copied, _ = self.export_session_to_usb(
            self._manifest_path(),
            usb_mount,
        )
        return copied

    @classmethod
    def export_session_to_usb(cls, manifest_path, usb_mount):
        """Copy one immutable manifest snapshot for a background worker."""
        manifest_path = Path(manifest_path).resolve()
        session_dir = manifest_path.parent
        try:
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            raise StorageError(
                f"The batch manifest could not be read: {error}"
            ) from error

        cls._validate_session_integrity(manifest_path, manifest)

        safe_folder = str(manifest.get("safe_barcode_folder") or "")
        session_id = str(manifest.get("session_id") or "")
        if (
            not safe_folder
            or Path(safe_folder).name != safe_folder
            or not session_id
            or Path(session_id).name != session_id
        ):
            raise StorageError(
                "The manifest contains an unsafe export folder."
            )

        destination_dir = (
            Path(usb_mount)
            / USB_IMAGE_FOLDER
            / safe_folder
            / session_id
        )
        copied = 0
        deleted_filenames = set()
        for item in manifest.get("images", []):
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename") or "")
            if not filename or Path(filename).name != filename:
                raise StorageError(
                    "The manifest contains an unsafe image filename."
                )
            if item.get("status") == "deleted":
                deleted_filenames.add(filename)
                continue
            if item.get("status") != "saved":
                continue
            image_path = session_dir / filename
            if not image_path.is_file():
                raise StorageError(
                    f"The saved image is missing: {filename}."
                )
            cls._copy_and_verify(
                image_path,
                destination_dir / filename,
            )
            copied += 1

        cls._copy_and_verify(
            manifest_path,
            destination_dir / manifest_path.name,
        )

        # A later verified export must reflect audited local deletions. Remove
        # only filenames explicitly marked deleted by this session manifest;
        # never clean unrelated USB files or broader directories.
        for filename in deleted_filenames:
            stale_destination = destination_dir / filename
            try:
                stale_destination.unlink(missing_ok=True)
            except OSError as error:
                raise StorageError(
                    f"Could not remove deleted USB image {filename}: {error}"
                ) from error
        return copied, destination_dir

    def copy_all_images_to_usb(self, image_files=None):
        """Compatibility wrapper: export selected batch plus manifest."""
        return self.copy_current_session_to_usb()
