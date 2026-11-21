"""Асинхронная SQLite-база: админы, PIN с анти-брутом, настройки ИИ и двойная память."""

from __future__ import annotations

import os
import time
from typing import Any

import aiosqlite

from bot.config import AppConfig
from bot.security import hash_pin, verify_pin

DEFAULT_SYSTEM_PROMPT = (
    "Ты умный, лаконичный и честный личный ИИ-ассистент. "
    "Отвечай по делу на языке пользователя (обычно на русском). "
    "Если спрашивают о сервере или коде — давай точные практичные команды и решения."
)

MAX_MEMORY_FACTS = 25
MAX_STORED_HISTORY_PER_USER = 50
PIN_MAX_FAILED_ATTEMPTS = 3
PIN_LOCKOUT_SECONDS = 15 * 60


class BotDB:
    """Слой данных бота."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self, cfg: AppConfig) -> None:
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL DEFAULT '',
                    added_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS kv_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    user_id INTEGER PRIMARY KEY,
                    unlocked_until INTEGER NOT NULL DEFAULT 0,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chat_history_user ON chat_history(user_id, id);

                CREATE TABLE IF NOT EXISTS user_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    fact TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_user_memory_user ON user_memory(user_id, id);
                """
            )
            now = int(time.time())
            for uid in cfg.admin_ids:
                await db.execute(
                    "INSERT OR IGNORE INTO admins (user_id, username, added_at) VALUES (?, '', ?)",
                    (uid, now),
                )

            defaults = {
                "bind_used": "0",
                "session_ttl_minutes": "30",
                "gemini_api_key": cfg.gemini_api_key,
                "ai_model": cfg.gemini_model,
                "ai_system_prompt": DEFAULT_SYSTEM_PROMPT,
                "ai_temperature": "0.7",
                "ai_max_tokens": "2048",
                "ai_history_limit": "10",
                "ai_google_search": "0",
                "ai_memory_enabled": "1",
            }
            for k, v in defaults.items():
                await db.execute(
                    "INSERT OR IGNORE INTO kv_settings (key, value) VALUES (?, ?)",
                    (k, str(v)),
                )

            if cfg.gemini_api_key:
                cur = await db.execute(
                    "SELECT value FROM kv_settings WHERE key = 'gemini_api_key'"
                )
                row = await cur.fetchone()
                if row and not row[0]:
                    await db.execute(
                        "UPDATE kv_settings SET value = ? WHERE key = 'gemini_api_key'",
                        (cfg.gemini_api_key,),
                    )

            if cfg.initial_pin:
                cur = await db.execute("SELECT value FROM kv_settings WHERE key = 'pin_hash'")
                if await cur.fetchone() is None:
                    p_hash, p_salt = hash_pin(cfg.initial_pin)
                    await db.execute(
                        "INSERT INTO kv_settings (key, value) VALUES ('pin_hash', ?)",
                        (p_hash,),
                    )
                    await db.execute(
                        "INSERT INTO kv_settings (key, value) VALUES ('pin_salt', ?)",
                        (p_salt,),
                    )

            await db.commit()

    # --- Вайтлист и одноразовая привязка ---

    async def is_admin(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
            return (await cur.fetchone()) is not None

    async def has_any_admin(self) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT 1 FROM admins LIMIT 1")
            return (await cur.fetchone()) is not None

    async def add_admin(self, user_id: int, username: str = "") -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO admins (user_id, username, added_at) VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET username = excluded.username
                """,
                (user_id, username or "", int(time.time())),
            )
            await db.commit()

    async def remove_admin(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM admins")
            count = (await cur.fetchone())[0]
            if count <= 1:
                return False
            cur2 = await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            await db.commit()
            return cur2.rowcount > 0

    async def list_admins(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT user_id, username, added_at FROM admins ORDER BY added_at ASC"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def try_consume_bind_code(self, user_id: int, username: str = "") -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM admins")
            if (await cur.fetchone())[0] > 0:
                return False
            cur2 = await db.execute("SELECT value FROM kv_settings WHERE key = 'bind_used'")
            row = await cur2.fetchone()
            if row and row[0] == "1":
                return False
            await db.execute(
                "INSERT INTO admins (user_id, username, added_at) VALUES (?, ?, ?)",
                (user_id, username or "", int(time.time())),
            )
            await db.execute(
                "INSERT OR REPLACE INTO kv_settings (key, value) VALUES ('bind_used', '1')"
            )
            await db.commit()
            return True

    # --- PIN и защита от перебора ---

    async def has_pin(self) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT value FROM kv_settings WHERE key = 'pin_hash'")
            row = await cur.fetchone()
            return bool(row and row[0])

    async def set_pin(self, pin: str) -> None:
        p_hash, p_salt = hash_pin(pin)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO kv_settings (key, value) VALUES ('pin_hash', ?)",
                (p_hash,),
            )
            await db.execute(
                "INSERT OR REPLACE INTO kv_settings (key, value) VALUES ('pin_salt', ?)",
                (p_salt,),
            )
            await db.execute("DELETE FROM auth_sessions")
            await db.commit()

    async def clear_pin(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM kv_settings WHERE key IN ('pin_hash', 'pin_salt')")
            await db.execute("DELETE FROM auth_sessions")
            await db.commit()

    async def is_unlocked(self, user_id: int, now: int | None = None) -> bool:
        if not await self.has_pin():
            return True
        ts = int(now if now is not None else time.time())
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT unlocked_until FROM auth_sessions WHERE user_id = ?", (user_id,)
            )
            row = await cur.fetchone()
            return bool(row and int(row[0]) > ts)

    async def lock_session(self, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO auth_sessions (user_id, unlocked_until, failed_attempts, locked_until)
                VALUES (?, 0, 0, 0)
                ON CONFLICT(user_id) DO UPDATE SET unlocked_until = 0
                """,
                (user_id,),
            )
            await db.commit()

    async def verify_and_unlock(
        self, user_id: int, candidate: str, now: int | None = None
    ) -> dict[str, Any]:
        ts = int(now if now is not None else time.time())
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT key, value FROM kv_settings WHERE key IN ('pin_hash', 'pin_salt', 'session_ttl_minutes')"
            )
            kv = dict(await cur.fetchall())
            p_hash = kv.get("pin_hash", "")
            p_salt = kv.get("pin_salt", "")
            ttl_min = int(kv.get("session_ttl_minutes", "30") or "30")

            if not p_hash:
                return {"ok": True, "locked_seconds": 0, "attempts_left": PIN_MAX_FAILED_ATTEMPTS}

            cur2 = await db.execute(
                "SELECT failed_attempts, locked_until FROM auth_sessions WHERE user_id = ?",
                (user_id,),
            )
            row = await cur2.fetchone()
            failed = int(row[0]) if row else 0
            locked_until = int(row[1]) if row else 0

            if locked_until > ts:
                return {
                    "ok": False,
                    "locked_seconds": locked_until - ts,
                    "attempts_left": 0,
                }

            if verify_pin(candidate, p_hash, p_salt):
                unlocked_until = ts + ttl_min * 60
                await db.execute(
                    """
                    INSERT INTO auth_sessions (user_id, unlocked_until, failed_attempts, locked_until)
                    VALUES (?, ?, 0, 0)
                    ON CONFLICT(user_id) DO UPDATE SET
                        unlocked_until = excluded.unlocked_until,
                        failed_attempts = 0,
                        locked_until = 0
                    """,
                    (user_id, unlocked_until),
                )
                await db.commit()
                return {
                    "ok": True,
                    "locked_seconds": 0,
                    "attempts_left": PIN_MAX_FAILED_ATTEMPTS,
                    "ttl_minutes": ttl_min,
                }

            failed += 1
            new_locked_until = 0
            if failed >= PIN_MAX_FAILED_ATTEMPTS:
                new_locked_until = ts + PIN_LOCKOUT_SECONDS
                failed = 0
                attempts_left = 0
                locked_secs = PIN_LOCKOUT_SECONDS
            else:
                attempts_left = PIN_MAX_FAILED_ATTEMPTS - failed
                locked_secs = 0

            await db.execute(
                """
                INSERT INTO auth_sessions (user_id, unlocked_until, failed_attempts, locked_until)
                VALUES (?, 0, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    unlocked_until = 0,
                    failed_attempts = excluded.failed_attempts,
                    locked_until = excluded.locked_until
                """,
                (user_id, failed, new_locked_until),
            )
            await db.commit()
            return {
                "ok": False,
                "locked_seconds": locked_secs,
                "attempts_left": attempts_left,
            }

    # --- Настройки ---

    async def get_setting(self, key: str, default: str = "") -> str:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT value FROM kv_settings WHERE key = ?", (key,))
            row = await cur.fetchone()
            return row[0] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO kv_settings (key, value) VALUES (?, ?)",
                (key, str(value)),
            )
            await db.commit()

    async def get_ai_settings(self) -> dict[str, Any]:
        keys = (
            "gemini_api_key",
            "ai_model",
            "ai_system_prompt",
            "ai_temperature",
            "ai_max_tokens",
            "ai_history_limit",
            "ai_google_search",
            "ai_memory_enabled",
            "session_ttl_minutes",
        )
        placeholders = ",".join("?" for _ in keys)
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                f"SELECT key, value FROM kv_settings WHERE key IN ({placeholders})",
                keys,
            )
            raw = dict(await cur.fetchall())

        return {
            "gemini_api_key": raw.get("gemini_api_key", ""),
            "ai_model": raw.get("ai_model", "gemini-3.8-flash"),
            "ai_system_prompt": raw.get("ai_system_prompt", DEFAULT_SYSTEM_PROMPT),
            "ai_temperature": float(raw.get("ai_temperature", "0.7")),
            "ai_max_tokens": int(raw.get("ai_max_tokens", "2048")),
            "ai_history_limit": int(raw.get("ai_history_limit", "10")),
            "ai_google_search": raw.get("ai_google_search", "0") == "1",
            "ai_memory_enabled": raw.get("ai_memory_enabled", "1") == "1",
            "session_ttl_minutes": int(raw.get("session_ttl_minutes", "30")),
        }

    # --- Скользящая история диалога ---

    async def add_history(self, user_id: int, role: str, content: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO chat_history (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (user_id, role, content, int(time.time())),
            )
            # Держим компактный хвост, чтобы SQLite не разрастался
            await db.execute(
                """
                DELETE FROM chat_history
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?
                )
                """,
                (user_id, user_id, MAX_STORED_HISTORY_PER_USER),
            )
            await db.commit()

    async def get_recent_history(self, user_id: int, limit: int = 10) -> list[dict[str, str]]:
        if limit <= 0:
            return []
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT role, content FROM (
                    SELECT id, role, content FROM chat_history
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (user_id, limit),
            )
            return [{"role": r["role"], "content": r["content"]} for r in await cur.fetchall()]

    async def clear_history(self, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
            await db.commit()

    # --- Долгая компактная память фактов ---

    async def add_memory_fact(self, user_id: int, fact: str) -> int:
        cleaned = " ".join(fact.strip().split())[:280]
        if not cleaned:
            raise ValueError("Пустой факт нельзя сохранить.")
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "INSERT INTO user_memory (user_id, fact, created_at) VALUES (?, ?, ?)",
                (user_id, cleaned, int(time.time())),
            )
            # Сохраняем не больше MAX_MEMORY_FACTS свежих фактов, чтобы не раздувать промпт
            await db.execute(
                """
                DELETE FROM user_memory
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM user_memory WHERE user_id = ? ORDER BY id DESC LIMIT ?
                )
                """,
                (user_id, user_id, MAX_MEMORY_FACTS),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def list_memory_facts(self, user_id: int) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, fact, created_at FROM user_memory WHERE user_id = ? ORDER BY id ASC",
                (user_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def delete_memory_fact(self, user_id: int, fact_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "DELETE FROM user_memory WHERE id = ? AND user_id = ?",
                (fact_id, user_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def clear_memory_facts(self, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM user_memory WHERE user_id = ?", (user_id,))
            await db.commit()
