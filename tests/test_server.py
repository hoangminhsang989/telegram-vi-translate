import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from translator_proxy.cache import TranslationCache
from translator_proxy.config import TranslationConfig
from translator_proxy.providers import BaseTranslateProvider, ProviderError
from translator_proxy.server import SingleFlightTranslator, create_http_server


class StubProvider(BaseTranslateProvider):
    def __init__(self, fail: bool = False, delay: float = 0.0):
        self.calls = 0
        self.fail = fail
        self.delay = delay
        self.last_target: str | None = None
        self.lock = threading.Lock()

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            self.calls += 1
            self.last_target = target_lang
        if self.fail:
            raise ProviderError("upstream down")
        return f"[vi] {text}"


class TestServer(unittest.TestCase):
    def _start(self, provider: BaseTranslateProvider):
        d = tempfile.TemporaryDirectory()
        cfg = TranslationConfig(
            host="127.0.0.1",
            port=0,
            force_target_vi=True,
            target_lang="vi",
            cache_db=Path(d.name) / "c.db",
        )
        cache = TranslationCache(cfg.cache_db)
        svc = SingleFlightTranslator(cache, provider, cfg)
        srv = create_http_server(cfg, svc)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        return d, cache, srv

    def test_forces_vi_wraps_json_array_and_handles_braces(self):
        prov = StubProvider()
        d, cache, srv = self._start(prov)
        try:
            q = urllib.parse.quote('{"a": &quot;测试&quot;}')
            url = f"http://127.0.0.1:{srv.server_port}/translate?from=zh&to=en&q={q}"
            with urllib.request.urlopen(url, timeout=5) as r:
                body = json.loads(r.read().decode("utf-8"))
            self.assertEqual(prov.last_target, "vi")
            self.assertEqual(body, [[['[vi] {"a": "测试"}']]])
        finally:
            srv.shutdown()
            srv.server_close()
            cache.close()
            d.cleanup()

    def test_skips_upstream_for_url_empty_and_vietnamese_messages(self):
        prov = StubProvider()
        d, cache, srv = self._start(prov)
        try:
            for raw in ["https://one.google.com/sku", "Xin chào mọi người nhé"]:
                url = f"http://127.0.0.1:{srv.server_port}/translate?from=auto&to=en&q={urllib.parse.quote(raw)}"
                with urllib.request.urlopen(url, timeout=5) as r:
                    body = json.loads(r.read().decode("utf-8"))
                self.assertEqual(body, [[[raw]]])
            self.assertEqual(prov.calls, 0)
        finally:
            srv.shutdown()
            srv.server_close()
            cache.close()
            d.cleanup()

    def test_burst_20_duplicate_requests_hit_upstream_once(self):
        prov = StubProvider(delay=0.05)
        d, cache, srv = self._start(prov)
        try:
            results = []
            errors = []

            def req() -> None:
                try:
                    url = (
                        f"http://127.0.0.1:{srv.server_port}/translate?from=zh&to=en&q="
                        + urllib.parse.quote("咪 哈基")
                    )
                    with urllib.request.urlopen(url, timeout=5) as r:
                        results.append(json.loads(r.read().decode("utf-8"))[0][0][0])
                except Exception as exc:
                    errors.append(exc)

            ts = [threading.Thread(target=req) for _ in range(20)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 20)
            self.assertEqual(set(results), {"[vi] 咪 哈基"})
            self.assertEqual(prov.calls, 1)
            self.assertEqual(cache.count(), 1)
        finally:
            srv.shutdown()
            srv.server_close()
            cache.close()
            d.cleanup()

    def test_upstream_failure_returns_502_and_does_not_poison_cache(self):
        prov = StubProvider(fail=True)
        d, cache, srv = self._start(prov)
        try:
            url = (
                f"http://127.0.0.1:{srv.server_port}/translate?from=zh&to=vi&q="
                + urllib.parse.quote("钱")
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(url, timeout=5)
            self.assertEqual(ctx.exception.code, 502)
            self.assertEqual(cache.count(), 0)
        finally:
            srv.shutdown()
            srv.server_close()
            cache.close()
            d.cleanup()

    def test_health_endpoint_reports_status_and_cached_count(self):
        prov = StubProvider()
        d, cache, srv = self._start(prov)
        try:
            url = f"http://127.0.0.1:{srv.server_port}/health"
            with urllib.request.urlopen(url, timeout=5) as r:
                health = json.loads(r.read().decode("utf-8"))
            self.assertTrue(health["ok"])
            self.assertEqual(health["target_lang"], "vi")
            self.assertEqual(health["cached"], 0)
        finally:
            srv.shutdown()
            srv.server_close()
            cache.close()
            d.cleanup()


if __name__ == "__main__":
    unittest.main()
