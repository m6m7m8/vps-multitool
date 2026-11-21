"""Основной диалог с Gemini: текст, фото, файлы и компактное скользящее окно памяти."""

from __future__ import annotations

import html
import io
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, StateFilter
from aiogram.types import Message

from bot.db import BotDB
from bot.formatting import markdown_to_telegram_html, split_telegram_text
from bot.gemini_service import generate_reply

router = Router(name="ai_chat")
logger = logging.getLogger(__name__)

MAX_DOC_BYTES = 5 * 1024 * 1024
SUPPORTED_DOC_MIMES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/pdf",
    "text/x-python",
}


@router.message(Command("clear"))
@router.message(F.text == "💬 Новый диалог")
async def cmd_clear(message: Message, db: BotDB) -> None:
    await db.clear_history(message.from_user.id)
    await message.answer(
        "🧹 Контекст текущего диалога очищен.\n"
        "<i>(Короткие факты в «🧠 Память» сохранены)</i>",
        parse_mode="HTML",
    )


async def _send_formatted_reply(message: Message, raw_text: str, sources: list[dict[str, str]]) -> None:
    body_html = markdown_to_telegram_html(raw_text)
    if sources:
        src_lines = "\n".join(
            f"• <a href=\"{html.escape(s['uri'], quote=True)}\">{html.escape(s['title'])}</a>"
            for s in sources
        )
        body_html += f"\n\n🌐 <b>Источники:</b>\n{src_lines}"

    chunks = split_telegram_text(body_html, max_len=3900)
    for chunk in chunks:
        try:
            await message.answer(chunk, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:  # noqa: BLE001
            # На случай редкой ошибки парсинга HTML отправляем чистый текст
            for plain_chunk in split_telegram_text(raw_text, max_len=3900):
                await message.answer(plain_chunk, disable_web_page_preview=True)
            return


async def _run_gemini_turn(
    message: Message,
    db: BotDB,
    user_text: str,
    media_bytes: bytes | None = None,
    media_mime: str | None = None,
) -> None:
    uid = message.from_user.id
    settings = await db.get_ai_settings()
    api_key = settings["gemini_api_key"]
    if not api_key:
        await message.answer(
            "⚠️ Не задан <b>Gemini API Key</b>.\n"
            "Откройте «⚙️ Настройки ИИ» → «🔑 Задать / сменить Gemini API Key».",
            parse_mode="HTML",
        )
        return

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    hist_limit = int(settings["ai_history_limit"])
    history = await db.get_recent_history(uid, limit=hist_limit)
    facts_rows = await db.list_memory_facts(uid) if settings["ai_memory_enabled"] else []
    facts = [r["fact"] for r in facts_rows]

    try:
        res = await generate_reply(
            api_key=api_key,
            settings=settings,
            history=history,
            facts=facts,
            user_text=user_text,
            media_bytes=media_bytes,
            media_mime=media_mime,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка Gemini API: %s", exc)
        await message.answer(f"❌ Ошибка Gemini: <code>{html.escape(str(exc)[:600])}</code>", parse_mode="HTML")
        return

    switched = res.get("switched_model")
    if switched:
        old_model = settings.get("ai_model", "")
        await db.set_setting("ai_model", switched)
        await message.answer(
            f"ℹ️ Модель <code>{html.escape(old_model)}</code> устарела — "
            f"автоматически переключился на <code>{html.escape(switched)}</code>.",
            parse_mode="HTML",
        )

    if hist_limit > 0:
        stored_user = user_text.strip() or "[Прикреплён файл/изображение]"
        await db.add_history(uid, "user", stored_user[:4000])
        await db.add_history(uid, "model", res["text"][:4000])

    await _send_formatted_reply(message, res["text"], res["sources"])


@router.message(StateFilter(None), F.photo)
async def handle_photo(message: Message, bot: Bot, db: BotDB) -> None:
    photo = message.photo[-1]
    buf = io.BytesIO()
    await bot.download(photo, destination=buf)
    caption = (message.caption or "").strip()
    await _run_gemini_turn(message, db, caption, media_bytes=buf.getvalue(), media_mime="image/jpeg")


@router.message(StateFilter(None), F.document)
async def handle_document(message: Message, bot: Bot, db: BotDB) -> None:
    doc = message.document
    if doc.file_size and doc.file_size > MAX_DOC_BYTES:
        await message.answer("❌ Файл больше 5 МБ. Пришлите файл поменьше.")
        return

    mime = (doc.mime_type or "text/plain").lower()
    if mime not in SUPPORTED_DOC_MIMES and not mime.startswith("text/"):
        await message.answer(
            f"❌ Формат <code>{html.escape(mime)}</code> пока не поддерживается "
            "(поддерживаются текст, код, JSON, CSV, PDF и картинки).",
            parse_mode="HTML",
        )
        return

    buf = io.BytesIO()
    await bot.download(doc, destination=buf)
    caption = (message.caption or "").strip() or f"Проанализируй файл {doc.file_name or ''}:"
    await _run_gemini_turn(message, db, caption, media_bytes=buf.getvalue(), media_mime=mime)


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def handle_text(message: Message, db: BotDB) -> None:
    text = (message.text or "").strip()
    if not text:
        return
    await _run_gemini_turn(message, db, text)
