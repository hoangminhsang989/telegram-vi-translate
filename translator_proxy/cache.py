from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any


def _cache_key(provider: str, target_lang: str, text: str) -> str:
    payload = f"{provider}\x00{target_lang}\x00{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class TranslationCache:
    """Thread-safe two-level (in-memory LRU + SQLite WAL) translation cache."""

    def __init__(self, db_path: Path, max_memory_items: int = 4096) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_memory_items = max(1, max_memory_items)
        self._lock = threading.Lock()
        self._lru: OrderedDict[str, str] = OrderedDict()
        self._conn = sqlite3.connect(
            str(self._db_path),
            timeout=10.0,
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS translations (
                cache_key TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                source_text TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def _remember_lru(self, key: str, value: str) -> None:
        self._lru[key] = value
        self._lru.move_to_end(key)
        while len(self._lru) > self._max_memory_items:
            self._lru.popitem(last=False)

    def get(self, provider: str, target_lang: str, text: str) -> str | None:
        key = _cache_key(provider, target_lang, text)
        with self._lock:
            cached = self._lru.get(key)
            if cached is not None:
                self._lru.move_to_end(key)
                return cached
            row = self._conn.execute(
                "SELECT translated_text FROM translations WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            translated = str(row[0])
            self._remember_lru(key, translated)
            return translated

    def put(self, provider: str, target_lang: str, text: str, translated: str) -> None:
        key = _cache_key(provider, target_lang, text)
        now = time.time()
        with self._lock:
            self._remember_lru(key, translated)
            self._conn.execute(
                """
                INSERT INTO translations (
                    cache_key, provider, target_lang, source_text, translated_text, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    translated_text = excluded.translated_text,
                    updated_at = excluded.updated_at
                """,
                (key, provider, target_lang, text, translated, now),
            )
            self._conn.commit()

    def list_recent(self, limit: int = 50, query: str = "") -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        q = query.strip()
        with self._lock:
            if q:
                like = f"%{q}%"
                rows = self._conn.execute(
                    """
                    SELECT cache_key, provider, target_lang, source_text, translated_text, updated_at
                    FROM translations
                    WHERE source_text LIKE ? OR translated_text LIKE ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (like, like, safe_limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT cache_key, provider, target_lang, source_text, translated_text, updated_at
                    FROM translations
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
        return [
            {
                "cache_key": r[0],
                "provider": r[1],
                "target_lang": r[2],
                "source_text": r[3],
                "translated_text": r[4],
                "updated_at": float(r[5]),
            }
            for r in rows
        ]

    def delete_key(self, cache_key: str) -> bool:
        with self._lock:
            self._lru.pop(cache_key, None)
            cur = self._conn.execute(
                "DELETE FROM translations WHERE cache_key = ?",
                (cache_key,),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def clear(self, provider: str | None = None) -> int:
        with self._lock:
            self._lru.clear()
            if provider:
                cur = self._conn.execute(
                    "DELETE FROM translations WHERE provider = ?",
                    (provider,),
                )
            else:
                cur = self._conn.execute("DELETE FROM translations")
            self._conn.commit()
            return cur.rowcount

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM translations").fetchone()
            return int(row[0]) if row else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
