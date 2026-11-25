import tempfile
import unittest
from pathlib import Path

from translator_proxy.cache import TranslationCache
from translator_proxy.config import TranslationConfig
from translator_proxy.pretranslate import MtprotoPretranslator
from translator_proxy.providers import BaseTranslateProvider, ProviderError
from translator_proxy.server import SingleFlightTranslator


class BatchStubProvider(BaseTranslateProvider):
    def __init__(self, fail_batch: bool = False) -> None:
        self.fail_batch = fail_batch
        self.batch_calls: list[list[str]] = []
        self.single_calls: list[str] = []

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        self.single_calls.append(text)
        return f"[single] {text}"

    def translate_batch(self, texts: list[str], source_lang: str, target_lang: str) -> list[str]:
        self.batch_calls.append(list(texts))
        if self.fail_batch:
            raise ProviderError("batch failed")
        return [f"[batch] {t}" for t in texts]


class TestMtprotoPretranslator(unittest.TestCase):
    def test_pretranslate_batches_dedups_skips_cached_and_passthrough(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = TranslationConfig(
                provider="ai",
                target_lang="vi",
                cache_db=Path(d) / "c.db",
                mtproto_batch_size=2,
            )
            cache = TranslationCache(cfg.cache_db)
            cache.put("ai", "vi", "已缓存", "Đã cache")
            prov = BatchStubProvider()
            translator = SingleFlightTranslator(cache, prov, cfg)
            pre = MtprotoPretranslator(translator, session_path=Path(d) / "sess")

            checked, newly = pre.pretranslate_texts(
                [
                    "已缓存",
                    "https://t.me/test",
                    "Xin chào anh em",
                    "挂了",
                    "挂了",
                    "土区",
                    "上车",
                ]
            )
            self.assertEqual(checked, 7)
            self.assertEqual(newly, 3)
            self.assertEqual(prov.batch_calls, [["挂了", "土区"], ["上车"]])
            self.assertEqual(cache.get("ai", "vi", "挂了"), "[batch] 挂了")
            self.assertEqual(cache.get("ai", "vi", "土区"), "[batch] 土区")
            self.assertEqual(cache.get("ai", "vi", "上车"), "[batch] 上车")
            cache.close()

    def test_pretranslate_falls_back_per_item_when_batch_fails(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = TranslationConfig(
                provider="ai",
                target_lang="vi",
                cache_db=Path(d) / "c.db",
                mtproto_batch_size=4,
            )
            cache = TranslationCache(cfg.cache_db)
            prov = BatchStubProvider(fail_batch=True)
            translator = SingleFlightTranslator(cache, prov, cfg)
            pre = MtprotoPretranslator(translator, session_path=Path(d) / "sess")

            checked, newly = pre.pretranslate_texts(["挂了", "上车"])
            self.assertEqual(checked, 2)
            self.assertEqual(newly, 2)
            self.assertEqual(prov.single_calls, ["挂了", "上车"])
            self.assertEqual(cache.get("ai", "vi", "挂了"), "[single] 挂了")
            cache.close()

    def test_start_qr_login_serializes_connect_and_returns_svg(self):
        import asyncio

        class FakeQr:
            url = "tg://login?token=test_token"
            async def wait(self, timeout=120):
                await asyncio.sleep(0.05)
                raise TimeoutError("expired")

        class FakeClient:
            def __init__(self):
                self.connect_calls = 0
                self.connected = False
            def is_connected(self):
                return self.connected
            async def connect(self):
                self.connect_calls += 1
                await asyncio.sleep(0.05)
                self.connected = True
            async def is_user_authorized(self):
                return False
            async def qr_login(self):
                return FakeQr()
            async def disconnect(self):
                self.connected = False

        with tempfile.TemporaryDirectory() as d:
            cfg = TranslationConfig(
                provider="ai",
                target_lang="vi",
                cache_db=Path(d) / "c.db",
                mtproto_enabled=True,
                mtproto_api_id=12345,
                mtproto_api_hash="abcdef123456",
            )
            cache = TranslationCache(cfg.cache_db)
            prov = BatchStubProvider()
            translator = SingleFlightTranslator(cache, prov, cfg)
            fake_client = FakeClient()
            pre = MtprotoPretranslator(
                translator,
                session_path=Path(d) / "sess",
                client_factory=lambda s, i, h: fake_client,
            )
            pre.start()
            try:
                state = pre.start_qr_login()
                self.assertEqual(fake_client.connect_calls, 1)
                self.assertEqual(state["qr_url"], "tg://login?token=test_token")
                self.assertTrue(state["qr_image"].startswith("data:image/svg+xml;base64,"))
            finally:
                pre.stop()
                cache.close()


if __name__ == "__main__":
    unittest.main()
