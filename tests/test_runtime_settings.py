import json
import tempfile
import unittest
from pathlib import Path

import config
from runtime_settings import RuntimeSettings


class RuntimeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.temporary.name) / "settings.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_old_settings_file_receives_runout_default(self):
        self.settings_path.write_text(
            json.dumps({"post_capture_delay_sec": 0.25}),
            encoding="utf-8",
        )

        values = RuntimeSettings(self.settings_path).load()

        self.assertEqual(values["post_capture_delay_sec"], 0.25)
        self.assertEqual(
            values["batch_completion_runout_delay_sec"],
            config.BATCH_COMPLETION_RUNOUT_DELAY_SEC,
        )

    def test_runout_setting_is_clamped_and_saved(self):
        manager = RuntimeSettings(self.settings_path)
        values = manager.restore_defaults()
        values["batch_completion_runout_delay_sec"] = 99

        saved = manager.save(values)

        self.assertEqual(
            saved["batch_completion_runout_delay_sec"],
            5.0,
        )
        stored = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(
            stored["batch_completion_runout_delay_sec"],
            5.0,
        )
