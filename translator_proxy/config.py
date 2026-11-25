from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_CONFIG_PATH = _PACKAGE_DIR / "config.default.json"
DEFAULT_USER_CONFIG_PATH = _PACKAGE_DIR / "config.json"
_REPO_ROOT = _PACKAGE_DIR.parent


def _default_glossary() -> dict[str, str]:
    if _DEFAULT_CONFIG_PATH.exists():
        raw = json.loads(_DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(raw.get("glossary"), dict):
            return {str(k): str(v) for k, v in raw["glossary"].items()}
    return {
        "0元购": "săn deal 0 đồng",
        "薅羊毛": "săn kèo hời",
        "羊毛": "kèo hời / ưu đãi",
        "脚本": "script",
        "上车": "chốt kèo / tham gia",
    }


def _normalize_mtproto_chats(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    seen: set[int] = set()
    for item in raw:
        if isinstance(item, int):
            cid = item
            title = str(cid)
            enabled = True
        elif isinstance(item, dict) and "id" in item:
            try:
                cid = int(item["id"])
            except (TypeError, ValueError):
                continue
            title = str(item.get("title") or cid)
            enabled = bool(item.get("enabled", True))
        else:
            continue
        if cid in seen:
            continue
        seen.add(cid)
        out.append({"id": cid, "title": title, "enabled": enabled})
    return out


@dataclass
class TranslationConfig:
    """Configuration for the localhost Telegram translation proxy."""

    host: str = "127.0.0.1"
    port: int = 18765
    provider: str = "google_gtx"
    force_target_vi: bool = True
    target_lang: str = "vi"
    fallback_to_google: bool = True
    slang_fixups: bool = True
    cache_db: Path = field(default_factory=lambda: _REPO_ROOT / "data" / "cache.db")
    max_memory_items: int = 4096
    timeout_sec: float = 8.0
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = "gpt-4o-mini"
    mtproto_enabled: bool = False
    mtproto_api_id: int = 0
    mtproto_api_hash: str = ""
    mtproto_chats: list[dict[str, Any]] = field(default_factory=list)
    mtproto_prefetch_limit: int = 300
    mtproto_batch_size: int = 20
    mtproto_listen_new: bool = True
    glossary: dict[str, str] = field(default_factory=_default_glossary)

    def to_public_dict(self, mask_secret: bool = True) -> dict[str, Any]:
        """Serialize configuration for JSON responses or disk persistence."""
        data = asdict(self)
        try:
            data["cache_db"] = str(self.cache_db.relative_to(_REPO_ROOT)).replace("\\", "/")
        except ValueError:
            data["cache_db"] = str(self.cache_db)
        if mask_secret:
            key = data.pop("ai_api_key", "")
            data["has_ai_api_key"] = bool(key)
            data["ai_api_key_preview"] = (
                f"{key[:4]}...{key[-4:]}" if len(key) > 10 else ("***" if key else "")
            )
            mt_hash = data.pop("mtproto_api_hash", "")
            data["has_mtproto_api_hash"] = bool(mt_hash)
            data["mtproto_api_hash_preview"] = (
                f"{mt_hash[:4]}...{mt_hash[-4:]}" if len(mt_hash) > 8 else ("***" if mt_hash else "")
            )
        return data


def _apply_env_overrides(merged: dict[str, Any]) -> None:
    env_map = {
        "VI_TRANSLATE_PROVIDER": "provider",
        "VI_TRANSLATE_AI_BASE_URL": "ai_base_url",
        "VI_TRANSLATE_AI_API_KEY": "ai_api_key",
        "VI_TRANSLATE_AI_MODEL": "ai_model",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key, "").strip()
        if val:
            merged[cfg_key] = val


def config_from_dict(merged: dict[str, Any]) -> TranslationConfig:
    """Build a TranslationConfig instance from a merged JSON dictionary."""
    raw_db = Path(merged.get("cache_db", "data/cache.db"))
    cache_db = raw_db if raw_db.is_absolute() else (_REPO_ROOT / raw_db)

    glossary_raw = merged.get("glossary")
    glossary = (
        {str(k): str(v) for k, v in glossary_raw.items()}
        if isinstance(glossary_raw, dict)
        else _default_glossary()
    )

    try:
        api_id = int(merged.get("mtproto_api_id") or 0)
    except (TypeError, ValueError):
        api_id = 0

    return TranslationConfig(
        host=str(merged.get("host", "127.0.0.1")),
        port=int(merged.get("port", 18765)),
        provider=str(merged.get("provider", "google_gtx")),
        force_target_vi=bool(merged.get("force_target_vi", True)),
        target_lang=str(merged.get("target_lang", "vi")),
        fallback_to_google=bool(merged.get("fallback_to_google", True)),
        slang_fixups=bool(merged.get("slang_fixups", True)),
        cache_db=cache_db,
        max_memory_items=int(merged.get("max_memory_items", 4096)),
        timeout_sec=float(merged.get("timeout_sec", 8.0)),
        ai_base_url=str(merged.get("ai_base_url", "")),
        ai_api_key=str(merged.get("ai_api_key", "")),
        ai_model=str(merged.get("ai_model", "gpt-4o-mini")),
        mtproto_enabled=bool(merged.get("mtproto_enabled", False)),
        mtproto_api_id=api_id,
        mtproto_api_hash=str(merged.get("mtproto_api_hash", "")).strip(),
        mtproto_chats=_normalize_mtproto_chats(merged.get("mtproto_chats", [])),
        mtproto_prefetch_limit=max(10, min(2000, int(merged.get("mtproto_prefetch_limit", 300)))),
        mtproto_batch_size=max(1, min(40, int(merged.get("mtproto_batch_size", 20)))),
        mtproto_listen_new=bool(merged.get("mtproto_listen_new", True)),
        glossary=glossary,
    )


def load_config(path: Path | None = None) -> TranslationConfig:
    """Load config JSON merged over package defaults and optional env overrides."""
    merged: dict[str, Any] = {}
    if _DEFAULT_CONFIG_PATH.exists():
        merged.update(json.loads(_DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")))

    user_path = path if path is not None else DEFAULT_USER_CONFIG_PATH
    if user_path.exists():
        user_data = json.loads(user_path.read_text(encoding="utf-8"))
        merged.update(user_data)

    _apply_env_overrides(merged)
    return config_from_dict(merged)


def save_config_updates(updates: dict[str, Any], path: Path | None = None) -> TranslationConfig:
    """Persist partial config updates to user config JSON and return reloaded config."""
    target_path = path if path is not None else DEFAULT_USER_CONFIG_PATH
    base: dict[str, Any] = {}
    if _DEFAULT_CONFIG_PATH.exists():
        base.update(json.loads(_DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")))
    if target_path.exists():
        base.update(json.loads(target_path.read_text(encoding="utf-8")))

    allowed_keys = {
        "host",
        "port",
        "provider",
        "force_target_vi",
        "target_lang",
        "fallback_to_google",
        "slang_fixups",
        "cache_db",
        "max_memory_items",
        "timeout_sec",
        "ai_base_url",
        "ai_api_key",
        "ai_model",
        "mtproto_enabled",
        "mtproto_api_id",
        "mtproto_api_hash",
        "mtproto_chats",
        "mtproto_prefetch_limit",
        "mtproto_batch_size",
        "mtproto_listen_new",
        "glossary",
    }
    for key, value in updates.items():
        if key in {"ai_api_key", "mtproto_api_hash"} and (value is None or value == ""):
            continue
        if key in allowed_keys:
            base[key] = value

    cfg = config_from_dict(base)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    serializable = cfg.to_public_dict(mask_secret=False)
    tmp_path = target_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(serializable, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp_path.replace(target_path)
    return load_config(target_path)
