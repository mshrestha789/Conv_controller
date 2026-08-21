import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from storage import StorageError, StorageManager


class StorageManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.images = self.root / "images"
        self.state = self.images / ".active_batch.json"
        self.mount_roots = self.root / "mounts"
        self.mount_roots.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def manager(self):
        return StorageManager(
            image_dir=self.images,
            active_state_file=self.state,
            usb_mount_roots=(self.mount_roots,),
            min_free_bytes=0,
        )

    @staticmethod
    def fake_capture(manager, source="auto"):
        path = manager.next_capture_path(source)
        path.write_bytes(b"not-a-real-jpeg-but-nonempty")
        manager.record_capture(path, source)
        return path

    def test_barcode_folder_is_safe_and_collision_resistant(self):
        first = StorageManager.safe_barcode_folder("lot/a")
        second = StorageManager.safe_barcode_folder("lot?a")

        self.assertNotIn("/", first)
        self.assertNotEqual(first, second)

    def test_active_batch_survives_restart_and_duplicate_batch_is_refused(self):
        manager = self.manager()
        manager.create_batch("SDS1EDEC5R1232", expected_count=2)
        self.fake_capture(manager)

        restarted = self.manager()
        self.assertEqual(
            restarted.active_manifest["barcode"],
            "SDS1EDEC5R1232",
        )
        self.assertEqual(restarted.current_photo_count(), 1)

        with self.assertRaises(StorageError):
            restarted.create_batch("OTHER")

    def test_completion_clears_active_pointer_and_reuse_creates_new_session(self):
        manager = self.manager()
        first = manager.create_batch("REUSED", expected_count=1)
        self.fake_capture(manager)
        completed = manager.complete_batch()

        self.assertEqual(completed["status"], "completed")
        self.assertTrue(completed["count_matches_expected"])
        self.assertFalse(self.state.exists())

        second = manager.create_batch("REUSED", expected_count=0)
        self.assertNotEqual(first["session_id"], second["session_id"])

    def test_restart_locks_batch_when_an_untracked_image_exists(self):
        manager = self.manager()
        manager.create_batch("POWER-LOSS")
        unfinished = manager.current_session_dir / ".sample_0001.partial.jpg"
        unfinished.write_bytes(b"unfinished")

        restarted = self.manager()

        self.assertIsNone(restarted.active_manifest)
        self.assertIn("Untracked or unfinished", restarted.recovery_error)

    def test_restart_locks_batch_when_a_recorded_image_is_missing(self):
        manager = self.manager()
        manager.create_batch("MISSING-FILE")
        image_path = self.fake_capture(manager)
        image_path.unlink()

        restarted = self.manager()

        self.assertIsNone(restarted.active_manifest)
        self.assertIn("recorded in the manifest are missing", restarted.recovery_error)

    def test_delete_updates_manifest_without_reusing_sequence(self):
        manager = self.manager()
        manager.create_batch("DELETE-TEST")
        first_path = self.fake_capture(manager)

        self.assertTrue(manager.delete_image(first_path))
        self.assertEqual(manager.current_photo_count(), 0)
        self.assertEqual(
            manager.current_manifest["images"][0]["status"],
            "deleted",
        )

        second_path = manager.next_capture_path("auto")
        self.assertIn("sample_0002", second_path.name)

    def test_usb_export_preserves_batch_session_and_manifest(self):
        usb_mount = self.mount_roots / "USB1"
        usb_mount.mkdir()

        manager = self.manager()
        manager.create_batch("USB/BATCH")
        image_path = self.fake_capture(manager)
        copied = manager.copy_current_session_to_usb()

        destination = (
            usb_mount
            / "stem_images"
            / manager.current_manifest["safe_barcode_folder"]
            / manager.current_manifest["session_id"]
        )
        self.assertEqual(copied, 1)
        self.assertEqual(
            (destination / image_path.name).stat().st_size,
            image_path.stat().st_size,
        )
        exported_manifest = json.loads(
            (destination / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(exported_manifest["barcode"], "USB/BATCH")

    def test_usb_export_worker_reports_verified_success(self):
        usb_mount = self.mount_roots / "USB2"
        usb_mount.mkdir()
        manager = self.manager()
        manager.create_batch("WORKER")
        self.fake_capture(manager)

        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parents[1] / "usb_export_worker.py"),
                str(manager._manifest_path()),
                str(usb_mount),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        response = json.loads(result.stdout.strip())

        self.assertEqual(result.returncode, 0)
        self.assertTrue(response["success"])
        self.assertEqual(response["copied"], 1)


if __name__ == "__main__":
    unittest.main()
