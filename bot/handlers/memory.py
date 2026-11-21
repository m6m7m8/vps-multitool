"""Управление компактной долговременной памятью пользователя."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.db import BotDB, MAX_MEMORY_FACTS
from bot.keyboards import memory_keyboard

router = Router(name="memory")


class MemoryForm(StatesGroup):
    waiting_fact = State()


def _format_memory_text(facts: list[dict], enabled: bool) -> str:
    status = "✅ Включена" if enabled else "❌ Выключена"
    lines = [
        f"🧠 <b>Долгая память</b> ({status}, сохранено {len(facts)}/{MAX_MEMORY_FACTS}):",
        "<i>Эти короткие факты подставляются в каждый запрос даже после /clear.</i>\n",
    ]
    if not facts:
        lines.append("Пока пусто. Добавьте факт через <code>/remember текст</code> или кнопкой ниже.")
    else:
        for i, item in enumerate(facts, start=1):
            lines.append(f"{i}. {html.escape(item['fact'])}")
    return "\n".join(lines)


@router.message(Command("memory"))
@router.message(F.text == "🧠 Память")
async def cmd_memory(message: Message, db: BotDB) -> None:
    facts = await db.list_memory_facts(message.from_user.id)
    s = await db.get_ai_settings()
    await message.answer(
        _format_memory_text(facts, s["ai_memory_enabled"]),
        parse_mode="HTML",
        reply_markup=memory_keyboard(facts, s["ai_memory_enabled"]),
    )


@router.message(Command("remember"))
async def cmd_remember(message: Message, db: BotDB) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Использование: <code>/remember Пиши ответы максимально кратко</code>", parse_mode="HTML")
        return
    await db.add_memory_fact(message.from_user.id, parts[1])
    facts = await db.list_memory_facts(message.from_user.id)
    s = await db.get_ai_settings()
    await message.answer(
        f"✅ Запомнил!\n\n{_format_memory_text(facts, s['ai_memory_enabled'])}",
        parse_mode="HTML",
        reply_markup=memory_keyboard(facts, s["ai_memory_enabled"]),
    )


@router.callback_query(F.data == "mem:add")
async def cb_mem_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MemoryForm.waiting_fact)
    await callback.message.answer(
        "Напишите короткий факт, который бот должен помнить (например: <code>Мой основной VPS на Ubuntu 22.04</code>):",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(MemoryForm.waiting_fact)
async def fsm_mem_add(message: Message, state: FSMContext, db: BotDB) -> None:
    fact = (message.text or "").strip()
    if not fact:
        await message.answer("❌ Факт не может быть пустым.")
        return
    await db.add_memory_fact(message.from_user.id, fact)
    await state.clear()
    facts = await db.list_memory_facts(message.from_user.id)
    s = await db.get_ai_settings()
    await message.answer(
        f"✅ Запомнил!\n\n{_format_memory_text(facts, s['ai_memory_enabled'])}",
        parse_mode="HTML",
        reply_markup=memory_keyboard(facts, s["ai_memory_enabled"]),
    )


@router.callback_query(F.data == "mem:toggle")
async def cb_mem_toggle(callback: CallbackQuery, db: BotDB) -> None:
    s = await db.get_ai_settings()
    new_val = not s["ai_memory_enabled"]
    await db.set_setting("ai_memory_enabled", "1" if new_val else "0")
    facts = await db.list_memory_facts(callback.from_user.id)
    await callback.message.edit_text(
        _format_memory_text(facts, new_val),
        parse_mode="HTML",
        reply_markup=memory_keyboard(facts, new_val),
    )
    await callback.answer("Память переключена")


@router.callback_query(F.data.startswith("mem:del:"))
async def cb_mem_del(callback: CallbackQuery, db: BotDB) -> None:
    fact_id = int((callback.data or "").split(":")[2])
    await db.delete_memory_fact(callback.from_user.id, fact_id)
    facts = await db.list_memory_facts(callback.from_user.id)
    s = await db.get_ai_settings()
    await callback.message.edit_text(
        _format_memory_text(facts, s["ai_memory_enabled"]),
        parse_mode="HTML",
        reply_markup=memory_keyboard(facts, s["ai_memory_enabled"]),
    )
    await callback.answer("Факт удалён")


@router.callback_query(F.data == "mem:clear")
async def cb_mem_clear(callback: CallbackQuery, db: BotDB) -> None:
    await db.clear_memory_facts(callback.from_user.id)
    s = await db.get_ai_settings()
    await callback.message.edit_text(
        _format_memory_text([], s["ai_memory_enabled"]),
        parse_mode="HTML",
        reply_markup=memory_keyboard([], s["ai_memory_enabled"]),
    )
    await callback.answer("Долгая память очищена")
