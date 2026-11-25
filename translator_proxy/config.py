from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_CONFIG_PATH = _PACKAGE_DIR / "config.default.json"
_REPO_ROOT = _PACKAGE_DIR.parent


def _default_glossary() -> dict[str, str]:
    return {
        "0元购": "săn deal 0 đồng",
        "薅羊毛": "săn kèo hời",
        "羊毛": "kèo hời / ưu đãi",
        "脚本": "script",
        "上车": "tham gia / chốt kèo",
    }


@dataclass
class TranslationConfig:
    """Configuration for the localhost Telegram translation proxy."""

    host: str = "127.0.0.1"
    port: int = 18765
    provider: str = "google_gtx"
    force_target_vi: bool = True
    target_lang: str = "vi"
    cache_db: Path = field(default_factory=lambda: _REPO_ROOT / "data" / "cache.db")
    max_memory_items: int = 4096
    timeout_sec: float = 8.0
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = "gpt-4o-mini"
    glossary: dict[str, str] = field(default_factory=_default_glossary)


def load_config(path: Path | None = None) -> TranslationConfig:
    """Load config JSON merged over package defaults."""
    merged: dict = {}
    if _DEFAULT_CONFIG_PATH.exists():
        merged.update(json.loads(_DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")))

    user_path = path if path is not None else (_PACKAGE_DIR / "config.json")
    if user_path.exists():
        user_data = json.loads(user_path.read_text(encoding="utf-8"))
        merged.update(user_data)

    raw_db = Path(merged.get("cache_db", "data/cache.db"))
    cache_db = raw_db if raw_db.is_absolute() else (_REPO_ROOT / raw_db)

    glossary_raw = merged.get("glossary")
    glossary = dict(glossary_raw) if isinstance(glossary_raw, dict) else _default_glossary()

    return TranslationConfig(
        host=str(merged.get("host", "127.0.0.1")),
        port=int(merged.get("port", 18765)),
        provider=str(merged.get("provider", "google_gtx")),
        force_target_vi=bool(merged.get("force_target_vi", True)),
        target_lang=str(merged.get("target_lang", "vi")),
        cache_db=cache_db,
        max_memory_items=int(merged.get("max_memory_items", 4096)),
        timeout_sec=float(merged.get("timeout_sec", 8.0)),
        ai_base_url=str(merged.get("ai_base_url", "")),
        ai_api_key=str(merged.get("ai_api_key", "")),
        ai_model=str(merged.get("ai_model", "gpt-4o-mini")),
        glossary=glossary,
    )
