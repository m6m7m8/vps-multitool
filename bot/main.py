"""Точка входа Telegram-бота VPS Multitool v2."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import load_config
from bot.db import BotDB
from bot.handlers import ai_chat, ai_settings, auth, memory
from bot.security import AccessControlMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("multitool-v2")


async def run() -> None:
    cfg = load_config()
    if not cfg.bot_token:
        raise SystemExit("BOT_TOKEN не задан в .env!")

    db = BotDB(cfg.db_path)
    await db.init(cfg)

    if not await db.has_any_admin() and not cfg.bind_code:
        logger.warning("Внимание: не задан ни ADMIN_IDS, ни BIND_CODE — бот никому не ответит!")

    bot = Bot(token=cfg.bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    mw = AccessControlMiddleware(cfg, db)
    dp.message.middleware(mw)
    dp.callback_query.middleware(mw)

    dp["cfg"] = cfg
    dp["db"] = db

    # Порядок роутеров важен: сначала auth (привязка/PIN/безопасность), в конце ai_chat
    dp.include_router(auth.router)
    dp.include_router(ai_settings.router)
    dp.include_router(memory.router)
    dp.include_router(ai_chat.router)

    try:
        logger.info("VPS Multitool Bot v2 запущен (модель по умолчанию: %s)", cfg.gemini_model)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())
