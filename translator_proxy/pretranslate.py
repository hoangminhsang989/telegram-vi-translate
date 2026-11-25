from __future__ import annotations

import asyncio
import base64
import io
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from translator_proxy.config import TranslationConfig
from translator_proxy.providers import ProviderError, prepare_source_text

LOGGER = logging.getLogger("translator_proxy.mtproto")

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SESSION_PATH = _REPO_ROOT / "data" / "tg_pretranslate"


def _render_qr_svg_data_url(url: str) -> str:
    try:
        import qrcode
        import qrcode.image.svg

        img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
        buf = io.BytesIO()
        img.save(buf)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/svg+xml;base64,{b64}"
    except Exception:
        return ""


class MtprotoPretranslator:
    """Background Telethon MTProto worker that batch-pretranslates recent & incoming chat messages."""

    def __init__(
        self,
        translator: Any,
        session_path: Path | None = None,
        client_factory: Callable[[str, int, str], Any] | None = None,
    ) -> None:
        self._translator = translator
        self._session_path = Path(session_path or DEFAULT_SESSION_PATH)
        self._session_path.parent.mkdir(parents=True, exist_ok=True)
        self._client_factory = client_factory

        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._connect_lock: asyncio.Lock | None = None
        self._client: Any = None
        self._client_creds: tuple[int, str] | None = None
        self._qr_login: Any = None
        self._qr_wait_task: asyncio.Task[Any] | None = None
        self._phone_number: str = ""
        self._phone_code_hash: str = ""
        self._listener_task: asyncio.Future[Any] | None = None
        self._sync_task: asyncio.Task[Any] | None = None
        self._new_handler_registered = False
        self._stop_requested = False

        self._state: dict[str, Any] = {
            "available": True,
            "connected": False,
            "authorized": False,
            "needs_2fa": False,
            "user_label": "",
            "qr_url": "",
            "qr_image": "",
            "syncing": False,
            "listening": False,
            "current_chat": "",
            "synced_checked": 0,
            "synced_translated": 0,
            "last_sync_at": 0.0,
            "last_error": "",
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _set_state(self, **kwargs: Any) -> None:
        with self._lock:
            self._state.update(kwargs)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_requested = False
            ready = threading.Event()
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(ready,),
                name="mtproto-pretranslator",
                daemon=True,
            )
            self._thread.start()
        ready.wait(timeout=5)
        cfg = self._translator.cfg
        if cfg.mtproto_enabled and cfg.mtproto_api_id > 0 and cfg.mtproto_api_hash:
            self._submit_coro(self._auto_connect_and_run())

    def stop(self) -> None:
        self._stop_requested = True
        loop = self._loop
        if loop and loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self._shutdown_async(), loop)
            try:
                fut.result(timeout=5)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def notify_config_updated(self, cfg: TranslationConfig) -> None:
        if not self._loop or not self._loop.is_running():
            return
        if cfg.mtproto_enabled and cfg.mtproto_api_id > 0 and cfg.mtproto_api_hash:
            self._submit_coro(self._auto_connect_and_run())
        else:
            self._submit_coro(self._stop_sync_only())

    def _run_loop(self, ready: threading.Event) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._connect_lock = asyncio.Lock()
        ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                loop.close()
            except Exception:
                pass

    def _submit_coro(self, coro: Any, wait: bool = False, timeout: float = 20.0) -> Any:
        if not self._loop or not self._loop.is_running():
            self.start()
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        if wait:
            return fut.result(timeout=timeout)
        return fut

    async def _shutdown_async(self) -> None:
        await self._stop_sync_only()
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._set_state(connected=False, listening=False)

    async def _stop_sync_only(self) -> None:
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
        self._sync_task = None
        self._set_state(syncing=False, listening=False, current_chat="")

    def _make_client(self, api_id: int, api_hash: str) -> Any:
        if self._client_factory is not None:
            return self._client_factory(str(self._session_path), api_id, api_hash)
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            self._set_state(available=False, last_error=f"Chưa cài telethon: {exc}")
            raise RuntimeError("Telethon chưa được cài đặt") from exc
        return TelegramClient(str(self._session_path), api_id, api_hash)

    async def _ensure_client(self) -> Any:
        cfg = self._translator.cfg
        if cfg.mtproto_api_id <= 0 or not cfg.mtproto_api_hash:
            raise RuntimeError("Cần nhập MTProto api_id và api_hash trước")
        creds = (cfg.mtproto_api_id, cfg.mtproto_api_hash)
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            if self._client is not None and self._client_creds != creds:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                self._client = None
                self._new_handler_registered = False
            if self._client is None:
                self._client = self._make_client(creds[0], creds[1])
                self._client_creds = creds
            if not self._client.is_connected():
                await self._client.connect()
            self._set_state(connected=True, available=True)
            return self._client

    async def _refresh_user_info(self, client: Any) -> bool:
        authed = await client.is_user_authorized()
        if not authed:
            self._set_state(authorized=False, user_label="")
            return False
        me = await client.get_me()
        label = ""
        if me is not None:
            parts = [getattr(me, "first_name", None) or "", getattr(me, "last_name", None) or ""]
            name = " ".join(p for p in parts if p).strip()
            uname = getattr(me, "username", None)
            label = f"{name} (@{uname})" if uname else (name or str(getattr(me, "id", "")))
        self._set_state(
            authorized=True,
            needs_2fa=False,
            user_label=label,
            qr_url="",
            qr_image="",
            last_error="",
        )
        return True

    async def _auto_connect_and_run(self) -> None:
        try:
            client = await self._ensure_client()
            if await self._refresh_user_info(client):
                self._ensure_event_handler(client)
                self._schedule_sync()
        except Exception as exc:
            LOGGER.warning("MTProto auto-connect warning: %s", exc)
            self._set_state(last_error=str(exc))

    def start_qr_login(self) -> dict[str, Any]:
        return self._submit_coro(self._start_qr_login_async(), wait=True, timeout=25.0)

    async def _start_qr_login_async(self) -> dict[str, Any]:
        client = await self._ensure_client()
        if await self._refresh_user_info(client):
            self._ensure_event_handler(client)
            return self.status()
        if self._qr_wait_task and not self._qr_wait_task.done():
            self._qr_wait_task.cancel()
        qr = await client.qr_login()
        self._qr_login = qr
        svg = _render_qr_svg_data_url(qr.url)
        if not svg:
            raise RuntimeError("Không thể tạo ảnh SVG cho mã QR")
        self._set_state(qr_url=qr.url, qr_image=svg, needs_2fa=False, last_error="")
        self._qr_wait_task = asyncio.create_task(self._wait_qr_task(qr, client))
        return self.status()

    async def _wait_qr_task(self, qr: Any, client: Any) -> None:
        try:
            await qr.wait(timeout=120)
            if await self._refresh_user_info(client):
                self._ensure_event_handler(client)
                if self._translator.cfg.mtproto_enabled:
                    self._schedule_sync()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            msg = str(exc)
            if "SessionPasswordNeeded" in type(exc).__name__ or "Two-steps" in msg or "password" in msg.lower():
                self._set_state(needs_2fa=True, qr_url="", qr_image="", last_error="Tài khoản bật mật khẩu 2 bước (2FA)")
            else:
                self._set_state(qr_url="", qr_image="", last_error=f"QR login hết hạn hoặc lỗi: {msg}")

    def send_phone_code(self, phone: str) -> dict[str, Any]:
        return self._submit_coro(self._send_phone_code_async(phone.strip()), wait=True, timeout=20.0)

    async def _send_phone_code_async(self, phone: str) -> dict[str, Any]:
        if not phone:
            raise RuntimeError("Thiếu số điện thoại")
        client = await self._ensure_client()
        sent = await client.send_code_request(phone)
        self._phone_number = phone
        self._phone_code_hash = getattr(sent, "phone_code_hash", "")
        self._set_state(last_error="", needs_2fa=False)
        return {"ok": True}

    def complete_sign_in(self, code: str = "", password: str = "") -> dict[str, Any]:
        return self._submit_coro(
            self._complete_sign_in_async(code.strip(), password),
            wait=True,
            timeout=20.0,
        )

    async def _complete_sign_in_async(self, code: str, password: str) -> dict[str, Any]:
        client = await self._ensure_client()
        try:
            if self._state.get("needs_2fa") and password:
                await client.sign_in(password=password)
            elif code:
                await client.sign_in(
                    phone=self._phone_number,
                    code=code,
                    phone_code_hash=self._phone_code_hash,
                )
            elif password:
                await client.sign_in(password=password)
            else:
                raise RuntimeError("Cần nhập mã OTP hoặc mật khẩu 2FA")
        except Exception as exc:
            if "SessionPasswordNeeded" in type(exc).__name__:
                self._set_state(needs_2fa=True, last_error="Cần nhập mật khẩu 2 bước (2FA)")
                return self.status()
            self._set_state(last_error=str(exc))
            raise

        if await self._refresh_user_info(client):
            self._ensure_event_handler(client)
            if self._translator.cfg.mtproto_enabled:
                self._schedule_sync()
        return self.status()

    def logout(self) -> dict[str, Any]:
        return self._submit_coro(self._logout_async(), wait=True, timeout=15.0)

    async def _logout_async(self) -> dict[str, Any]:
        await self._stop_sync_only()
        if self._client is not None:
            try:
                await self._client.log_out()
            except Exception:
                pass
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._new_handler_registered = False
        self._set_state(authorized=False, connected=False, user_label="", qr_url="", qr_image="", needs_2fa=False)
        return self.status()

    def list_dialogs(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._submit_coro(self._list_dialogs_async(limit), wait=True, timeout=25.0)

    async def _list_dialogs_async(self, limit: int = 100) -> list[dict[str, Any]]:
        client = await self._ensure_client()
        if not await client.is_user_authorized():
            raise RuntimeError("Chưa đăng nhập Telegram MTProto")
        selected = {int(c["id"]): bool(c.get("enabled", True)) for c in self._translator.cfg.mtproto_chats}
        dialogs: list[dict[str, Any]] = []
        async for d in client.iter_dialogs(limit=limit):
            if not (getattr(d, "is_group", False) or getattr(d, "is_channel", False)):
                continue
            cid = int(d.id)
            dialogs.append(
                {
                    "id": cid,
                    "title": str(getattr(d, "title", None) or getattr(d, "name", None) or cid),
                    "is_channel": bool(getattr(d, "is_channel", False) and not getattr(d, "is_group", False)),
                    "enabled": selected.get(cid, False),
                }
            )
        return dialogs

    def trigger_sync_now(self) -> dict[str, Any]:
        self._submit_coro(self._trigger_sync_async(), wait=True, timeout=10.0)
        return self.status()

    async def _trigger_sync_async(self) -> None:
        client = await self._ensure_client()
        if not await client.is_user_authorized():
            raise RuntimeError("Chưa đăng nhập Telegram MTProto")
        self._ensure_event_handler(client)
        self._schedule_sync()

    def _schedule_sync(self) -> None:
        if not self._loop:
            return
        if self._sync_task and not self._sync_task.done():
            return
        self._sync_task = self._loop.create_task(self._sync_all_chats())

    def _ensure_event_handler(self, client: Any) -> None:
        if self._new_handler_registered:
            return
        try:
            from telethon import events
        except ImportError:
            return

        @client.on(events.NewMessage())
        async def _on_new_message(event: Any) -> None:
            cfg = self._translator.cfg
            if not (cfg.mtproto_enabled and cfg.mtproto_listen_new):
                return
            enabled_ids = {int(c["id"]) for c in cfg.mtproto_chats if c.get("enabled", True)}
            chat_id = getattr(event, "chat_id", None)
            if chat_id is None or int(chat_id) not in enabled_ids:
                return
            raw_text = getattr(getattr(event, "message", None), "raw_text", None)
            if not isinstance(raw_text, str) or not raw_text.strip():
                return
            await asyncio.to_thread(self.pretranslate_texts, [raw_text])

        self._new_handler_registered = True
        self._set_state(listening=True)

    def pretranslate_texts(self, raw_texts: list[str]) -> tuple[int, int]:
        """Translate a batch of plain Telegram texts directly into TranslationCache."""
        cfg = self._translator.cfg
        target = self._translator.effective_target_lang(cfg.target_lang)
        provider_ns = self._translator.cache_namespace()

        pending: list[str] = []
        seen: set[str] = set()
        checked = 0
        for raw in raw_texts:
            if not raw:
                continue
            checked += 1
            text, skip = prepare_source_text(raw, target_lang=target)
            if skip or text in seen:
                continue
            if self._translator.cache.get(provider_ns, target, text) is not None:
                continue
            seen.add(text)
            pending.append(text)

        if not pending:
            self._set_state(synced_checked=int(self._state["synced_checked"]) + checked)
            return checked, 0

        batch_size = max(1, cfg.mtproto_batch_size)
        translated_count = 0
        for start in range(0, len(pending), batch_size):
            if self._stop_requested:
                break
            chunk = pending[start : start + batch_size]
            try:
                provider = self._translator.provider
                outputs = provider.translate_batch(chunk, "auto", target)
                for src, dst in zip(chunk, outputs):
                    if dst and dst.strip():
                        self._translator.cache.put(provider_ns, target, src, dst.strip())
                        translated_count += 1
            except ProviderError as exc:
                LOGGER.warning("Batch pretranslate error (%s); retrying smaller sub-batches", exc)
                sub_size = 5
                for sub_start in range(0, len(chunk), sub_size):
                    sub = chunk[sub_start : sub_start + sub_size]
                    try:
                        outputs = self._translator.provider.translate_batch(sub, "auto", target)
                        for src, dst in zip(sub, outputs):
                            if dst and dst.strip():
                                self._translator.cache.put(provider_ns, target, src, dst.strip())
                                translated_count += 1
                    except Exception:
                        for src in sub:
                            try:
                                self._translator.translate_text(src, "auto", target)
                                translated_count += 1
                            except Exception:
                                pass
            except Exception as exc:
                LOGGER.warning("Unexpected pretranslate error: %s", exc)
                self._set_state(last_error=str(exc))

        with self._lock:
            self._state["synced_checked"] = int(self._state["synced_checked"]) + checked
            self._state["synced_translated"] = int(self._state["synced_translated"]) + translated_count
        return checked, translated_count

    async def _sync_all_chats(self) -> None:
        client = self._client
        if client is None:
            return
        cfg = self._translator.cfg
        chats = [c for c in cfg.mtproto_chats if c.get("enabled", True)]
        if not chats:
            self._set_state(syncing=False, current_chat="")
            return

        self._set_state(syncing=True, last_error="", listening=bool(cfg.mtproto_listen_new))
        try:
            for chat in chats:
                if self._stop_requested:
                    break
                cid = int(chat["id"])
                title = str(chat.get("title") or cid)
                self._set_state(current_chat=title)
                collected: list[str] = []
                try:
                    async for msg in client.iter_messages(cid, limit=cfg.mtproto_prefetch_limit):
                        if self._stop_requested:
                            break
                        txt = getattr(msg, "raw_text", None)
                        if isinstance(txt, str) and txt.strip():
                            collected.append(txt)
                    if collected:
                        await asyncio.to_thread(self.pretranslate_texts, collected)
                except Exception as exc:
                    if "FloodWait" in type(exc).__name__:
                        wait_s = int(getattr(exc, "seconds", 5)) + 1
                        LOGGER.warning("Telegram FloodWait %ss during prefetch", wait_s)
                        await asyncio.sleep(wait_s)
                    else:
                        LOGGER.warning("Failed prefetching chat %s: %s", title, exc)
                        self._set_state(last_error=f"{title}: {exc}")
            self._set_state(last_sync_at=time.time())
        finally:
            self._set_state(syncing=False, current_chat="")
