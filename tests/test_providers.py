import json
import unittest

from translator_proxy.config import TranslationConfig
from translator_proxy.providers import (
    AiTranslateProvider,
    GoogleGtxProvider,
    ProviderError,
    create_provider,
    prepare_source_text,
)


class FakeResp:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestProviders(unittest.TestCase):
    def test_prepare_source_text_unescapes_html_and_skips_non_translatable(self):
        text, skip = prepare_source_text("a &amp; b &quot;0元购&quot; &lt;ok&gt;")
        self.assertEqual(text, 'a & b "0元购" <ok>')
        self.assertFalse(skip)
        for trivial in ["", "   ", "https://one.google.com/sku?a=1", "123456", "@leon_01"]:
            _, should_skip = prepare_source_text(trivial)
            self.assertTrue(should_skip, trivial)

    def test_google_gtx_joins_multi_segment_response(self):
        payload = json.dumps([[["Xin chào ", "你好 "], ["thế giới", "世界"]]]).encode("utf-8")
        p = GoogleGtxProvider(opener=lambda req, timeout: FakeResp(payload))
        self.assertEqual(p.translate("你好 世界", "zh", "vi"), "Xin chào thế giới")

    def test_google_gtx_splits_long_text_preserving_newlines(self):
        calls = []

        def fake_open(req, timeout):
            calls.append(req.full_url)
            return FakeResp(json.dumps([[["dòng", "line"]]]).encode("utf-8"))

        p = GoogleGtxProvider(opener=fake_open, max_chunk_chars=20)
        out = p.translate("第一行很长的测试内容在这里\n第二行也很长的测试内容在这里", "zh", "vi")
        self.assertEqual(len(calls), 2)
        self.assertEqual(out, "dòng\ndòng")

    def test_google_gtx_raises_provider_error_on_empty_or_bad_json(self):
        p = GoogleGtxProvider(opener=lambda req, timeout: FakeResp(b"not-json"))
        with self.assertRaises(ProviderError):
            p.translate("你好", "zh", "vi")

    def test_ai_provider_includes_glossary_and_parses_chat_completion(self):
        captured = {}

        def fake_open(req, timeout):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["url"] = req.full_url
            return FakeResp(
                json.dumps({"choices": [{"message": {"content": "Săn kèo 0 đồng"}}]}).encode("utf-8")
            )

        p = AiTranslateProvider(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="gpt-4o-mini",
            glossary={"0元购": "săn deal 0 đồng"},
            opener=fake_open,
        )
        out = p.translate("0元购", "zh", "vi")
        self.assertEqual(out, "Săn kèo 0 đồng")
        self.assertEqual(captured["url"], "https://api.example.com/v1/chat/completions")
        sys_msg = captured["body"]["messages"][0]["content"]
        self.assertIn("0元购", sys_msg)
        self.assertIn("săn deal 0 đồng", sys_msg)

    def test_create_provider_falls_back_to_google_when_ai_not_configured(self):
        cfg = TranslationConfig(provider="ai", ai_base_url="", ai_api_key="")
        with self.assertRaises(ProviderError):
            create_provider(cfg)
        cfg_google = TranslationConfig(provider="google_gtx")
        self.assertIsInstance(create_provider(cfg_google), GoogleGtxProvider)


if __name__ == "__main__":
    unittest.main()
