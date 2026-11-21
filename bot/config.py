"""Загрузка конфигурации из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _parse_int_set(raw: str) -> set[int]:
    result: set[int] = set()
    for part in raw.split(","):
        cleaned = part.strip()
        if cleaned.lstrip("-").isdigit():
            result.add(int(cleaned))
    return result


@dataclass(slots=True)
class AppConfig:
    bot_token: str
    admin_ids: set[int]
    bind_code: str
    initial_pin: str
    gemini_api_key: str
    gemini_model: str
    db_path: str


def load_config() -> AppConfig:
    return AppConfig(
        bot_token=os.environ.get("BOT_TOKEN", "").strip(),
        admin_ids=_parse_int_set(os.environ.get("ADMIN_IDS", "")),
        bind_code=os.environ.get("BIND_CODE", "").strip(),
        initial_pin=os.environ.get("INITIAL_PIN", "").strip(),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash",
        db_path=os.environ.get("DB_PATH", "/app/data/multitool.sqlite3").strip(),
    )
