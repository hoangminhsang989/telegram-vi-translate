import json
import urllib.parse
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
        _, skip_vi_to_vi = prepare_source_text("Xin chào mọi người", target_lang="vi")
        _, skip_vi_to_en = prepare_source_text("Xin chào mọi người", target_lang="en")
        self.assertTrue(skip_vi_to_vi)
        self.assertFalse(skip_vi_to_en)

    def test_google_gtx_joins_multi_segment_response(self):
        payload = json.dumps([[["Xin chào ", "你好 "], ["thế giới", "世界"]]]).encode("utf-8")
        p = GoogleGtxProvider(opener=lambda req, timeout: FakeResp(payload), slang_fixups=False)
        self.assertEqual(p.translate("你好 世界", "zh", "vi"), "Xin chào thế giới")

    def test_google_gtx_protects_urls_mentions_and_expands_chinese_slang(self):
        seen_q = []

        def fake_open(req, timeout):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
            q = qs["q"][0]
            seen_q.append(q)
            return FakeResp(json.dumps([[[q.replace("土耳其区", "khu vực Thổ Nhĩ Kỳ"), q]]]).encode("utf-8"))

        p = GoogleGtxProvider(opener=fake_open, slang_fixups=True)
        out = p.translate("换土区 0元购 https://t.me/test @admin `cmd`", "zh", "vi")
        self.assertIn("土耳其区", seen_q[0])
        self.assertNotIn("https://t.me/test", seen_q[0])
        self.assertIn("săn deal 0đ", out)
        self.assertIn("khu vực Thổ Nhĩ Kỳ", out)
        self.assertIn("https://t.me/test", out)
        self.assertIn("@admin", out)
        self.assertIn("`cmd`", out)

    def test_google_gtx_splits_long_text_and_skips_blank_chunks(self):
        calls = []

        def fake_open(req, timeout):
            calls.append(req.full_url)
            return FakeResp(json.dumps([[["dòng", "line"]]]).encode("utf-8"))

        p = GoogleGtxProvider(opener=fake_open, max_chunk_chars=20, slang_fixups=False)
        out = p.translate("第一行很长的测试内容在这里\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n第二行也很长的测试内容在这里", "zh", "vi")
        self.assertEqual(len(calls), 2)
        self.assertTrue(out.startswith("dòng"))
        self.assertTrue(out.endswith("dòng"))

    def test_google_gtx_raises_provider_error_on_empty_or_bad_json(self):
        p = GoogleGtxProvider(opener=lambda req, timeout: FakeResp(b"not-json"), retries=0)
        with self.assertRaises(ProviderError):
            p.translate("你好", "zh", "vi")

    def test_ai_provider_includes_glossary_and_strips_think_block(self):
        captured = {}

        def fake_open(req, timeout):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["url"] = req.full_url
            return FakeResp(
                json.dumps(
                    {"choices": [{"message": {"content": "<think>plan</think>\nSăn kèo 0 đồng"}}]}
                ).encode("utf-8")
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

    def test_ai_provider_supports_gemini_native_and_sse_fallback(self):
        gemini_urls = []

        def fake_gemini(req, timeout):
            gemini_urls.append(req.full_url)
            return FakeResp(
                json.dumps(
                    {"candidates": [{"content": {"parts": [{"text": "Chốt kèo"}]}}]}
                ).encode("utf-8")
            )

        gp = AiTranslateProvider(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            api_key="g-key",
            model="gemini-2.5-flash",
            opener=fake_gemini,
        )
        self.assertEqual(gp.translate("上车", "zh", "vi"), "Chốt kèo")
        self.assertIn(":generateContent?key=g-key", gemini_urls[0])

        sse_body = (
            'data: {"choices":[{"delta":{"content":"Xin "}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"chào"}}]}\n\n'
            "data: [DONE]\n"
        ).encode("utf-8")
        sp = AiTranslateProvider(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="gpt-4o-mini",
            opener=lambda req, timeout: FakeResp(sse_body),
        )
        self.assertEqual(sp.translate("你好", "zh", "vi"), "Xin chào")

    def test_create_provider_falls_back_to_google_when_ai_not_configured(self):
        cfg = TranslationConfig(provider="ai", ai_base_url="", ai_api_key="")
        with self.assertRaises(ProviderError):
            create_provider(cfg)
        cfg_local = TranslationConfig(provider="ai", ai_base_url="http://127.0.0.1:11434/v1", ai_api_key="")
        self.assertIsInstance(create_provider(cfg_local), AiTranslateProvider)
        cfg_google = TranslationConfig(provider="google_gtx")
        self.assertIsInstance(create_provider(cfg_google), GoogleGtxProvider)

    def test_ai_provider_translate_batch_and_partial_fallback(self):
        calls = []

        def fake_open(req, timeout):
            body = json.loads(req.data.decode("utf-8"))
            calls.append(body)
            if len(calls) == 1:
                content = json.dumps(
                    {
                        "translations": [
                            {"id": 0, "vi": "Die rồi"},
                        ]
                    },
                    ensure_ascii=False,
                )
                return FakeResp(
                    json.dumps({"choices": [{"message": {"content": f"```json\n{content}\n```"}}]}).encode("utf-8")
                )
            return FakeResp(
                json.dumps({"choices": [{"message": {"content": "Chốt kèo"}}]}).encode("utf-8")
            )

        p = AiTranslateProvider(
            base_url="http://127.0.0.1:20131/v1",
            api_key="",
            model="co-gpt-5.6-terra",
            opener=fake_open,
        )
        out = p.translate_batch(["挂了", "上车"], "zh", "vi")
        self.assertEqual(out, ["Die rồi", "Chốt kèo"])
        self.assertNotIn("temperature", calls[0])
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
