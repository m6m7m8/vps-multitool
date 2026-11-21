"""Клавиатуры главного меню, гибких настроек ИИ, памяти и безопасности."""

from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu_keyboard(has_pin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="💬 Новый диалог"), KeyboardButton(text="🧠 Память")],
        [KeyboardButton(text="⚙️ Настройки ИИ"), KeyboardButton(text="🛡 Безопасность")],
    ]
    if has_pin:
        rows.append([KeyboardButton(text="🔒 Заблокировать")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def ai_settings_keyboard(s: dict[str, Any]) -> InlineKeyboardMarkup:
    search_lbl = "🔍 Поиск Google: ✅ Вкл" if s["ai_google_search"] else "🔍 Поиск Google: ❌ Выкл"
    mem_lbl = "🧠 Долгая память: ✅ Вкл" if s["ai_memory_enabled"] else "🧠 Долгая память: ❌ Выкл"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🤖 Модель: {s['ai_model']}", callback_data="ai_cfg:menu_model"),
            ],
            [
                InlineKeyboardButton(text="🎭 Роль / Промпт", callback_data="ai_cfg:menu_role"),
                InlineKeyboardButton(text=f"🌡 Temp: {s['ai_temperature']}", callback_data="ai_cfg:menu_temp"),
            ],
            [
                InlineKeyboardButton(text=f"💬 История: {s['ai_history_limit']} сообщ.", callback_data="ai_cfg:menu_hist"),
                InlineKeyboardButton(text=f"📏 Токены: {s['ai_max_tokens']}", callback_data="ai_cfg:menu_tokens"),
            ],
            [
                InlineKeyboardButton(text=search_lbl, callback_data="ai_cfg:toggle_search"),
                InlineKeyboardButton(text=mem_lbl, callback_data="ai_cfg:toggle_mem"),
            ],
            [
                InlineKeyboardButton(text="🔑 Задать / сменить Gemini API Key", callback_data="ai_cfg:set_key"),
            ],
        ]
    )


def model_picker_keyboard(
    current: str, dynamic_models: list[str] | None = None
) -> InlineKeyboardMarkup:
    if dynamic_models:
        models = [(m, m) for m in dynamic_models]
    else:
        models = [
            ("gemini-3.8-flash (рекомендуемая)", "gemini-3.8-flash"),
            ("gemini-3.5-flash-lite (быстрая)", "gemini-3.5-flash-lite"),
            ("gemini-4-argon (мощная)", "gemini-4-argon"),
            ("gemini-4-neon (топовая)", "gemini-4-neon"),
        ]
    rows = [
        [
            InlineKeyboardButton(
                text=("✅ " if val == current else "") + label,
                callback_data=f"ai_cfg:set_model:{val}",
            )
        ]
        for label, val in models
    ]
    rows.append([InlineKeyboardButton(text="🔄 Загрузить модели с API", callback_data="ai_cfg:load_models")])
    rows.append([InlineKeyboardButton(text="✏️ Ввести свою модель", callback_data="ai_cfg:custom_model")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ai_cfg:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def role_picker_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🤖 Ассистент", callback_data="ai_cfg:role:assistant"),
                InlineKeyboardButton(text="💻 Программист", callback_data="ai_cfg:role:coder"),
            ],
            [
                InlineKeyboardButton(text="📝 Редактор", callback_data="ai_cfg:role:editor"),
                InlineKeyboardButton(text="🌍 Переводчик", callback_data="ai_cfg:role:translator"),
            ],
            [InlineKeyboardButton(text="✏️ Написать свой промпт", callback_data="ai_cfg:custom_prompt")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="ai_cfg:back")],
        ]
    )


def temp_picker_keyboard(current: float) -> InlineKeyboardMarkup:
    temps = [("0.2 (строго)", "0.2"), ("0.7 (баланс)", "0.7"), ("1.0 (живой)", "1.0"), ("1.3 (креатив)", "1.3")]
    rows = [
        [
            InlineKeyboardButton(
                text=("✅ " if abs(float(val) - current) < 0.05 else "") + label,
                callback_data=f"ai_cfg:set_temp:{val}",
            )
            for label, val in temps[:2]
        ],
        [
            InlineKeyboardButton(
                text=("✅ " if abs(float(val) - current) < 0.05 else "") + label,
                callback_data=f"ai_cfg:set_temp:{val}",
            )
            for label, val in temps[2:]
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="ai_cfg:back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def history_picker_keyboard(current: int) -> InlineKeyboardMarkup:
    opts = [("0 (без контекста)", "0"), ("6", "6"), ("10", "10"), ("20", "20")]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("✅ " if int(val) == current else "") + label,
                    callback_data=f"ai_cfg:set_hist:{val}",
                )
                for label, val in opts
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="ai_cfg:back")],
        ]
    )


def tokens_picker_keyboard(current: int) -> InlineKeyboardMarkup:
    opts = ["1024", "2048", "4096", "8192"]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("✅ " if int(val) == current else "") + val,
                    callback_data=f"ai_cfg:set_tokens:{val}",
                )
                for val in opts
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="ai_cfg:back")],
        ]
    )


def memory_keyboard(facts: list[dict[str, Any]], enabled: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="➕ Запомнить факт", callback_data="mem:add"),
            InlineKeyboardButton(
                text="🧠 Память: ✅ Вкл" if enabled else "🧠 Память: ❌ Выкл",
                callback_data="mem:toggle",
            ),
        ]
    ]
    for f in facts:
        preview = f["fact"][:32] + ("…" if len(f["fact"]) > 32 else "")
        rows.append(
            [InlineKeyboardButton(text=f"🗑 {preview}", callback_data=f"mem:del:{f['id']}")]
        )
    if facts:
        rows.append([InlineKeyboardButton(text="🧹 Очистить всю память", callback_data="mem:clear")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def security_keyboard(has_pin: bool, ttl_minutes: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="🔑 Сменить PIN" if has_pin else "🔑 Включить PIN-замок",
                callback_data="sec:set_pin",
            )
        ]
    ]
    if has_pin:
        rows.append([InlineKeyboardButton(text="🔓 Отключить PIN", callback_data="sec:off_pin")])
        rows.append(
            [
                InlineKeyboardButton(
                    text=("✅ " if ttl_minutes == val else "") + lbl,
                    callback_data=f"sec:ttl:{val}",
                )
                for lbl, val in [("15м", 15), ("30м", 30), ("2ч", 120), ("24ч", 1440)]
            ]
        )
    rows.append([InlineKeyboardButton(text="👥 Вайтлист (Telegram ID)", callback_data="sec:admins")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
