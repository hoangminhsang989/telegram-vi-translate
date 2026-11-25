import json
import tempfile
import unittest
from pathlib import Path

from scripts.config_telegram import merge_experimental_options, restore_experimental_options


class TestConfigTelegram(unittest.TestCase):
    def test_merge_preserves_existing_keys_and_restore_reverts_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            tdata = Path(d)
            opt = tdata / "experimental_options.json"
            opt.write_text('{"foo": true}', encoding="utf-8")

            merge_experimental_options(
                tdata, "http://127.0.0.1:18765/translate?from=%f&to=%t&q=%q"
            )
            data = json.loads(opt.read_text(encoding="utf-8"))
            self.assertTrue(data["foo"])
            self.assertEqual(
                data["translate-url-template"],
                "http://127.0.0.1:18765/translate?from=%f&to=%t&q=%q",
            )
            self.assertTrue((tdata / "experimental_options.json.vi_backup").exists())

            restore_experimental_options(tdata)
            self.assertEqual(json.loads(opt.read_text(encoding="utf-8")), {"foo": True})
            self.assertFalse((tdata / "experimental_options.json.vi_backup").exists())

    def test_restore_removes_file_when_none_existed_before(self):
        with tempfile.TemporaryDirectory() as d:
            tdata = Path(d)
            merge_experimental_options(
                tdata, "http://127.0.0.1:18765/translate?from=%f&to=%t&q=%q"
            )
            self.assertTrue((tdata / "experimental_options.json").exists())
            restore_experimental_options(tdata)
            self.assertFalse((tdata / "experimental_options.json").exists())
            self.assertFalse((tdata / "experimental_options.json.vi_backup").exists())


if __name__ == "__main__":
    unittest.main()
