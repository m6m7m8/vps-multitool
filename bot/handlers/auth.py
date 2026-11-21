"""Аутентификация: одноразовая привязка по коду, PIN-замок с анти-брутом и вайтлист."""

from __future__ import annotations

import html
from typing import Any

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.db import BotDB
from bot.keyboards import main_menu_keyboard, security_keyboard

router = Router(name="auth")


class SecForm(StatesGroup):
    waiting_new_pin = State()
    waiting_add_admin = State()


class IsBindAttempt(BaseFilter):
    async def __call__(self, message: Message, bind_attempt: bool = False) -> bool:
        return bool(bind_attempt)


class NeedsPin(BaseFilter):
    async def __call__(self, event: Any, needs_pin: bool = False) -> bool:
        return bool(needs_pin)


def _format_security_text(has_pin: bool, ttl_minutes: int, admins: list[dict[str, Any]]) -> str:
    pin_status = f"🔒 Включён (автоблокировка через <b>{ttl_minutes} мин.</b>)" if has_pin else "🔓 Выключен"
    admin_lines = "\n".join(
        f"• <code>{a['user_id']}</code>" + (f" (@{html.escape(a['username'])})" if a["username"] else "")
        for a in admins
    )
    return (
        "🛡 <b>Безопасность и доступ</b>\n\n"
        f"• <b>PIN-замок:</b> {pin_status}\n"
        f"• <b>Защита от перебора:</b> 3 ошибки → бан на 15 мин.\n\n"
        f"<b>Вайтлист админов:</b>\n{admin_lines}"
    )


@router.message(IsBindAttempt())
async def handle_first_bind(message: Message, db: BotDB) -> None:
    user = message.from_user
    if user is None:
        return
    bound = await db.try_consume_bind_code(user.id, user.username or "")
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    if not bound:
        return

    has_pin = await db.has_pin()
    extra = (
        "\n\n🔒 Установлен PIN-замок — введите ваш PIN, чтобы открыть доступ."
        if has_pin
        else "\n\nСовет: можете дополнительно включить PIN в разделе «🛡 Безопасность»."
    )
    await message.answer(
        f"✅ <b>Готово!</b> Ваш Telegram ID (<code>{user.id}</code>) привязан как владелец.\n"
        f"Повторная привязка для чужих закрыта.{extra}",
        parse_mode="HTML",
        reply_markup=None if has_pin else main_menu_keyboard(has_pin=False),
    )


@router.message(StateFilter("*"), NeedsPin())
async def handle_locked_message(message: Message, db: BotDB, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        await message.answer("🔒 <b>Бот заблокирован.</b> Введите PIN-код для входа:", parse_mode="HTML")
        return

    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    res = await db.verify_and_unlock(message.from_user.id, text)
    if res["ok"]:
        await state.clear()
        ttl = res.get("ttl_minutes", 30)
        await message.answer(
            f"🔓 <b>Доступ открыт</b> (сессия на {ttl} мин.).",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(has_pin=True),
        )
        return

    if res["locked_seconds"] > 0:
        mins = max(1, (res["locked_seconds"] + 59) // 60)
        await message.answer(
            f"⛔ Превышено число попыток. Повторите через <b>{mins} мин.</b>",
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"❌ Неверный PIN. Осталось попыток: <b>{res['attempts_left']}</b>.",
        parse_mode="HTML",
    )


@router.callback_query(StateFilter("*"), NeedsPin())
async def handle_locked_callback(callback: CallbackQuery) -> None:
    await callback.answer("🔒 Сначала введите PIN-код в чат!", show_alert=True)


@router.message(Command("start"))
async def cmd_start(message: Message, db: BotDB, state: FSMContext) -> None:
    await state.clear()
    settings = await db.get_ai_settings()
    has_pin = await db.has_pin()
    key_ok = "✅ задан" if settings["gemini_api_key"] else "⚠️ не задан (нажмите «⚙️ Настройки ИИ»)"
    await message.answer(
        "🤖 <b>VPS Multitool — Личный Gemini-бот готов!</b>\n\n"
        f"• <b>Модель:</b> <code>{html.escape(settings['ai_model'])}</code>\n"
        f"• <b>Gemini API:</b> {key_ok}\n"
        f"• <b>Память:</b> окно {settings['ai_history_limit']} сообщ. + долгие факты\n\n"
        "Просто напишите любой вопрос, пришлите <b>фото</b> или <b>документ</b>.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(has_pin=has_pin),
    )


@router.message(Command("lock"))
@router.message(F.text == "🔒 Заблокировать")
async def cmd_lock(message: Message, db: BotDB, state: FSMContext) -> None:
    await state.clear()
    if not await db.has_pin():
        await message.answer("PIN-замок сейчас выключен. Включите его в «🛡 Безопасность».")
        return
    await db.lock_session(message.from_user.id)
    await message.answer("🔒 <b>Сессия заблокирована.</b> Чтобы продолжить, введите PIN.", parse_mode="HTML")


@router.message(Command("security"))
@router.message(F.text == "🛡 Безопасность")
async def cmd_security(message: Message, db: BotDB) -> None:
    has_pin = await db.has_pin()
    ttl = int(await db.get_setting("session_ttl_minutes", "30") or "30")
    admins = await db.list_admins()
    await message.answer(
        _format_security_text(has_pin, ttl, admins),
        parse_mode="HTML",
        reply_markup=security_keyboard(has_pin, ttl),
    )


@router.callback_query(F.data == "sec:back")
async def cb_sec_back(callback: CallbackQuery, db: BotDB) -> None:
    has_pin = await db.has_pin()
    ttl = int(await db.get_setting("session_ttl_minutes", "30") or "30")
    admins = await db.list_admins()
    await callback.message.edit_text(
        _format_security_text(has_pin, ttl, admins),
        parse_mode="HTML",
        reply_markup=security_keyboard(has_pin, ttl),
    )
    await callback.answer()


@router.callback_query(F.data == "sec:set_pin")
async def cb_set_pin(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SecForm.waiting_new_pin)
    await callback.message.answer("Введите новый PIN или пароль (минимум 4 символа):")
    await callback.answer()


@router.message(SecForm.waiting_new_pin)
async def fsm_save_pin(message: Message, state: FSMContext, db: BotDB) -> None:
    pin = (message.text or "").strip()
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    if len(pin) < 4:
        await message.answer("❌ Слишком коротко — минимум 4 символа.")
        return

    await db.set_pin(pin)
    await db.verify_and_unlock(message.from_user.id, pin)
    await state.clear()
    await message.answer(
        "✅ PIN обновлён (ваше сообщение удалено для безопасности).",
        reply_markup=main_menu_keyboard(has_pin=True),
    )


@router.callback_query(F.data == "sec:off_pin")
async def cb_off_pin(callback: CallbackQuery, db: BotDB) -> None:
    await db.clear_pin()
    ttl = int(await db.get_setting("session_ttl_minutes", "30") or "30")
    admins = await db.list_admins()
    await callback.message.edit_text(
        _format_security_text(False, ttl, admins),
        parse_mode="HTML",
        reply_markup=security_keyboard(False, ttl),
    )
    await callback.message.answer("🔓 PIN-замок отключён.", reply_markup=main_menu_keyboard(has_pin=False))
    await callback.answer("PIN отключён")


@router.callback_query(F.data.startswith("sec:ttl:"))
async def cb_sec_ttl(callback: CallbackQuery, db: BotDB) -> None:
    ttl = int((callback.data or "").split(":")[2])
    await db.set_setting("session_ttl_minutes", str(ttl))
    has_pin = await db.has_pin()
    admins = await db.list_admins()
    await callback.message.edit_text(
        _format_security_text(has_pin, ttl, admins),
        parse_mode="HTML",
        reply_markup=security_keyboard(has_pin, ttl),
    )
    await callback.answer(f"Сессия: {ttl} мин.")


@router.callback_query(F.data == "sec:admins")
async def cb_sec_admins(callback: CallbackQuery, db: BotDB) -> None:
    admins = await db.list_admins()
    rows = [[InlineKeyboardButton(text="➕ Добавить Telegram ID", callback_data="sec:adm_add")]]
    if len(admins) > 1:
        for a in admins:
            if a["user_id"] != callback.from_user.id:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"🗑 Удалить {a['user_id']}",
                            callback_data=f"sec:adm_del:{a['user_id']}",
                        )
                    ]
                )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="sec:back")])
    await callback.message.edit_text(
        "👥 <b>Управление вайтлистом:</b>\nУзнать ID другого человека можно через @userinfobot.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data == "sec:adm_add")
async def cb_adm_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SecForm.waiting_add_admin)
    await callback.message.answer("Пришлите числовой Telegram ID нового админа:")
    await callback.answer()


@router.message(SecForm.waiting_add_admin)
async def fsm_adm_add(message: Message, state: FSMContext, db: BotDB) -> None:
    raw = (message.text or "").strip()
    if not raw.lstrip("-").isdigit():
        await message.answer("❌ Нужен числовой Telegram ID (например `123456789`).")
        return
    uid = int(raw)
    await db.add_admin(uid)
    await state.clear()
    await message.answer(f"✅ Пользователь <code>{uid}</code> добавлен в вайтлист.", parse_mode="HTML")


@router.callback_query(F.data.startswith("sec:adm_del:"))
async def cb_adm_del(callback: CallbackQuery, db: BotDB) -> None:
    uid = int((callback.data or "").split(":")[2])
    if uid == callback.from_user.id:
        await callback.answer("Нельзя удалить самого себя", show_alert=True)
        return
    ok = await db.remove_admin(uid)
    await callback.answer("Удалён" if ok else "Нельзя удалить последнего админа", show_alert=not ok)
    await cb_sec_admins(callback, db)
