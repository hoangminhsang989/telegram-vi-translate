from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from translator_proxy.config import TranslationConfig

_URL_ONLY_RE = re.compile(r"^\s*(?:https?://\S+|t\.me/\S+)\s*$", re.IGNORECASE)
_MENTION_OR_SYMBOL_ONLY_RE = re.compile(r"^[\s\d\W_]+$|^\s*@[A-Za-z0-9_]+\s*$")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_VIETNAMESE_DIACRITICS_RE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
    r"ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ]"
)
_PROTECTED_SPAN_RE = re.compile(
    r"`[^`\n]+`|https?://[^\s<>\]\"']+|t\.me/[A-Za-z0-9_/+?=&#.-]+|(?<![A-Za-z0-9_])@[A-Za-z0-9_]{2,}",
    re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(r"⟦\s*(\d+)\s*⟧")
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)

_CHINESE_COMPOUND_EXPANSIONS: tuple[tuple[str, str], ...] = (
    ("土区卡", "土耳其银行卡"),
    ("土卡", "土耳其银行卡"),
    ("土区", "土耳其区"),
    ("美区", "美国区"),
    ("日区", "日本区"),
    ("尼区", "尼日利亚区"),
    ("印区", "印度区"),
    ("巴区", "巴西区"),
    ("港区", "香港区"),
    ("台区", "台湾区"),
    ("美卡", "美国银行卡"),
    ("实卡", "实体银行卡"),
    ("虚拟卡", "虚拟银行卡"),
    ("人机号", "机器批量账号"),
    ("老号", "老账号"),
    ("新号", "新账号"),
    ("封号", "封禁账号"),
    ("云机", "云手机"),
    ("双向", "双向限制"),
    ("号死了", "账号挂了"),
    ("全死了", "全挂了"),
    ("都死了", "都挂了"),
)

_CHINESE_EXACT_SLANG_TOKENS: tuple[tuple[str, str], ...] = (
    ("0元购", "săn deal 0đ"),
    ("0 元购", "săn deal 0đ"),
    ("零元购", "săn deal 0đ"),
    ("薅羊毛", "săn kèo hời"),
    ("反重力", "Antigravity"),
    ("喜提封号", "dính ngay khóa tài khoản"),
    ("掉订阅", "mất gói đăng ký"),
    ("拉不起来", "không bật được thanh toán"),
    ("一折", "giảm 90% (giá 10%)"),
    ("1折", "giảm 90% (giá 10%)"),
    ("二折", "giảm 80% (giá 20%)"),
    ("2折", "giảm 80% (giá 20%)"),
    ("五折", "giảm 50%"),
    ("5折", "giảm 50%"),
    ("白嫖", "dùng miễn phí"),
    ("翻车", "toang kèo"),
)

_ACCOUNT_DEATH_CONTEXT_RE = re.compile(r"(?:号|账号|开|封|区|卡|订阅|批|邮箱|新|老|死)")
_STANDALONE_DEAD_MAP = {
    "死了": "bị die / khóa rồi",
    "死了吗": "bị die chưa?",
    "死了没": "bị die chưa?",
    "私": "nhắn riêng (ib)",
}

Opener = Callable[[urllib.request.Request, float], object]


class ProviderError(RuntimeError):
    """Raised when an upstream translation provider fails or returns invalid data."""


def prepare_source_text(raw_q: str, target_lang: str = "vi") -> tuple[str, bool]:
    """Unescape Telegram's HTML-escaped query and decide whether to skip upstream translation."""
    text = html.unescape(raw_q)
    stripped = text.strip()
    if not stripped:
        return text, True
    if _URL_ONLY_RE.match(stripped) or _MENTION_OR_SYMBOL_ONLY_RE.match(stripped):
        return text, True
    if (
        target_lang.lower().startswith("vi")
        and _VIETNAMESE_DIACRITICS_RE.search(stripped)
        and not _CJK_RE.search(stripped)
    ):
        return text, True
    return text, False


def _split_text_chunks(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if len(line) > max_chars:
            if current:
                chunks.append("".join(current))
                current = []
                current_len = 0
            for start in range(0, len(line), max_chars):
                chunks.append(line[start : start + max_chars])
            continue
        if current_len + len(line) > max_chars and current:
            chunks.append("".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


def _protect_spans_and_slang(
    text: str,
    slang_fixups: bool,
    target_lang: str,
) -> tuple[str, list[str]]:
    tokens: list[str] = []

    def _stash(value: str) -> str:
        idx = len(tokens)
        tokens.append(value)
        return f"⟦{idx}⟧"

    protected = _PROTECTED_SPAN_RE.sub(lambda m: _stash(m.group(0)), text)

    if slang_fixups and target_lang.lower().startswith("vi") and _CJK_RE.search(protected):
        for src, replacement in _CHINESE_COMPOUND_EXPANSIONS:
            if src in protected:
                protected = protected.replace(src, replacement)

        if "死了" in protected and _ACCOUNT_DEATH_CONTEXT_RE.search(protected):
            protected = protected.replace("死了", _stash("bị die / khóa"))

        for src, vi_phrase in _CHINESE_EXACT_SLANG_TOKENS:
            if src in protected:
                protected = protected.replace(src, f" {_stash(vi_phrase)} ").replace("  ", " ")

    return protected, tokens


def _restore_tokens(text: str, tokens: list[str]) -> str:
    if not tokens:
        return text

    def _replace(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        if 0 <= idx < len(tokens):
            return tokens[idx]
        return match.group(0)

    restored = _PLACEHOLDER_RE.sub(_replace, text)
    return re.sub(r"\s{2,}", " ", restored).strip() if "  " in restored else restored


class BaseTranslateProvider:
    """Common interface for translation providers."""

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        raise NotImplementedError

    def translate_batch(self, texts: list[str], source_lang: str, target_lang: str) -> list[str]:
        """Translate a list of messages; default implementation translates sequentially."""
        return [self.translate(t, source_lang, target_lang) for t in texts]


class GoogleGtxProvider(BaseTranslateProvider):
    """Free Google Translate gtx endpoint provider with span protection and slang normalization."""

    _ENDPOINT = "https://translate.googleapis.com/translate_a/single"

    def __init__(
        self,
        timeout_sec: float = 8.0,
        opener: Opener | None = None,
        max_chunk_chars: int = 1800,
        slang_fixups: bool = True,
        retries: int = 1,
    ) -> None:
        self._timeout_sec = timeout_sec
        self._opener = opener or urllib.request.urlopen
        self._max_chunk_chars = max_chunk_chars
        self._slang_fixups = slang_fixups
        self._retries = max(0, retries)

    def _request_gtx(self, chunk: str, source_lang: str, target_lang: str) -> str:
        sl = source_lang if source_lang else "auto"
        params = urllib.parse.urlencode(
            [
                ("client", "gtx"),
                ("sl", sl),
                ("tl", target_lang),
                ("dt", "t"),
                ("q", chunk),
            ]
        )
        req = urllib.request.Request(
            f"{self._ENDPOINT}?{params}",
            headers={"User-Agent": "Mozilla/5.0"},
            method="GET",
        )
        last_exc: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                with self._opener(req, timeout=self._timeout_sec) as resp:
                    raw = resp.read()
                last_exc = None
                break
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= self._retries:
                    raise ProviderError(f"Google GTX HTTP {exc.code}: {exc.reason}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt >= self._retries:
                    raise ProviderError(f"Google GTX request failed: {exc}") from exc
            time.sleep(0.25 * (attempt + 1))

        if last_exc is not None:
            raise ProviderError(f"Google GTX request failed: {last_exc}") from last_exc

        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError(f"Invalid Google GTX JSON response: {exc}") from exc

        if not isinstance(parsed, list) or not parsed or not isinstance(parsed[0], list):
            raise ProviderError("Unexpected Google GTX payload shape")

        parts: list[str] = []
        for seg in parsed[0]:
            if isinstance(seg, list) and seg and isinstance(seg[0], str):
                parts.append(seg[0])
        joined = "".join(parts)
        if not joined.strip():
            raise ProviderError("Google GTX returned empty translation")
        return joined

    def _translate_chunk(self, chunk: str, source_lang: str, target_lang: str) -> str:
        if self._slang_fixups and target_lang.lower().startswith("vi"):
            standalone = _STANDALONE_DEAD_MAP.get(chunk.strip())
            if standalone is not None:
                return standalone

        prepared, tokens = _protect_spans_and_slang(chunk, self._slang_fixups, target_lang)
        if prepared.strip() and not _PLACEHOLDER_RE.sub("", prepared).strip():
            return _restore_tokens(prepared, tokens)

        translated = self._request_gtx(prepared, source_lang, target_lang)
        return _restore_tokens(translated, tokens)

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        chunks = _split_text_chunks(text, self._max_chunk_chars)
        out: list[str] = []
        for chunk in chunks:
            if not chunk.strip():
                out.append(chunk)
                continue
            trailing_nl = len(chunk) - len(chunk.rstrip("\n"))
            body = chunk.rstrip("\n")
            translated = self._translate_chunk(body, source_lang, target_lang)
            if trailing_nl and not translated.endswith("\n"):
                translated = translated + ("\n" * trailing_nl)
            out.append(translated)
        return "".join(out)


def _extract_sse_content(raw_text: str) -> str | None:
    parts: list[str] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data_part = line[5:].strip()
        if not data_part or data_part == "[DONE]":
            continue
        try:
            obj = json.loads(data_part)
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices")
        if isinstance(choices, list) and choices:
            delta = choices[0].get("delta") or choices[0].get("message") or {}
            piece = delta.get("content")
            if isinstance(piece, str):
                parts.append(piece)
    joined = "".join(parts).strip()
    return joined or None


class AiTranslateProvider(BaseTranslateProvider):
    """OpenAI-compatible & Gemini-native AI translation provider with Chinese slang glossary."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        glossary: dict[str, str] | None = None,
        timeout_sec: float = 15.0,
        opener: Opener | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._glossary = glossary or {}
        self._timeout_sec = timeout_sec
        self._opener = opener or urllib.request.urlopen

    def _build_system_prompt(self, source_lang: str, target_lang: str) -> str:
        if target_lang.lower().startswith("vi"):
            lines = [
                f"Bạn là người dịch tin nhắn Telegram từ {source_lang or 'auto'} sang tiếng Việt tự nhiên, sát nghĩa đời thường.",
                "Ngữ cảnh thường gặp: nhóm công nghệ, AI subscription, tài khoản Google/Apple/Cloud, proxy/node và săn deal Trung Quốc.",
                "Dịch theo đúng ý người nói trong chat (không dịch máy móc từng chữ kiểu văn bản hành chính như 'lái chiếc', 'ràng buộc thẻ', 'giá bọ').",
                "Giữ nguyên URL, @username, đoạn code, tên sản phẩm/gói (Antigravity, Ultra, Pro, Gemini, Claude, Google Cloud, bug, node, script, terminal), con số, xuống dòng và emoji.",
                "Chỉ xuất ra đúng nội dung đã dịch, không giải thích thêm, không bọc dấu ngoặc kép hay markdown.",
            ]
        else:
            lines = [
                f"Translate chat messages from {source_lang or 'auto'} to {target_lang}.",
                "Output ONLY the translated text with no explanations, quotes, or markdown wrappers.",
                "Preserve URLs, @usernames, code snippets, commands, numbers, line breaks, and emojis verbatim.",
            ]
        if self._glossary:
            terms = "; ".join(f"{k} -> {v}" for k, v in self._glossary.items())
            lines.append(f"Từ điển ưu tiên: {terms}.")
        return "\n".join(lines)

    def _is_gemini_native(self) -> bool:
        return (
            "generativelanguage.googleapis.com" in self._base_url
            and "/openai" not in self._base_url
        )

    def _is_local_endpoint(self) -> bool:
        parsed = urllib.parse.urlsplit(self._base_url)
        host = (parsed.hostname or "").lower()
        return host in {"127.0.0.1", "localhost", "::1"}

    def _call_gemini_native(self, text: str, source_lang: str, target_lang: str) -> str:
        model_name = self._model or "gemini-2.5-flash"
        if self._base_url.endswith(":generateContent"):
            endpoint = self._base_url
        else:
            endpoint = f"{self._base_url}/models/{urllib.parse.quote(model_name)}:generateContent"
        sep = "&" if "?" in endpoint else "?"
        url = f"{endpoint}{sep}key={urllib.parse.quote(self._api_key)}"
        payload = json.dumps(
            {
                "systemInstruction": {
                    "parts": [{"text": self._build_system_prompt(source_lang, target_lang)}]
                },
                "contents": [{"role": "user", "parts": [{"text": text}]}],
                "generationConfig": {"temperature": 0.1},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        raw = self._execute_request(req)
        try:
            parsed = json.loads(raw.decode("utf-8"))
            parts = parsed["candidates"][0]["content"]["parts"]
            content = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Invalid Gemini response payload: {exc}") from exc
        cleaned = _THINK_BLOCK_RE.sub("", content).strip()
        if not cleaned:
            raise ProviderError("Gemini provider returned empty translation")
        return cleaned

    def _execute_request(
        self,
        req: urllib.request.Request,
        timeout_sec: float | None = None,
    ) -> bytes:
        try:
            with self._opener(req, timeout=timeout_sec or self._timeout_sec) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:240]
            except Exception:
                pass
            raise ProviderError(f"AI HTTP {exc.code}: {detail or exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"AI translation request failed: {exc}") from exc

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        if not self._base_url or (not self._api_key and not self._is_local_endpoint()):
            raise ProviderError("AI provider requires ai_base_url and ai_api_key")
        if self._is_gemini_native():
            return self._call_gemini_native(text, source_lang, target_lang)

        endpoint = (
            self._base_url
            if self._base_url.endswith("/chat/completions")
            else f"{self._base_url}/chat/completions"
        )
        body_obj: dict[str, object] = {
            "model": self._model,
            "stream": False,
            "messages": [
                {"role": "system", "content": self._build_system_prompt(source_lang, target_lang)},
                {"role": "user", "content": text},
            ],
        }
        model_l = self._model.lower()
        if not (model_l.startswith("co-") or "gpt-5" in model_l or "gpt-6" in model_l):
            body_obj["temperature"] = 0.1
        payload = json.dumps(body_obj).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers=headers,
            method="POST",
        )
        raw = self._execute_request(req)
        raw_text = raw.decode("utf-8", errors="replace")

        content: str | None = None
        try:
            parsed = json.loads(raw_text)
            msg = parsed["choices"][0]["message"]["content"]
            if isinstance(msg, str):
                content = msg
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            content = _extract_sse_content(raw_text)
            if content is None:
                raise ProviderError(f"Invalid AI response payload: {exc}") from exc

        cleaned = _THINK_BLOCK_RE.sub("", content).strip()
        if not cleaned:
            raise ProviderError("AI provider returned empty translation")
        return cleaned

    def _build_batch_system_prompt(self, source_lang: str, target_lang: str) -> str:
        base = self._build_system_prompt(source_lang, target_lang)
        return (
            f"{base}\n"
            'Input is a JSON array of {"id": number, "text": string}. '
            'Output ONLY valid JSON in the exact format {"translations": [{"id": number, "vi": string}]}. '
            "Translate every item independently in order, keeping IDs unchanged and preserving URLs, @usernames, code, numbers, and emojis."
        )

    def translate_batch(self, texts: list[str], source_lang: str, target_lang: str) -> list[str]:
        if not texts:
            return []
        if len(texts) == 1:
            return [self.translate(texts[0], source_lang, target_lang)]
        if not self._base_url or (not self._api_key and not self._is_local_endpoint()):
            raise ProviderError("AI provider requires ai_base_url and ai_api_key")

        items = [{"id": idx, "text": txt} for idx, txt in enumerate(texts)]
        sys_prompt = self._build_batch_system_prompt(source_lang, target_lang)
        user_payload = json.dumps(items, ensure_ascii=False)

        if self._is_gemini_native():
            model_name = self._model or "gemini-2.5-flash"
            endpoint = (
                self._base_url
                if self._base_url.endswith(":generateContent")
                else f"{self._base_url}/models/{urllib.parse.quote(model_name)}:generateContent"
            )
            sep = "&" if "?" in endpoint else "?"
            url = f"{endpoint}{sep}key={urllib.parse.quote(self._api_key)}"
            payload = json.dumps(
                {
                    "systemInstruction": {"parts": [{"text": sys_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": user_payload}]}],
                    "generationConfig": {"temperature": 0.1},
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            batch_timeout = max(self._timeout_sec, min(90.0, 15.0 + 1.8 * len(texts)))
            raw = self._execute_request(req, timeout_sec=batch_timeout)
            parsed = json.loads(raw.decode("utf-8"))
            parts = parsed["candidates"][0]["content"]["parts"]
            content = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
        else:
            endpoint = (
                self._base_url
                if self._base_url.endswith("/chat/completions")
                else f"{self._base_url}/chat/completions"
            )
            body_obj: dict[str, object] = {
                "model": self._model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_payload},
                ],
            }
            model_l = self._model.lower()
            if not (model_l.startswith("co-") or "gpt-5" in model_l or "gpt-6" in model_l):
                body_obj["temperature"] = 0.1
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(body_obj).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            batch_timeout = max(self._timeout_sec, min(90.0, 15.0 + 1.8 * len(texts)))
            raw = self._execute_request(req, timeout_sec=batch_timeout)
            raw_text = raw.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw_text)
                content = parsed["choices"][0]["message"]["content"]
            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                content = _extract_sse_content(raw_text) or ""
                if not content:
                    raise ProviderError(f"Invalid AI batch response payload: {exc}") from exc

        cleaned = _THINK_BLOCK_RE.sub("", content).strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if len(lines) >= 2:
                cleaned = "\n".join(lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]).strip()

        try:
            obj = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"AI batch response was not valid JSON: {exc}") from exc

        rows = obj.get("translations") if isinstance(obj, dict) else obj
        if not isinstance(rows, list):
            raise ProviderError("Unexpected AI batch JSON shape")

        mapped: dict[int, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                rid = int(row["id"])
            except (KeyError, TypeError, ValueError):
                continue
            val = row.get("vi") or row.get("text") or row.get("translation")
            if isinstance(val, str) and val.strip():
                mapped[rid] = val.strip()

        results: list[str] = []
        for idx, orig in enumerate(texts):
            translated = mapped.get(idx)
            if not translated:
                translated = self.translate(orig, source_lang, target_lang)
            results.append(translated)
        return results


def create_provider(cfg: TranslationConfig) -> BaseTranslateProvider:
    """Instantiate the configured translation provider."""
    if cfg.provider == "google_gtx":
        return GoogleGtxProvider(
            timeout_sec=cfg.timeout_sec,
            slang_fixups=cfg.slang_fixups,
        )
    if cfg.provider == "ai":
        host = (urllib.parse.urlsplit(cfg.ai_base_url).hostname or "").lower()
        is_local = host in {"127.0.0.1", "localhost", "::1"}
        if not cfg.ai_base_url or (not cfg.ai_api_key and not is_local):
            raise ProviderError("AI provider selected but ai_base_url/ai_api_key are missing")
        return AiTranslateProvider(
            base_url=cfg.ai_base_url,
            api_key=cfg.ai_api_key,
            model=cfg.ai_model,
            glossary=cfg.glossary,
            timeout_sec=max(cfg.timeout_sec, 20.0),
        )
    raise ProviderError(f"Unknown translation provider: {cfg.provider}")
