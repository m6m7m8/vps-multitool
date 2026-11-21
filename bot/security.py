"""Хеширование PIN-кода и middleware контроля доступа (вайтлист + привязка + замок)."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

if TYPE_CHECKING:
    from bot.config import AppConfig
    from bot.db import BotDB


def hash_pin(pin: str, salt_hex: str | None = None) -> tuple[str, str]:
    """Хеширует PIN/пароль через PBKDF2-HMAC-SHA256 со случайной солью."""
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, 120_000)
    return digest.hex(), salt.hex()


def verify_pin(pin: str, stored_hash: str, salt_hex: str) -> bool:
    """Безопасная проверка PIN/пароля в постоянном времени."""
    if not stored_hash or not salt_hex:
        return False
    candidate_hash, _ = hash_pin(pin, salt_hex)
    return hmac.compare_digest(candidate_hash, stored_hash)


def extract_bind_candidate(text: str | None) -> str:
    if not text:
        return ""
    raw = text.strip()
    if raw.startswith("/start "):
        return raw.split(maxsplit=1)[1].strip()
    return raw


class AccessControlMiddleware(BaseMiddleware):
    """Пропускает только админов из вайтлиста (или одноразовый код привязки первого админа)."""

    def __init__(self, cfg: AppConfig, db: BotDB) -> None:
        self.cfg = cfg
        self.db = db

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return None

        if await self.db.is_admin(user.id):
            if await self.db.has_pin() and not await self.db.is_unlocked(user.id):
                data["needs_pin"] = True
            return await handler(event, data)

        # Если админов ещё нет и задан одноразовый BIND_CODE — допускаем только точный код
        if self.cfg.bind_code and not await self.db.has_any_admin():
            if isinstance(event, Message):
                candidate = extract_bind_candidate(event.text)
                if candidate and hmac.compare_digest(candidate, self.cfg.bind_code):
                    data["bind_attempt"] = True
                    return await handler(event, data)

        return None
