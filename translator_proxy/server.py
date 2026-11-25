from __future__ import annotations

import json
import logging
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from translator_proxy.cache import TranslationCache
from translator_proxy.config import TranslationConfig
from translator_proxy.providers import (
    BaseTranslateProvider,
    ProviderError,
    create_provider,
    prepare_source_text,
)

LOGGER = logging.getLogger("translator_proxy")


class _InFlightSlot:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: str = ""
        self.error: Exception | None = None


class SingleFlightTranslator:
    """Deduplicates concurrent identical translations and enforces cache + target language."""

    def __init__(
        self,
        cache: TranslationCache,
        provider: BaseTranslateProvider,
        cfg: TranslationConfig,
        max_upstream_concurrency: int = 4,
    ) -> None:
        self.cache = cache
        self.provider = provider
        self.cfg = cfg
        self._inflight_lock = threading.Lock()
        self._inflight: dict[tuple[str, str, str], _InFlightSlot] = {}
        self._upstream_sem = threading.Semaphore(max(1, max_upstream_concurrency))

    def effective_target_lang(self, requested_target: str) -> str:
        if self.cfg.force_target_vi:
            return self.cfg.target_lang or "vi"
        return requested_target.strip() or self.cfg.target_lang or "vi"

    def translate_text(self, raw_q: str, source_lang: str, target_lang: str) -> str:
        text, skip = prepare_source_text(raw_q)
        if skip:
            return text

        target = self.effective_target_lang(target_lang)
        provider_name = self.cfg.provider

        cached = self.cache.get(provider_name, target, text)
        if cached is not None:
            return cached

        flight_key = (provider_name, target, text)
        with self._inflight_lock:
            slot = self._inflight.get(flight_key)
            if slot is None:
                slot = _InFlightSlot()
                self._inflight[flight_key] = slot
                is_leader = True
            else:
                is_leader = False

        if not is_leader:
            slot.event.wait()
            if slot.error is not None:
                raise slot.error
            return slot.result

        try:
            with self._upstream_sem:
                # Re-check cache after acquiring semaphore in case a prior request finished.
                cached_again = self.cache.get(provider_name, target, text)
                if cached_again is not None:
                    translated = cached_again
                else:
                    translated = self.provider.translate(text, source_lang, target)
                    self.cache.put(provider_name, target, text, translated)
            slot.result = translated
            return translated
        except Exception as exc:
            slot.error = exc
            raise
        finally:
            with self._inflight_lock:
                self._inflight.pop(flight_key, None)
            slot.event.set()


class _ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        translator: SingleFlightTranslator,
    ) -> None:
        self.translator = translator
        super().__init__(server_address, handler_class)


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        translator: SingleFlightTranslator = self.server.translator  # type: ignore[attr-defined]

        if parsed.path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "provider": translator.cfg.provider,
                    "target_lang": translator.cfg.target_lang,
                    "cached": translator.cache.count(),
                },
            )
            return

        if parsed.path == "/translate":
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            raw_q = qs.get("q", [""])[0]
            source_lang = qs.get("from", ["auto"])[0]
            target_lang = qs.get("to", [translator.cfg.target_lang])[0]
            try:
                translated = translator.translate_text(raw_q, source_lang, target_lang)
            except ProviderError as exc:
                LOGGER.warning("Translation upstream error: %s", exc)
                self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
                return
            except Exception as exc:  # pragma: no cover - defensive guard
                LOGGER.exception("Unexpected server error")
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return

            self._send_json(HTTPStatus.OK, [[[translated]]])
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        LOGGER.debug(format, *args)


def create_http_server(
    cfg: TranslationConfig,
    translator: SingleFlightTranslator | None = None,
) -> ThreadingHTTPServer:
    """Create a multi-threaded localhost HTTP translation server."""
    if translator is None:
        cache = TranslationCache(cfg.cache_db, max_memory_items=cfg.max_memory_items)
        provider = create_provider(cfg)
        translator = SingleFlightTranslator(cache=cache, provider=provider, cfg=cfg)
    return _ProxyServer((cfg.host, cfg.port), _ProxyHandler, translator)
