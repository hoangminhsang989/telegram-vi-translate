# Telegram PC Vietnamese Auto-Translate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tự động dịch toàn bộ tin nhắn tiếng Trung trong nhóm Telegram Desktop (Windows 11) sang tiếng Việt ngay trên nội dung tin nhắn với 1 nút bật/tắt (`TranslateBar`), sử dụng dịch vụ dịch cục bộ + cache SQLite và không chỉnh sửa tệp `Telegram.exe` gốc trên đĩa.

**Architecture:** `Telegram.exe` nạp `mscms.dll` proxy (viết bằng Rust) đặt cùng thư mục để mở khóa riêng cổng `AmPremiumValue` bên trong `HistoryView::TranslateTracker::setup()` bằng dò tìm RTTI/AOB động trong RAM (không giả mạo Premium toàn cục, tự bỏ qua an toàn nếu không khớp chữ ký). Song song đó, `tdata/experimental_options.json` trỏ `"translate-url-template"` về HTTP proxy cục bộ chạy bằng Python 3.14 (`127.0.0.1:18765`), nơi xử lý HTML-unescape, khóa ngôn ngữ đích `vi`, single-flight deduplication, cache SQLite và dịch qua Google Translate miễn phí (mặc định) hoặc AI API tùy chọn.

**Tech Stack:** Python 3.14 (stdlib `http.server`, `sqlite3`, `urllib`, `html`, `unittest`), Rust 1.99.1 (`x86_64-pc-windows-msvc`, `cdylib`, Win32 API FFI tối thiểu không kéo dependency nặng), PowerShell (`scripts/`).

**Spec:** [2026-11-25-telegram-pc-vi-auto-translate-design.md](file:///e:/telegram/docs/superpowers/specs/2026-11-25-telegram-pc-vi-auto-translate-design.md)

## Global Constraints

- Mục tiêu hệ thống: Windows 11, `C:\Users\HMS-PCC\AppData\Roaming\Telegram Desktop\Telegram.exe` (v7.4.6.0, Qt 6.12.0).
- Không sửa trực tiếp nhị phân `Telegram.exe` trên đĩa; mọi thay đổi phải gỡ sạch bằng `uninstall.ps1`.
- Không giả mạo trạng thái Telegram Premium toàn cục; chỉ mở khóa nhánh `TranslateTracker::setup()`.
- `UrlTranslateProvider` của `tdesktop` gọi `TextUtilities::EscapeForHtml` trước khi gửi `%q` và ưu tiên định dạng JSON `[[["..."]]]` qua `ParseSegmentedArrayResponse`.
- Ngôn ngữ đích mặc định luôn ép về `"vi"` khi `force_target_vi: true` (vì UI Telegram đang để tiếng Anh).
- Không dùng thư viện bên thứ ba nặng khi Python stdlib / Rust core Win32 FFI đáp ứng đủ.

## Review Focus

1. **HTML-escaped & JSON-special payloads (`&quot;`, `&amp;`, `{`, `[`)**: Tin nhắn chứa HTML entities do `EscapeForHtml` sinh ra hoặc mở đầu bằng `{`/`[` phải được `html.unescape` trước khi dịch và bọc trong `[[["<bản dịch>"]]]` để `FormatJsonResponse` của `Telegram.exe` không parse nhầm thành key-value JSON.
2. **English UI target override & passthrough strings**: Khi `Telegram.exe` gửi `to=en` (do UI tiếng Anh), server phải ép dịch sang `vi` khi `force_target_vi=true`; chuỗi rỗng, chỉ chứa số/URL/ký tự điều khiển hoặc đã là tiếng Việt không bị biến dạng hay lãng phí lượt gọi upstream.
3. **20-request burst & duplicate in-flight strings**: Khi `TranslateTracker::requestSome` bắn đồng thời tối đa 20 HTTP GET giống hoặc khác nhau, single-flight coalescing phải giữ chỉ 1 upstream call cho chuỗi trùng và không làm lỗi `sqlite3` locked.
4. **Upstream failure / HTTP 429 / timeout safety**: Khi Google/AI lỗi mạng hoặc timeout, `GET /translate` phải trả HTTP `502` và tuyệt đối không ghi chuỗi lỗi vào `cache.db`, để `TranslateTracker` giữ nguyên tin gốc thay vì hiển thị thông báo lỗi lên khung chat.
5. **Proxy `mscms.dll` non-recursion & safe signature mismatch**: Khi `mscms.dll` đặt cạnh `Telegram.exe`, quá trình chuyển tiếp 123 export phải load tường minh `C:\Windows\System32\mscms.dll` (không gọi `LoadLibraryW("mscms.dll")` gây đệ quy vô hạn), và nếu byte AOB không khớp phiên bản `Telegram.exe` thì ghi log cảnh báo và bỏ qua patch không crash.

---

### Task 1: Config và SQLite LRU Cache (`translator_proxy`)

**Files:**
- Create: `e:\telegram\translator_proxy\__init__.py`
- Create: `e:\telegram\translator_proxy\config.py`
- Create: `e:\telegram\translator_proxy\config.default.json`
- Create: `e:\telegram\translator_proxy\cache.py`
- Test: `e:\telegram\tests\test_cache.py`

**Interfaces:**
- Consumes: Python 3.14 stdlib (`dataclasses`, `json`, `pathlib`, `sqlite3`, `hashlib`, `collections.OrderedDict`, `threading`).
- Produces:
  - `TranslationConfig` dataclass (`host: str`, `port: int`, `provider: str`, `force_target_vi: bool`, `target_lang: str`, `cache_db: Path`, `max_memory_items: int`, `timeout_sec: float`, `ai_base_url: str`, `ai_api_key: str`, `ai_model: str`, `glossary: dict[str, str]`)
  - `load_config(path: Path | None) -> TranslationConfig`
  - `TranslationCache(db_path: Path, max_memory_items: int = 4096)` với `get(provider: str, target_lang: str, text: str) -> str | None`, `put(provider: str, target_lang: str, text: str, translated: str) -> None`, `count() -> int`, `close() -> None`

- [ ] **Step 1: Write failing tests for config loading and cache round-trip/LRU/concurrency**

```python
# tests/test_cache.py
import tempfile, threading, unittest
from pathlib import Path
from translator_proxy.config import TranslationConfig, load_config
from translator_proxy.cache import TranslationCache

class TestConfigAndCache(unittest.TestCase):
    def test_load_config_defaults_and_override(self):
        with tempfile.TemporaryDirectory() as d:
            cfg_path = Path(d) / "config.json"
            cfg_path.write_text('{"port": 19000, "force_target_vi": false}', encoding="utf-8")
            cfg = load_config(cfg_path)
            self.assertEqual(cfg.port, 19000)
            self.assertFalse(cfg.force_target_vi)
            self.assertEqual(cfg.provider, "google_gtx")

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
            c2.close()

    def test_cache_concurrent_writes_do_not_lock(self):
        with tempfile.TemporaryDirectory() as d:
            c = TranslationCache(Path(d) / "cache.db")
            errors = []
            def worker(idx):
                try:
                    for j in range(10):
                        c.put("google_gtx", "vi", f"msg-{idx}-{j}", f"tin-{idx}-{j}")
                except Exception as exc:
                    errors.append(exc)
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads: t.start()
            for t in threads: t.join()
            self.assertEqual(errors, [])
            self.assertEqual(c.count(), 80)
            c.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'translator_proxy'`.

- [ ] **Step 3: Implement `config.py`, `config.default.json`, and `cache.py`**

Tạo `translator_proxy/__init__.py`, `translator_proxy/config.default.json` (glossary mặc định gồm `0元购`, `薅羊毛`, `羊毛`, `脚本`, `上车`), `translator_proxy/config.py` (`TranslationConfig` với giá trị mặc định cho mọi field để có thể khởi tạo một phần trong test, kèm `load_config` hợp nhất JSON với giá trị mặc định), và `translator_proxy/cache.py` (SQLite chế độ `WAL` + `timeout=10.0` + `threading.Lock` bảo vệ cả `OrderedDict` LRU lẫn connection SQLite, key SHA-256 của `(provider, target_lang, text)`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests/test_cache.py -v`
Expected: PASS (3 tests OK).

- [ ] **Step 5: Commit**

```bash
git add translator_proxy/__init__.py translator_proxy/config.py translator_proxy/config.default.json translator_proxy/cache.py tests/test_cache.py
git commit -m "feat: add translation proxy config and thread-safe SQLite LRU cache"
```

---

### Task 2: Bộ dịch đa backend (`GoogleGtxProvider`, `AiTranslateProvider`, HTML & passthrough rules)

**Files:**
- Create: `e:\telegram\translator_proxy\providers.py`
- Test: `e:\telegram\tests\test_providers.py`

**Interfaces:**
- Consumes: `TranslationConfig` từ `translator_proxy.config`.
- Produces:
  - `class ProviderError(RuntimeError)`
  - `prepare_source_text(raw_q: str) -> tuple[str, bool]` (HTML-unescape + kiểm tra passthrough cho chuỗi rỗng/URL-only/số-ký hiệu thuần)
  - `class BaseTranslateProvider`: `translate(text: str, source_lang: str, target_lang: str) -> str`
  - `class GoogleGtxProvider(BaseTranslateProvider)`: `__init__(timeout_sec: float = 8.0, opener=None)`
  - `class AiTranslateProvider(BaseTranslateProvider)`: `__init__(base_url: str, api_key: str, model: str, glossary: dict[str, str], timeout_sec: float = 15.0, opener=None)`
  - `create_provider(cfg: TranslationConfig) -> BaseTranslateProvider`

- [ ] **Step 1: Write failing tests for HTML unescape, passthrough, Google GTX parsing, and AI glossary prompt**

```python
# tests/test_providers.py
import io, json, unittest
from translator_proxy.config import TranslationConfig
from translator_proxy.providers import (
    AiTranslateProvider, GoogleGtxProvider, ProviderError,
    create_provider, prepare_source_text,
)

class FakeResp:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status
    def read(self): return self._body
    def __enter__(self): return self
    def __exit__(self, *a): return False

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

    def test_google_gtx_raises_provider_error_on_empty_or_bad_json(self):
        p = GoogleGtxProvider(opener=lambda req, timeout: FakeResp(b"not-json"))
        with self.assertRaises(ProviderError):
            p.translate("你好", "zh", "vi")

    def test_ai_provider_includes_glossary_and_parses_chat_completion(self):
        captured = {}
        def fake_open(req, timeout):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResp(json.dumps({"choices": [{"message": {"content": "Săn kèo 0 đồng"}}]}).encode("utf-8"))
        p = AiTranslateProvider(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="gpt-4o-mini",
            glossary={"0元购": "săn deal 0 đồng"},
            opener=fake_open,
        )
        out = p.translate("0元购", "zh", "vi")
        self.assertEqual(out, "Săn kèo 0 đồng")
        sys_msg = captured["body"]["messages"][0]["content"]
        self.assertIn("0元购", sys_msg)
        self.assertIn("săn deal 0 đồng", sys_msg)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests/test_providers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'translator_proxy.providers'`.

- [ ] **Step 3: Implement `translator_proxy/providers.py`**

Cài `prepare_source_text` (`html.unescape` + regex nhận diện chuỗi chỉ gồm whitespace/URL/`@mention`/số/dấu câu), `GoogleGtxProvider` (gọi `https://translate.googleapis.com/translate_a/single?client=gtx&sl=...&tl=...&dt=t&q=...`, chia chunk <= 1800 ký tự theo xuống dòng nếu tin quá dài, ghép các segment `root[0][i][0]`, ném `ProviderError` khi lỗi HTTP/JSON), và `AiTranslateProvider` (POST `/chat/completions`, nhúng glossary vào system prompt, giữ nguyên link/`@username`/mã code).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests/test_providers.py -v`
Expected: PASS (4 tests OK).

- [ ] **Step 5: Commit**

```bash
git add translator_proxy/providers.py tests/test_providers.py
git commit -m "feat: implement Google GTX and AI translation providers with HTML unescaping"
```

---

### Task 3: HTTP Server với Single-Flight Deduplication và CLI Entry Point (`translator_proxy`)

**Files:**
- Create: `e:\telegram\translator_proxy\server.py`
- Create: `e:\telegram\translator_proxy\__main__.py`
- Test: `e:\telegram\tests\test_server.py`

**Interfaces:**
- Consumes: `TranslationConfig`, `load_config` (`translator_proxy.config`), `TranslationCache` (`translator_proxy.cache`), `BaseTranslateProvider`, `ProviderError`, `prepare_source_text`, `create_provider` (`translator_proxy.providers`).
- Produces:
  - `SingleFlightTranslator(cache: TranslationCache, provider: BaseTranslateProvider, cfg: TranslationConfig)` với `translate_text(raw_q: str, source_lang: str, target_lang: str) -> str`
  - `create_http_server(cfg: TranslationConfig, translator: SingleFlightTranslator | None = None) -> ThreadingHTTPServer`
  - Endpoints:
    - `GET /health` -> `200 {"ok": true, "provider": "...", "target_lang": "vi", "cached": N}`
    - `GET /translate?from=...&to=...&q=...` -> `200 [[["<translated>"]]]` hoặc `502 {"error": "..."}` nếu upstream lỗi

- [ ] **Step 1: Write failing tests covering Review Focus #1, #2, #3, #4 over real HTTP**

```python
# tests/test_server.py
import json, tempfile, threading, time, unittest, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from translator_proxy.cache import TranslationCache
from translator_proxy.config import TranslationConfig
from translator_proxy.providers import BaseTranslateProvider, ProviderError
from translator_proxy.server import SingleFlightTranslator, create_http_server

class StubProvider(BaseTranslateProvider):
    def __init__(self, fail=False, delay=0.0):
        self.calls = 0
        self.fail = fail
        self.delay = delay
        self.last_target = None
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
    def _start(self, provider):
        d = tempfile.TemporaryDirectory()
        cfg = TranslationConfig(host="127.0.0.1", port=0, force_target_vi=True, target_lang="vi", cache_db=Path(d.name)/"c.db")
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
            srv.shutdown(); srv.server_close(); cache.close(); d.cleanup()

    def test_burst_20_duplicate_requests_hit_upstream_once(self):
        prov = StubProvider(delay=0.05)
        d, cache, srv = self._start(prov)
        try:
            results = []
            def req():
                url = f"http://127.0.0.1:{srv.server_port}/translate?from=zh&to=en&q=" + urllib.parse.quote("咪 哈基")
                with urllib.request.urlopen(url, timeout=5) as r:
                    results.append(json.loads(r.read().decode("utf-8"))[0][0][0])
            ts = [threading.Thread(target=req) for _ in range(20)]
            for t in ts: t.start()
            for t in ts: t.join()
            self.assertEqual(len(results), 20)
            self.assertEqual(prov.calls, 1)
            self.assertEqual(cache.count(), 1)
        finally:
            srv.shutdown(); srv.server_close(); cache.close(); d.cleanup()

    def test_upstream_failure_returns_502_and_does_not_poison_cache(self):
        prov = StubProvider(fail=True)
        d, cache, srv = self._start(prov)
        try:
            url = f"http://127.0.0.1:{srv.server_port}/translate?from=zh&to=vi&q=" + urllib.parse.quote("钱")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(url, timeout=5)
            self.assertEqual(ctx.exception.code, 502)
            self.assertEqual(cache.count(), 0)
        finally:
            srv.shutdown(); srv.server_close(); cache.close(); d.cleanup()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'translator_proxy.server'`.

- [ ] **Step 3: Implement `server.py` and `__main__.py`**

Cài `SingleFlightTranslator` (dùng map `dict[tuple[str, str, str], _InFlightSlot]` kèm `threading.Event` để 20 request trùng đợi cùng 1 slot rồi ghi cache chỉ khi thành công), `create_http_server` (`ThreadingHTTPServer` với `daemon_threads = True`, `GET /health` và `GET /translate` trả `[[[translated]]]`), và `__main__.py` đọc `--config` rồi chạy server cho tới `SIGINT`/`SIGTERM`.

- [ ] **Step 4: Run all Python proxy tests**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`
Expected: PASS (all proxy tests OK).

- [ ] **Step 5: Commit**

```bash
git add translator_proxy/server.py translator_proxy/__main__.py tests/test_server.py
git commit -m "feat: add multithreaded localhost translation HTTP server with single-flight cache"
```

---

### Task 4: Bộ phát hiện chữ ký RTTI/AOB `TranslateTracker::setup()` và xác minh trên nhị phân `Telegram.exe` (`shim_mscms`)

**Files:**
- Create: `e:\telegram\shim_mscms\Cargo.toml`
- Create: `e:\telegram\shim_mscms\src\pe_patch.rs`
- Test: `e:\telegram\shim_mscms\tests\pe_scan_test.rs`

**Interfaces:**
- Consumes: `core`/`std` (không cần crate bên ngoài nặng; parse PE header + `.text`/`.rdata`/`.pdata` trực tiếp trên slice `&[u8]`).
- Produces in `shim_mscms::pe_patch`:
  - `pub struct PatchPlan { pub call_site_rva: usize, pub am_premium_rva: usize, pub rpl_single_vtable_rva: usize }`
  - `pub fn find_translate_tracker_patch(image: &[u8]) -> Result<PatchPlan, &'static str>`
  - `pub fn build_rpl_single_true_stub(rpl_single_vtable_va: u64) -> [u8; 22]` (sinh mã máy x64 gán `[rcx] = vtable`, `[rcx+8] = 1`, `[rcx+0x38] = rcx`, `mov rax, rcx; ret`)

- [ ] **Step 1: Write failing Rust tests in `tests/pe_scan_test.rs` (kể cả test đọc `Telegram.exe` thật nếu tồn tại + test buffer giả khi không khớp chữ ký)**

```rust
// shim_mscms/tests/pe_scan_test.rs
use std::path::Path;
use mscms::pe_patch::{build_rpl_single_true_stub, find_translate_tracker_patch};

#[test]
fn rejects_non_matching_or_truncated_pe_safely() {
    let garbage = vec![0u8; 4096];
    assert!(find_translate_tracker_patch(&garbage).is_err());
}

#[test]
fn stub_encodes_vtable_and_true_flag() {
    let stub = build_rpl_single_true_stub(0x14932eca0);
    assert_eq!(&stub[0..2], &[0x48, 0xB8]); // mov rax, imm64
    assert_eq!(u64::from_le_bytes(stub[2..10].try_into().unwrap()), 0x14932eca0);
    assert_eq!(stub[stub.len() - 1], 0xC3); // ret
}

#[test]
fn locates_translate_tracker_call_in_installed_telegram_exe() {
    let tg = Path::new(r"C:\Users\HMS-PCC\AppData\Roaming\Telegram Desktop\Telegram.exe");
    if !tg.exists() { return; }
    let bytes = std::fs::read(tg).unwrap();
    let plan = find_translate_tracker_patch(&bytes).expect("must find TranslateTracker::setup gate");
    assert!(plan.call_site_rva > 0x1000);
    assert!(plan.rpl_single_vtable_rva > 0x1000);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --manifest-path shim_mscms/Cargo.toml`
Expected: FAIL (Cargo.toml / `pe_patch` chưa tồn tại).

- [ ] **Step 3: Implement `Cargo.toml` (`crate-type = ["cdylib", "rlib"]`) and `src/pe_patch.rs`**

Cài `find_translate_tracker_patch(image: &[u8])`:
1. Đọc PE64 sections (`.text`, `.rdata`, `.pdata`), hỗ trợ cả file chưa map (`raw_offset`) lẫn module đã map trong RAM (`va_offset`, phân biệt bằng tham số hoặc `find_translate_tracker_patch_mapped` dùng `rva` trực tiếp).
2. Quét `.rdata` tìm `TypeDescriptor` chứa `setup@TranslateTracker@HistoryView@@` -> tìm `CompleteObjectLocator` -> tìm `vftable` -> quét `.text` tìm `LEA` tham chiếu `vftable` -> tra `.pdata` lấy phạm vi hàm `TranslateTracker::setup()`.
3. Trong phạm vi hàm `TranslateTracker::setup()`, quét mẫu AOB quanh lệnh gọi `Data::AmPremiumValue`: `48 8B 52 60 48 85 D2 0F 84 ?? ?? ?? ?? 48 8D 8D ?? ?? 00 00 E8 ?? ?? ?? ?? 90 48 8B 05 ?? ?? ?? ?? 48 85 C0`.
4. Theo đích `E8 rel32` vào `Data::AmPremiumValue`, tìm nhánh `rpl::single<bool>(false)`: `48 85 FF 75 ?? 48 8D 05 ?? ?? ?? ?? 48 89 01 44 88 71 08 48 89 49 38` để trích `rpl_single_vtable_rva`.

- [ ] **Step 4: Run `cargo test` to verify scanner matches `Telegram.exe 7.4.6` dynamically**

Run: `cargo test --manifest-path shim_mscms/Cargo.toml -- --nocapture`
Expected: PASS (3 tests OK; `locates_translate_tracker_call_in_installed_telegram_exe` khớp chính xác).

- [ ] **Step 5: Commit**

```bash
git add shim_mscms/Cargo.toml shim_mscms/src/pe_patch.rs shim_mscms/tests/pe_scan_test.rs
git commit -m "feat: add dynamic RTTI and AOB locator for TranslateTracker::setup"
```

---

### Task 5: `mscms.dll` Proxy Forwarding & In-Memory Patch (`shim_mscms`)

**Files:**
- Create: `e:\telegram\shim_mscms\build.rs`
- Create: `e:\telegram\shim_mscms\src\lib.rs`
- Test: `e:\telegram\shim_mscms\tests\proxy_exports_test.rs`

**Interfaces:**
- Consumes: `pe_patch::find_translate_tracker_patch_in_module` & `pe_patch::build_rpl_single_true_stub` (`shim_mscms/src/pe_patch.rs`), danh sách đúng 123 named exports từ `C:\Windows\System32\mscms.dll`.
- Produces: `e:\telegram\shim_mscms\target\release\mscms.dll` (xuất đủ 123 hàm của `mscms.dll`, tự nạp `C:\Windows\System32\mscms.dll` qua `LoadLibraryW` đường dẫn tuyệt đối + jump-slot trampoline bằng `core::arch::global_asm!`, và thực thi patch 5-byte `E8` một lần duy nhất trong `DLL_PROCESS_ATTACH` với `VirtualAlloc` trong vùng ±1GB quanh `Telegram.exe` + `VirtualProtect` + `FlushInstructionCache` + ghi log `%LOCALAPPDATA%\TelegramViShim\shim.log`).

- [ ] **Step 1: Write failing test verifying the built DLL exposes all 123 System32 `mscms.dll` exports and links no circular `mscms.dll` self-import**

```rust
// shim_mscms/tests/proxy_exports_test.rs
use std::collections::BTreeSet;
use std::path::Path;
use mscms::pe_patch::read_export_names;

#[test]
fn built_shim_exports_match_system32_mscms_123_functions() {
    let sys_dll = Path::new(r"C:\Windows\System32\mscms.dll");
    let built_dll = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/release/mscms.dll");
    if !built_dll.exists() {
        panic!("Run cargo build --release first");
    }
    let sys_exports: BTreeSet<String> = read_export_names(&std::fs::read(sys_dll).unwrap()).into_iter().collect();
    let shim_exports: BTreeSet<String> = read_export_names(&std::fs::read(&built_dll).unwrap()).into_iter().collect();
    assert_eq!(sys_exports.len(), 123);
    assert_eq!(sys_exports, shim_exports);
}
```

- [ ] **Step 2: Run release build / test to verify it fails**

Run: `cargo test --manifest-path shim_mscms/Cargo.toml --test proxy_exports_test`
Expected: FAIL (chưa có `read_export_names`, `build.rs`, `src/lib.rs`, `target/release/mscms.dll`).

- [ ] **Step 3: Implement `build.rs`, `read_export_names` in `pe_patch.rs`, and `src/lib.rs`**

- `build.rs`: đọc bảng export của `C:\Windows\System32\mscms.dll` (123 tên hàm), sinh file `OUT_DIR/mscms_forwards.rs` gồm bảng `[AtomicUsize; 123]` con trỏ hàm, mảng tên ASCII kết thúc `\0`, và 123 hàm `#[unsafe(naked)] pub unsafe extern "system" fn <Name>()` nhảy gián tiếp `jmp qword ptr [rip + SLOT_i]` (hoặc gọi helper resolver nếu slot = 0, bảo toàn toàn bộ tham số thanh ghi/stack `rcx, rdx, r8, r9`). Đặt tên package `[lib] name = "mscms"` trong `Cargo.toml`.
- `src/lib.rs`: `DllMain` khi `DLL_PROCESS_ATTACH` gọi `DisableThreadLibraryCalls`, nạp `C:\Windows\System32\mscms.dll` (tuyệt đối), điền bảng 123 con trỏ hàm trước khi trả về `TRUE`, kiểm tra tên tiến trình là `Telegram.exe` rồi khởi chạy worker thread an toàn (`CreateThread`) quét bộ nhớ `GetModuleHandleW(null)` bằng `find_translate_tracker_patch_in_module` -> cấp phát trampoline executable gần `base` -> vá `call` 5 byte -> ghi log.

- [ ] **Step 4: Build release `mscms.dll` and run all Rust tests**

Run: `cargo build --release --manifest-path shim_mscms/Cargo.toml && cargo test --manifest-path shim_mscms/Cargo.toml`
Expected: PASS (0 errors, `mscms.dll` export khớp đúng 123/123 hàm của `System32\mscms.dll`).

- [ ] **Step 5: Commit**

```bash
git add shim_mscms/build.rs shim_mscms/src/lib.rs shim_mscms/src/pe_patch.rs shim_mscms/Cargo.toml shim_mscms/tests/proxy_exports_test.rs
git commit -m "feat: build mscms.dll forwarding proxy with runtime TranslateTracker patch"
```

---

### Task 6: Cấu hình `experimental_options.json` và Bộ script vận hành Windows (`scripts/`)

**Files:**
- Create: `e:\telegram\scripts\config_telegram.py`
- Create: `e:\telegram\scripts\install.ps1`
- Create: `e:\telegram\scripts\start.ps1`
- Create: `e:\telegram\scripts\stop.ps1`
- Create: `e:\telegram\scripts\status.ps1`
- Create: `e:\telegram\scripts\uninstall.ps1`
- Test: `e:\telegram\tests\test_config_telegram.py`

**Interfaces:**
- Consumes: đường dẫn `tdata` (`C:\Users\HMS-PCC\AppData\Roaming\Telegram Desktop\tdata`), `shim_mscms/target/release/mscms.dll`, `translator_proxy`.
- Produces:
  - `merge_experimental_options(tdata_dir: Path, template_url: str) -> Path` (tạo backup `.bak` nếu có file gốc và chưa có backup, giữ nguyên các key khác, ghi `"translate-url-template"`)
  - `restore_experimental_options(tdata_dir: Path) -> None` (khôi phục từ `.bak` hoặc xóa key `"translate-url-template"` nếu trước đó không có file)

- [ ] **Step 1: Write failing tests for safe JSON merge and restore**

```python
# tests/test_config_telegram.py
import json, tempfile, unittest
from pathlib import Path
from scripts.config_telegram import merge_experimental_options, restore_experimental_options

class TestConfigTelegram(unittest.TestCase):
    def test_merge_preserves_existing_keys_and_restore_reverts_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            tdata = Path(d)
            opt = tdata / "experimental_options.json"
            opt.write_text('{"foo": true}', encoding="utf-8")
            merge_experimental_options(tdata, "http://127.0.0.1:18765/translate?from=%f&to=%t&q=%q")
            data = json.loads(opt.read_text(encoding="utf-8"))
            self.assertTrue(data["foo"])
            self.assertIn("18765", data["translate-url-template"])
            restore_experimental_options(tdata)
            self.assertEqual(json.loads(opt.read_text(encoding="utf-8")), {"foo": True})

    def test_restore_removes_file_when_none_existed_before(self):
        with tempfile.TemporaryDirectory() as d:
            tdata = Path(d)
            merge_experimental_options(tdata, "http://127.0.0.1:18765/translate?from=%f&to=%t&q=%q")
            self.assertTrue((tdata / "experimental_options.json").exists())
            restore_experimental_options(tdata)
            self.assertFalse((tdata / "experimental_options.json").exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests/test_config_telegram.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.config_telegram'`.

- [ ] **Step 3: Implement `scripts/config_telegram.py` and the 5 PowerShell scripts**

- `config_telegram.py`: thực hiện merge/restore an toàn kèm CLI `--install` / `--uninstall` / `--tdata`.
- `install.ps1`: build `mscms.dll` (nếu chưa có), copy `mscms.dll` sang `C:\Users\HMS-PCC\AppData\Roaming\Telegram Desktop\mscms.dll`, chạy `config_telegram.py --install`, tạo `translator_proxy\config.json` từ `config.default.json` nếu chưa có.
- `start.ps1`: kiểm tra `/health` trên `127.0.0.1:18765`; nếu chưa chạy thì khởi động `python -m translator_proxy` ẩn nền (`Start-Process -WindowStyle Hidden`) và chờ `/health` OK; sau đó kiểm tra nếu `Telegram.exe` đang chạy bản cũ chưa nạp `mscms.dll` thì thông báo rõ (hoặc khởi động lại nếu truyền `-RestartTelegram`), rồi mở `Telegram.exe -noupdate`.
- `stop.ps1` / `status.ps1` / `uninstall.ps1`: dừng proxy nền, xem log `shim.log` + `/health` + trạng thái DLL trong `Telegram.exe`, hoặc gỡ sạch `mscms.dll` và khôi phục `experimental_options.json`.

- [ ] **Step 4: Run full automated test suite (Python + Rust) and PowerShell syntax check**

Run: `python -m unittest discover -s tests -p "test_*.py" -v && cargo test --manifest-path shim_mscms/Cargo.toml && powershell -NoProfile -Command "foreach ($s in Get-ChildItem scripts/*.ps1) { [void][System.Management.Automation.Language.Parser]::ParseFile($s.FullName, [ref]$null, [ref]$null) }"`
Expected: PASS toàn bộ test Python, test Rust, và 0 lỗi cú pháp PowerShell.

- [ ] **Step 5: End-to-End Smoke Verification với `Telegram.exe` thật (trên nhóm `AI 羊毛交流群`)**

- Chạy `.\scripts\install.ps1` và `.\scripts\start.ps1 -RestartTelegram`.
- Kiểm tra `status.ps1`: proxy `/health` trả `200 OK`, `%LOCALAPPDATA%\TelegramViShim\shim.log` báo patch thành công (không lỗi).
- Mở nhóm `AI 羊毛交流群` trên Telegram PC: xác nhận thanh `Translate to Vietnamese` / `Translate` xuất hiện ở đầu khung chat -> bấm bật để xác nhận tin nhắn tiếng Trung đổi sang tiếng Việt ngay trong bong bóng chat -> bấm lại để xác nhận hoàn nguyên về tiếng Trung gốc.

- [ ] **Step 6: Commit**

```bash
git add scripts/ tests/test_config_telegram.py
git commit -m "feat: add experimental_options manager and lifecycle PowerShell scripts"
```
