from __future__ import annotations

import html
import json
import re
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

Opener = Callable[[urllib.request.Request, float], object]


class ProviderError(RuntimeError):
    """Raised when an upstream translation provider fails or returns invalid data."""


def prepare_source_text(raw_q: str) -> tuple[str, bool]:
    """Unescape Telegram's HTML-escaped query and decide whether to skip upstream translation."""
    text = html.unescape(raw_q)
    stripped = text.strip()
    if not stripped:
        return text, True
    if _URL_ONLY_RE.match(stripped) or _MENTION_OR_SYMBOL_ONLY_RE.match(stripped):
        return text, True
    if _VIETNAMESE_DIACRITICS_RE.search(stripped) and not _CJK_RE.search(stripped):
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


class BaseTranslateProvider:
    """Common interface for translation providers."""

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        raise NotImplementedError


class GoogleGtxProvider(BaseTranslateProvider):
    """Free Google Translate gtx endpoint provider."""

    _ENDPOINT = "https://translate.googleapis.com/translate_a/single"

    def __init__(
        self,
        timeout_sec: float = 8.0,
        opener: Opener | None = None,
        max_chunk_chars: int = 1800,
    ) -> None:
        self._timeout_sec = timeout_sec
        self._opener = opener or urllib.request.urlopen
        self._max_chunk_chars = max_chunk_chars

    def _translate_chunk(self, chunk: str, source_lang: str, target_lang: str) -> str:
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
        try:
            with self._opener(req, timeout=self._timeout_sec) as resp:
                raw = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"Google GTX request failed: {exc}") from exc

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

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        chunks = _split_text_chunks(text, self._max_chunk_chars)
        out: list[str] = []
        for chunk in chunks:
            trailing_nl = len(chunk) - len(chunk.rstrip("\n"))
            translated = self._translate_chunk(chunk.rstrip("\n") or chunk, source_lang, target_lang)
            if trailing_nl and not translated.endswith("\n"):
                translated = translated + ("\n" * trailing_nl)
            out.append(translated)
        return "".join(out)


class AiTranslateProvider(BaseTranslateProvider):
    """OpenAI-compatible chat completions provider with Chinese slang glossary support."""

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
        lines = [
            f"Translate chat messages from {source_lang or 'auto'} to {target_lang} (natural Vietnamese).",
            "Output ONLY the translated text with no explanations or markdown wrappers.",
            "Preserve URLs, @usernames, code snippets, commands, numbers, and emojis verbatim.",
        ]
        if self._glossary:
            terms = "; ".join(f"{k} -> {v}" for k, v in self._glossary.items())
            lines.append(f"Preferred domain glossary: {terms}.")
        return "\n".join(lines)

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        if not self._base_url or not self._api_key:
            raise ProviderError("AI provider requires ai_base_url and ai_api_key")
        endpoint = (
            self._base_url
            if self._base_url.endswith("/chat/completions")
            else f"{self._base_url}/chat/completions"
        )
        payload = json.dumps(
            {
                "model": self._model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": self._build_system_prompt(source_lang, target_lang)},
                    {"role": "user", "content": text},
                ],
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        try:
            with self._opener(req, timeout=self._timeout_sec) as resp:
                raw = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"AI translation request failed: {exc}") from exc

        try:
            parsed = json.loads(raw.decode("utf-8"))
            content = parsed["choices"][0]["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Invalid AI response payload: {exc}") from exc

        if not isinstance(content, str) or not content.strip():
            raise ProviderError("AI provider returned empty translation")
        return content.strip()


def create_provider(cfg: TranslationConfig) -> BaseTranslateProvider:
    """Instantiate the configured translation provider."""
    if cfg.provider == "google_gtx":
        return GoogleGtxProvider(timeout_sec=cfg.timeout_sec)
    if cfg.provider == "ai":
        if not cfg.ai_base_url or not cfg.ai_api_key:
            raise ProviderError("AI provider selected but ai_base_url/ai_api_key are missing")
        return AiTranslateProvider(
            base_url=cfg.ai_base_url,
            api_key=cfg.ai_api_key,
            model=cfg.ai_model,
            glossary=cfg.glossary,
            timeout_sec=max(cfg.timeout_sec, 12.0),
        )
    raise ProviderError(f"Unknown translation provider: {cfg.provider}")
