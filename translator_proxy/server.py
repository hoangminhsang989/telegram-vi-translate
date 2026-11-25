from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from translator_proxy.cache import TranslationCache
from translator_proxy.config import (
    DEFAULT_USER_CONFIG_PATH,
    TranslationConfig,
    save_config_updates,
)
from translator_proxy.dashboard import DASHBOARD_HTML
from translator_proxy.pretranslate import MtprotoPretranslator
from translator_proxy.providers import (
    BaseTranslateProvider,
    GoogleGtxProvider,
    ProviderError,
    create_provider,
    prepare_source_text,
)

LOGGER = logging.getLogger("translator_proxy")
_REPO_ROOT = Path(__file__).resolve().parent.parent
_LAUNCHER_EXE = _REPO_ROOT / "shim_mscms" / "target" / "release" / "vi_launcher.exe"


class _InFlightSlot:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: str = ""
        self.error: Exception | None = None


def _build_fallback_provider(cfg: TranslationConfig) -> GoogleGtxProvider | None:
    if cfg.provider == "ai" and cfg.fallback_to_google:
        return GoogleGtxProvider(timeout_sec=cfg.timeout_sec, slang_fixups=cfg.slang_fixups)
    return None


def _cache_namespace(cfg: TranslationConfig) -> str:
    if cfg.provider == "google_gtx" and cfg.slang_fixups:
        return "google_gtx:slang"
    return cfg.provider


class SingleFlightTranslator:
    """Deduplicates concurrent identical translations and enforces cache + target language."""

    def __init__(
        self,
        cache: TranslationCache,
        provider: BaseTranslateProvider,
        cfg: TranslationConfig,
        max_upstream_concurrency: int = 10,
        config_path: Path | None = None,
    ) -> None:
        self.cache = cache
        self.provider = provider
        self.cfg = cfg
        self._config_path = config_path or DEFAULT_USER_CONFIG_PATH
        self._fallback_provider = _build_fallback_provider(cfg)
        self._cfg_lock = threading.RLock()
        self._inflight_lock = threading.Lock()
        self._inflight: dict[tuple[str, str, str], _InFlightSlot] = {}
        self._upstream_sem = threading.Semaphore(max(1, max_upstream_concurrency))

    def cache_namespace(self) -> str:
        with self._cfg_lock:
            return _cache_namespace(self.cfg)

    def reload_config(self, new_cfg: TranslationConfig) -> None:
        new_provider = create_provider(new_cfg)
        new_fallback = _build_fallback_provider(new_cfg)
        with self._cfg_lock:
            self.cfg = new_cfg
            self.provider = new_provider
            self._fallback_provider = new_fallback

    def effective_target_lang(self, requested_target: str) -> str:
        with self._cfg_lock:
            if self.cfg.force_target_vi:
                return self.cfg.target_lang or "vi"
            return requested_target.strip() or self.cfg.target_lang or "vi"

    def translate_text(
        self,
        raw_q: str,
        source_lang: str,
        target_lang: str,
        *,
        bypass_cache: bool = False,
    ) -> str:
        text, _ = self.translate_text_with_meta(
            raw_q, source_lang, target_lang, bypass_cache=bypass_cache
        )
        return text

    def translate_text_with_meta(
        self,
        raw_q: str,
        source_lang: str,
        target_lang: str,
        *,
        bypass_cache: bool = False,
    ) -> tuple[str, str]:
        target = self.effective_target_lang(target_lang)
        text, skip = prepare_source_text(raw_q, target_lang=target)
        if skip:
            return text, "passthrough"

        with self._cfg_lock:
            cfg = self.cfg
            provider = self.provider
            fallback_provider = self._fallback_provider
            primary_ns = _cache_namespace(cfg)

        if not bypass_cache:
            cached = self.cache.get(primary_ns, target, text)
            if cached is not None:
                return cached, primary_ns

        flight_key = (primary_ns, target, text)
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
            return slot.result, primary_ns

        used_provider = primary_ns
        try:
            with self._upstream_sem:
                if not bypass_cache:
                    cached_again = self.cache.get(primary_ns, target, text)
                    if cached_again is not None:
                        slot.result = cached_again
                        return cached_again, primary_ns

                try:
                    translated = provider.translate(text, source_lang, target)
                    if not bypass_cache:
                        self.cache.put(primary_ns, target, text, translated)
                except ProviderError as primary_err:
                    if fallback_provider is None:
                        raise
                    LOGGER.warning("Primary provider failed (%s); falling back to Google GTX", primary_err)
                    fallback_ns = "google_gtx:slang" if cfg.slang_fixups else "google_gtx"
                    used_provider = fallback_ns
                    cached_fb = None if bypass_cache else self.cache.get(fallback_ns, target, text)
                    if cached_fb is not None:
                        translated = cached_fb
                    else:
                        translated = fallback_provider.translate(text, source_lang, target)
                        if not bypass_cache:
                            self.cache.put(fallback_ns, target, text, translated)
            slot.result = translated
            return translated, used_provider
        except Exception as exc:
            slot.error = exc
            raise
        finally:
            with self._inflight_lock:
                self._inflight.pop(flight_key, None)
            slot.event.set()


def _probe_telegram_patch() -> dict[str, Any]:
    if not _LAUNCHER_EXE.exists():
        return {"checked": False}
    try:
        proc = subprocess.run(
            [str(_LAUNCHER_EXE), "--check", "--no-proxy"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        raw = proc.stdout.strip()
        if raw.startswith("{"):
            data = json.loads(raw)
            data["checked"] = True
            return data
    except Exception:
        pass
    return {"checked": False}


class _ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        translator: SingleFlightTranslator,
        pretranslator: MtprotoPretranslator,
    ) -> None:
        self.translator = translator
        self.pretranslator = pretranslator
        super().__init__(server_address, handler_class)


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _safe_write(self, status: int, content_type: str, body: bytes) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            LOGGER.debug("Client closed HTTP connection early")

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._safe_write(status, "application/json; charset=utf-8", body)

    def _read_json_body(self, allow_empty: bool = False) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0 and allow_empty:
            return {}
        if length <= 0 or length > 1_048_576:
            raise ValueError("Invalid JSON request body size")
        raw = self.rfile.read(length)
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("Expected JSON object")
        return parsed

    def _apply_mtproto_updates(
        self,
        updates: dict[str, Any],
        *,
        trigger_connect: bool = True,
    ) -> TranslationConfig:
        translator: SingleFlightTranslator = self.server.translator  # type: ignore[attr-defined]
        pretranslator: MtprotoPretranslator = self.server.pretranslator  # type: ignore[attr-defined]
        allowed = {
            k: v
            for k, v in updates.items()
            if k in {
                "mtproto_enabled",
                "mtproto_api_id",
                "mtproto_api_hash",
                "mtproto_chats",
                "mtproto_prefetch_limit",
                "mtproto_batch_size",
                "mtproto_listen_new",
            }
        }
        if not allowed:
            return translator.cfg
        new_cfg = save_config_updates(allowed, translator._config_path)
        translator.reload_config(new_cfg)
        if trigger_connect:
            pretranslator.notify_config_updated(new_cfg)
        return new_cfg

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        translator: SingleFlightTranslator = self.server.translator  # type: ignore[attr-defined]
        pretranslator: MtprotoPretranslator = self.server.pretranslator  # type: ignore[attr-defined]

        if parsed.path == "/":
            self._safe_write(
                HTTPStatus.OK,
                "text/html; charset=utf-8",
                DASHBOARD_HTML.encode("utf-8"),
            )
            return

        if parsed.path == "/health":
            with translator._cfg_lock:
                cfg = translator.cfg
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "provider": cfg.provider,
                    "target_lang": cfg.target_lang,
                    "cached": translator.cache.count(),
                },
            )
            return

        if parsed.path == "/api/status":
            with translator._cfg_lock:
                cfg = translator.cfg
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "cached": translator.cache.count(),
                    "config": cfg.to_public_dict(mask_secret=True),
                    "telegram": _probe_telegram_patch(),
                    "mtproto": pretranslator.status(),
                },
            )
            return

        if parsed.path == "/api/mtproto/status":
            with translator._cfg_lock:
                pub = translator.cfg.to_public_dict(mask_secret=True)
            self._send_json(
                HTTPStatus.OK,
                {
                    "state": pretranslator.status(),
                    "config": {
                        "mtproto_enabled": pub["mtproto_enabled"],
                        "mtproto_api_id": pub["mtproto_api_id"],
                        "has_mtproto_api_hash": pub["has_mtproto_api_hash"],
                        "mtproto_api_hash_preview": pub["mtproto_api_hash_preview"],
                        "mtproto_chats": pub["mtproto_chats"],
                        "mtproto_prefetch_limit": pub["mtproto_prefetch_limit"],
                        "mtproto_batch_size": pub["mtproto_batch_size"],
                        "mtproto_listen_new": pub["mtproto_listen_new"],
                    },
                },
            )
            return

        if parsed.path == "/api/mtproto/dialogs":
            try:
                dialogs = pretranslator.list_dialogs()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"dialogs": dialogs})
            return

        if parsed.path == "/api/cache":
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            q = qs.get("q", [""])[0]
            try:
                limit = int(qs.get("limit", ["40"])[0])
            except ValueError:
                limit = 40
            items = translator.cache.list_recent(limit=limit, query=q)
            self._send_json(HTTPStatus.OK, {"items": items, "total": translator.cache.count()})
            return

        if parsed.path == "/translate":
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            raw_q = qs.get("q", [""])[0]
            source_lang = qs.get("from", ["auto"])[0]
            with translator._cfg_lock:
                default_target = translator.cfg.target_lang
            target_lang = qs.get("to", [default_target])[0]
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

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        translator: SingleFlightTranslator = self.server.translator  # type: ignore[attr-defined]
        pretranslator: MtprotoPretranslator = self.server.pretranslator  # type: ignore[attr-defined]

        if parsed.path == "/api/config":
            try:
                updates = self._read_json_body()
                new_cfg = save_config_updates(updates, translator._config_path)
                translator.reload_config(new_cfg)
                pretranslator.notify_config_updated(new_cfg)
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "config": new_cfg.to_public_dict(mask_secret=True)},
            )
            return

        if parsed.path == "/api/mtproto/config":
            try:
                updates = self._read_json_body()
                new_cfg = self._apply_mtproto_updates(updates)
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "config": new_cfg.to_public_dict(mask_secret=True)},
            )
            return

        if parsed.path == "/api/mtproto/qr":
            try:
                body = self._read_json_body(allow_empty=True)
                self._apply_mtproto_updates(body, trigger_connect=False)
                state = pretranslator.start_qr_login()
            except Exception as exc:
                LOGGER.warning("MTProto QR login failed: %s", exc)
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "state": state})
            return

        if parsed.path == "/api/mtproto/code":
            try:
                body = self._read_json_body()
                self._apply_mtproto_updates(body, trigger_connect=False)
                res = pretranslator.send_phone_code(str(body.get("phone", "")))
            except Exception as exc:
                LOGGER.warning("MTProto send code failed: %s", exc)
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, res)
            return

        if parsed.path == "/api/mtproto/signin":
            try:
                body = self._read_json_body()
                state = pretranslator.complete_sign_in(
                    code=str(body.get("code", "")),
                    password=str(body.get("password", "")),
                )
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "state": state})
            return

        if parsed.path == "/api/mtproto/sync":
            try:
                body = self._read_json_body(allow_empty=True)
                if body:
                    self._apply_mtproto_updates(body)
                state = pretranslator.trigger_sync_now()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "state": state})
            return

        if parsed.path == "/api/mtproto/logout":
            try:
                state = pretranslator.logout()
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "state": state})
            return

        if parsed.path == "/api/test":
            try:
                body = self._read_json_body()
                raw_text = str(body.get("text", ""))
                source_lang = str(body.get("from", "auto"))
                target_lang = str(body.get("to", translator.cfg.target_lang))
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return

            t0 = time.perf_counter()
            try:
                translated, used = translator.translate_text_with_meta(
                    raw_text, source_lang, target_lang, bypass_cache=True
                )
            except ProviderError as exc:
                self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
                return
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            self._send_json(
                HTTPStatus.OK,
                {"translated": translated, "used_provider": used, "elapsed_ms": elapsed_ms},
            )
            return

        if parsed.path == "/api/cache/clear":
            try:
                body = self._read_json_body()
                prov = str(body.get("provider", "")).strip()
            except Exception:
                prov = ""
            if prov == "google_gtx":
                removed = translator.cache.clear("google_gtx") + translator.cache.clear("google_gtx:slang")
            elif prov:
                removed = translator.cache.clear(prov)
            else:
                removed = translator.cache.clear(None)
            self._send_json(HTTPStatus.OK, {"ok": True, "removed": removed, "cached": translator.cache.count()})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        translator: SingleFlightTranslator = self.server.translator  # type: ignore[attr-defined]
        if parsed.path == "/api/cache":
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            key = qs.get("key", [""])[0].strip()
            deleted = translator.cache.delete_key(key) if key else False
            self._send_json(HTTPStatus.OK, {"ok": deleted, "cached": translator.cache.count()})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        LOGGER.debug(format, *args)


def create_http_server(
    cfg: TranslationConfig,
    translator: SingleFlightTranslator | None = None,
    config_path: Path | None = None,
    pretranslator: MtprotoPretranslator | None = None,
) -> ThreadingHTTPServer:
    """Create a multi-threaded localhost HTTP translation server."""
    if translator is None:
        cache = TranslationCache(cfg.cache_db, max_memory_items=cfg.max_memory_items)
        provider = create_provider(cfg)
        translator = SingleFlightTranslator(
            cache=cache,
            provider=provider,
            cfg=cfg,
            config_path=config_path,
        )
    if pretranslator is None:
        pretranslator = MtprotoPretranslator(translator)
    return _ProxyServer((cfg.host, cfg.port), _ProxyHandler, translator, pretranslator)
