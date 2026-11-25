import os
import tempfile
import threading
import unittest
from pathlib import Path

from translator_proxy.cache import TranslationCache
from translator_proxy.config import TranslationConfig, load_config, save_config_updates


class TestConfigAndCache(unittest.TestCase):
    def test_load_config_defaults_and_override(self):
        with tempfile.TemporaryDirectory() as d:
            cfg_path = Path(d) / "config.json"
            cfg_path.write_text('{"port": 19000, "force_target_vi": false}', encoding="utf-8")
            cfg = load_config(cfg_path)
            self.assertEqual(cfg.port, 19000)
            self.assertFalse(cfg.force_target_vi)
            self.assertEqual(cfg.provider, "google_gtx")
            self.assertTrue(cfg.slang_fixups)
            self.assertTrue(cfg.fallback_to_google)
            self.assertIsInstance(cfg, TranslationConfig)

    def test_save_config_updates_and_env_override(self):
        with tempfile.TemporaryDirectory() as d:
            cfg_path = Path(d) / "config.json"
            saved = save_config_updates(
                {"provider": "ai", "ai_base_url": "https://api.example.com/v1", "ai_api_key": "sk-secret-1234"},
                cfg_path,
            )
            self.assertEqual(saved.provider, "ai")
            self.assertEqual(saved.ai_api_key, "sk-secret-1234")
            pub = saved.to_public_dict(mask_secret=True)
            self.assertNotIn("ai_api_key", pub)
            self.assertTrue(pub["has_ai_api_key"])

            old_env = os.environ.get("VI_TRANSLATE_AI_MODEL")
            os.environ["VI_TRANSLATE_AI_MODEL"] = "gemini-2.5-flash"
            try:
                reloaded = load_config(cfg_path)
                self.assertEqual(reloaded.ai_model, "gemini-2.5-flash")
            finally:
                if old_env is None:
                    os.environ.pop("VI_TRANSLATE_AI_MODEL", None)
                else:
                    os.environ["VI_TRANSLATE_AI_MODEL"] = old_env

    def test_cache_persists_across_reopen_and_namespaces_by_provider(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "cache.db"
            c1 = TranslationCache(db, max_memory_items=2)
            c1.put("google_gtx", "vi", "你好", "Xin chào")
            c1.put("ai", "vi", "你好", "Chào bạn")
            c1.put("google_gtx", "vi", "钱", "Tiền")
            c1.put("google_gtx", "vi", "0元购", "Mua 0 đồng")
            self.assertEqual(c1.count(), 4)
            c1.close()

            c2 = TranslationCache(db, max_memory_items=2)
            self.assertEqual(c2.get("google_gtx", "vi", "你好"), "Xin chào")
            self.assertEqual(c2.get("ai", "vi", "你好"), "Chào bạn")
            self.assertIsNone(c2.get("google_gtx", "en", "你好"))
            items = c2.list_recent(limit=10, query="0元购")
            self.assertEqual(len(items), 1)
            self.assertTrue(c2.delete_key(items[0]["cache_key"]))
            self.assertEqual(c2.count(), 3)
            removed = c2.clear("google_gtx")
            self.assertEqual(removed, 2)
            self.assertEqual(c2.count(), 1)
            c2.close()

    def test_cache_concurrent_writes_do_not_lock(self):
        with tempfile.TemporaryDirectory() as d:
            c = TranslationCache(Path(d) / "cache.db")
            errors = []

            def worker(idx: int) -> None:
                try:
                    for j in range(10):
                        c.put("google_gtx", "vi", f"msg-{idx}-{j}", f"tin-{idx}-{j}")
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            self.assertEqual(c.count(), 80)
            c.close()


if __name__ == "__main__":
    unittest.main()
