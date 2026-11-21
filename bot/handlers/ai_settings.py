"""Гибкие настройки Gemini AI: модель, роль, температура, токены, поиск Google, ключ."""

from __future__ import annotations

import html
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.db import BotDB, DEFAULT_SYSTEM_PROMPT
from bot.gemini_service import list_available_models, validate_gemini_key
from bot.keyboards import (
    ai_settings_keyboard,
    history_picker_keyboard,
    model_picker_keyboard,
    role_picker_keyboard,
    temp_picker_keyboard,
    tokens_picker_keyboard,
)

router = Router(name="ai_settings")

ROLE_PRESETS = {
    "assistant": DEFAULT_SYSTEM_PROMPT,
    "coder": (
        "Ты опытный senior-разработчик и DevOps-инженер. "
        "Пиши чистый рабочий код, сразу давай готовые команды и указывай на подводные камни без воды."
    ),
    "editor": (
        "Ты профессиональный редактор и копирайтер. "
        "Улучшай стиль, делай текст живым, читаемым и структурированным, сохраняя смысл."
    ),
    "translator": (
        "Ты точный переводчик. Переводи смысл, сленг и технические термины максимально естественно "
        "(если текст не на русском — переводи на русский, если на русском — на естественный английский)."
    ),
}


class AiSettingsForm(StatesGroup):
    waiting_custom_model = State()
    waiting_custom_prompt = State()
    waiting_api_key = State()


def _mask_key(key: str) -> str:
    if not key:
        return "❌ не задан"
    if len(key) <= 10:
        return "****"
    return f"{key[:6]}…{key[-4:]}"


def _format_settings_text(s: dict[str, Any]) -> str:
    prompt_preview = s["ai_system_prompt"].replace("\n", " ")
    if len(prompt_preview) > 120:
        prompt_preview = prompt_preview[:120] + "…"
    return (
        "⚙️ <b>Настройки Gemini AI</b>\n\n"
        f"• <b>API-ключ:</b> <code>{html.escape(_mask_key(s['gemini_api_key']))}</code>\n"
        f"• <b>Модель:</b> <code>{html.escape(s['ai_model'])}</code>\n"
        f"• <b>Температура:</b> <b>{s['ai_temperature']}</b>\n"
        f"• <b>Окно диалога:</b> <b>{s['ai_history_limit']}</b> последних сообщений\n"
        f"• <b>Макс. длина ответа:</b> <b>{s['ai_max_tokens']}</b> токенов\n"
        f"• <b>Поиск Google:</b> {'✅ Вкл' if s['ai_google_search'] else '❌ Выкл'}\n"
        f"• <b>Долгая память:</b> {'✅ Вкл' if s['ai_memory_enabled'] else '❌ Выкл'}\n\n"
        f"<b>Системный промпт:</b>\n<i>{html.escape(prompt_preview)}</i>"
    )


async def _refresh_main_settings(callback: CallbackQuery, db: BotDB) -> None:
    s = await db.get_ai_settings()
    await callback.message.edit_text(
        _format_settings_text(s),
        parse_mode="HTML",
        reply_markup=ai_settings_keyboard(s),
    )


@router.message(Command("settings"))
@router.message(F.text == "⚙️ Настройки ИИ")
async def cmd_settings(message: Message, db: BotDB) -> None:
    s = await db.get_ai_settings()
    await message.answer(
        _format_settings_text(s),
        parse_mode="HTML",
        reply_markup=ai_settings_keyboard(s),
    )


@router.callback_query(F.data == "ai_cfg:back")
async def cb_back(callback: CallbackQuery, db: BotDB) -> None:
    await _refresh_main_settings(callback, db)
    await callback.answer()


@router.callback_query(F.data == "ai_cfg:menu_model")
async def cb_menu_model(callback: CallbackQuery, db: BotDB) -> None:
    s = await db.get_ai_settings()
    dynamic_models: list[str] | None = None
    if s["gemini_api_key"]:
        try:
            dynamic_models = await list_available_models(s["gemini_api_key"], force_refresh=False)
        except Exception:  # noqa: BLE001
            dynamic_models = None
    await callback.message.edit_text(
        "🤖 <b>Выберите модель Gemini:</b>",
        parse_mode="HTML",
        reply_markup=model_picker_keyboard(s["ai_model"], dynamic_models=dynamic_models or None),
    )
    await callback.answer()


@router.callback_query(F.data == "ai_cfg:load_models")
async def cb_load_models(callback: CallbackQuery, db: BotDB) -> None:
    s = await db.get_ai_settings()
    if not s["gemini_api_key"]:
        await callback.answer("Сначала задайте Gemini API Key!", show_alert=True)
        return
    await callback.answer("Загружаю список моделей...")
    try:
        models = await list_available_models(s["gemini_api_key"], force_refresh=True)
        if not models:
            await callback.message.edit_text(
                "🤖 Не удалось получить список моделей. Выберите из пресетов или введите вручную:",
                reply_markup=model_picker_keyboard(s["ai_model"]),
            )
            return
        await callback.message.edit_text(
            "🤖 <b>Актуальные модели с вашего Gemini API:</b>",
            parse_mode="HTML",
            reply_markup=model_picker_keyboard(s["ai_model"], dynamic_models=models),
        )
    except Exception as exc:  # noqa: BLE001
        await callback.message.answer(
            f"❌ Ошибка загрузки моделей: <code>{html.escape(str(exc)[:300])}</code>",
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("ai_cfg:set_model:"))
async def cb_set_model(callback: CallbackQuery, db: BotDB) -> None:
    model = (callback.data or "").split(":", 2)[2]
    await db.set_setting("ai_model", model)
    await _refresh_main_settings(callback, db)
    await callback.answer(f"Модель: {model}")


@router.callback_query(F.data == "ai_cfg:custom_model")
async def cb_custom_model(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AiSettingsForm.waiting_custom_model)
    await callback.message.answer(
        "Введите точное название модели (например <code>gemini-2.5-flash</code> или <code>gemini-2.0-flash</code>):",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AiSettingsForm.waiting_custom_model)
async def fsm_custom_model(message: Message, state: FSMContext, db: BotDB) -> None:
    model = (message.text or "").strip()
    if not model or " " in model:
        await message.answer("❌ Название модели не должно содержать пробелы.")
        return
    await db.set_setting("ai_model", model)
    await state.clear()
    s = await db.get_ai_settings()
    await message.answer(
        _format_settings_text(s),
        parse_mode="HTML",
        reply_markup=ai_settings_keyboard(s),
    )


@router.callback_query(F.data == "ai_cfg:menu_role")
async def cb_menu_role(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🎭 <b>Выберите готовую роль или задайте свою:</b>",
        parse_mode="HTML",
        reply_markup=role_picker_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ai_cfg:role:"))
async def cb_set_role(callback: CallbackQuery, db: BotDB) -> None:
    key = (callback.data or "").split(":")[2]
    prompt = ROLE_PRESETS.get(key, DEFAULT_SYSTEM_PROMPT)
    await db.set_setting("ai_system_prompt", prompt)
    await _refresh_main_settings(callback, db)
    await callback.answer("Роль обновлена!")


@router.callback_query(F.data == "ai_cfg:custom_prompt")
async def cb_custom_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AiSettingsForm.waiting_custom_prompt)
    await callback.message.answer("Отправьте свой системный промпт (инструкцию для ИИ):")
    await callback.answer()


@router.message(AiSettingsForm.waiting_custom_prompt)
async def fsm_custom_prompt(message: Message, state: FSMContext, db: BotDB) -> None:
    prompt = (message.text or "").strip()
    if not prompt:
        await message.answer("❌ Промпт не может быть пустым.")
        return
    await db.set_setting("ai_system_prompt", prompt)
    await state.clear()
    s = await db.get_ai_settings()
    await message.answer(
        _format_settings_text(s),
        parse_mode="HTML",
        reply_markup=ai_settings_keyboard(s),
    )


@router.callback_query(F.data == "ai_cfg:menu_temp")
async def cb_menu_temp(callback: CallbackQuery, db: BotDB) -> None:
    s = await db.get_ai_settings()
    await callback.message.edit_text(
        "🌡 <b>Температура ответов</b> (чем ниже — тем точнее, чем выше — тем креативнее):",
        parse_mode="HTML",
        reply_markup=temp_picker_keyboard(s["ai_temperature"]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ai_cfg:set_temp:"))
async def cb_set_temp(callback: CallbackQuery, db: BotDB) -> None:
    val = (callback.data or "").split(":")[2]
    await db.set_setting("ai_temperature", val)
    await _refresh_main_settings(callback, db)
    await callback.answer(f"Температура: {val}")


@router.callback_query(F.data == "ai_cfg:menu_hist")
async def cb_menu_hist(callback: CallbackQuery, db: BotDB) -> None:
    s = await db.get_ai_settings()
    await callback.message.edit_text(
        "💬 <b>Размер скользящего окна диалога:</b>\n"
        "(сколько последних сообщений помнит бот в текущей беседе)",
        parse_mode="HTML",
        reply_markup=history_picker_keyboard(s["ai_history_limit"]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ai_cfg:set_hist:"))
async def cb_set_hist(callback: CallbackQuery, db: BotDB) -> None:
    val = (callback.data or "").split(":")[2]
    await db.set_setting("ai_history_limit", val)
    await _refresh_main_settings(callback, db)
    await callback.answer(f"Окно диалога: {val}")


@router.callback_query(F.data == "ai_cfg:menu_tokens")
async def cb_menu_tokens(callback: CallbackQuery, db: BotDB) -> None:
    s = await db.get_ai_settings()
    await callback.message.edit_text(
        "📏 <b>Максимум токенов в одном ответе:</b>",
        parse_mode="HTML",
        reply_markup=tokens_picker_keyboard(s["ai_max_tokens"]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ai_cfg:set_tokens:"))
async def cb_set_tokens(callback: CallbackQuery, db: BotDB) -> None:
    val = (callback.data or "").split(":")[2]
    await db.set_setting("ai_max_tokens", val)
    await _refresh_main_settings(callback, db)
    await callback.answer(f"Макс. токенов: {val}")


@router.callback_query(F.data == "ai_cfg:toggle_search")
async def cb_toggle_search(callback: CallbackQuery, db: BotDB) -> None:
    s = await db.get_ai_settings()
    new_val = "0" if s["ai_google_search"] else "1"
    await db.set_setting("ai_google_search", new_val)
    await _refresh_main_settings(callback, db)
    await callback.answer("Поиск Google обновлён")


@router.callback_query(F.data == "ai_cfg:toggle_mem")
async def cb_toggle_mem(callback: CallbackQuery, db: BotDB) -> None:
    s = await db.get_ai_settings()
    new_val = "0" if s["ai_memory_enabled"] else "1"
    await db.set_setting("ai_memory_enabled", new_val)
    await _refresh_main_settings(callback, db)
    await callback.answer("Долгая память переключена")


@router.callback_query(F.data == "ai_cfg:set_key")
async def cb_set_key(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AiSettingsForm.waiting_api_key)
    await callback.message.answer(
        "Пришлите ваш <b>Gemini API Key</b> (бесплатно на aistudio.google.com).\n"
        "После сохранения я сразу удалю сообщение с ключом из чата.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AiSettingsForm.waiting_api_key)
async def fsm_save_key(message: Message, state: FSMContext, db: BotDB) -> None:
    key = (message.text or "").strip()
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    wait_msg = await message.answer("⏳ Проверяю ключ Gemini...")
    ok, info = await validate_gemini_key(key)
    await db.set_setting("gemini_api_key", key)
    await state.clear()

    s = await db.get_ai_settings()
    if ok:
        await wait_msg.edit_text(
            f"✅ Ключ проверен и сохранён!\n\n{_format_settings_text(s)}",
            parse_mode="HTML",
            reply_markup=ai_settings_keyboard(s),
        )
    else:
        await wait_msg.edit_text(
            f"⚠️ Ключ сохранён, но проверка вернула ошибку: <code>{html.escape(info)}</code>\n"
            f"(если VPS в стране без доступа к Gemini — проверьте геолокацию сервера)\n\n"
            f"{_format_settings_text(s)}",
            parse_mode="HTML",
            reply_markup=ai_settings_keyboard(s),
        )
