"""Юнит-тесты для VPS Multitool v2 (безопасность, память, настройки Gemini и форматирование)."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.config import AppConfig  # noqa: E402
from bot.db import BotDB, MAX_MEMORY_FACTS  # noqa: E402
from bot.formatting import markdown_to_telegram_html, split_telegram_text  # noqa: E402
from bot.gemini_service import build_contents, build_system_instruction, pick_replacement_model  # noqa: E402
from bot.security import extract_bind_candidate, hash_pin, verify_pin  # noqa: E402


def _make_cfg(db_path: str, *, admin_ids: set[int] | None = None, bind_code: str = "", initial_pin: str = "") -> AppConfig:
    return AppConfig(
        bot_token="123:test",
        admin_ids=admin_ids or set(),
        bind_code=bind_code,
        initial_pin=initial_pin,
        gemini_api_key="AIzaTestKey123456",
        gemini_model="gemini-3.8-flash",
        db_path=db_path,
    )


class TestSecurityAndPin(unittest.TestCase):
    def test_pbkdf2_pin_hash_verify(self) -> None:
        h, salt = hash_pin("8492")
        self.assertTrue(verify_pin("8492", h, salt))
        self.assertFalse(verify_pin("0000", h, salt))

    def test_bind_candidate_extraction(self) -> None:
        self.assertEqual(extract_bind_candidate("849201"), "849201")
        self.assertEqual(extract_bind_candidate("/start 849201"), "849201")
        self.assertEqual(extract_bind_candidate(None), "")

    def test_one_time_bind_and_whitelist(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                db = BotDB(str(Path(tmp) / "t.sqlite3"))
                await db.init(_make_cfg(db.db_path, bind_code="123456"))

                self.assertFalse(await db.has_any_admin())
                self.assertTrue(await db.try_consume_bind_code(111, "first"))
                self.assertTrue(await db.is_admin(111))
                # Второй пользователь не может использовать одноразовую привязку
                self.assertFalse(await db.try_consume_bind_code(222, "second"))
                self.assertFalse(await db.is_admin(222))

                # Добавление и удаление второго админа; последнего удалить нельзя
                await db.add_admin(222, "friend")
                self.assertTrue(await db.is_admin(222))
                self.assertTrue(await db.remove_admin(222))
                self.assertFalse(await db.remove_admin(111))

        asyncio.run(scenario())

    def test_pin_antibrute_lockout_and_session(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                db = BotDB(str(Path(tmp) / "t.sqlite3"))
                await db.init(_make_cfg(db.db_path, admin_ids={101}, initial_pin="4321"))

                self.assertTrue(await db.has_pin())
                self.assertFalse(await db.is_unlocked(101, now=1000))

                # 2 неверные попытки
                r1 = await db.verify_and_unlock(101, "0000", now=1000)
                self.assertFalse(r1["ok"])
                self.assertEqual(r1["attempts_left"], 2)
                self.assertEqual(r1["locked_seconds"], 0)

                r2 = await db.verify_and_unlock(101, "1111", now=1005)
                self.assertFalse(r2["ok"])
                self.assertEqual(r2["attempts_left"], 1)

                # 3-я ошибка -> бан на 15 минут
                r3 = await db.verify_and_unlock(101, "2222", now=1010)
                self.assertFalse(r3["ok"])
                self.assertEqual(r3["attempts_left"], 0)
                self.assertEqual(r3["locked_seconds"], 900)

                # Даже с верным PIN внутри окна бана — отказ
                r_locked = await db.verify_and_unlock(101, "4321", now=1200)
                self.assertFalse(r_locked["ok"])
                self.assertGreater(r_locked["locked_seconds"], 0)

                # После истечения бана — верный PIN открывает сессию
                r_ok = await db.verify_and_unlock(101, "4321", now=2000)
                self.assertTrue(r_ok["ok"])
                self.assertTrue(await db.is_unlocked(101, now=2500))
                self.assertFalse(await db.is_unlocked(101, now=5000))  # TTL 30m = 1800s

                await db.lock_session(101)
                self.assertFalse(await db.is_unlocked(101, now=2100))

        asyncio.run(scenario())


class TestMemoryAndSettings(unittest.TestCase):
    def test_settings_history_and_long_term_memory(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                db = BotDB(str(Path(tmp) / "t.sqlite3"))
                await db.init(_make_cfg(db.db_path, admin_ids={1}))

                s = await db.get_ai_settings()
                self.assertEqual(s["ai_model"], "gemini-3.8-flash")
                self.assertEqual(s["ai_history_limit"], 10)
                self.assertFalse(s["ai_google_search"])
                self.assertTrue(s["ai_memory_enabled"])

                await db.set_setting("ai_model", "gemini-4-neon")
                await db.set_setting("ai_google_search", "1")
                s2 = await db.get_ai_settings()
                self.assertEqual(s2["ai_model"], "gemini-4-neon")
                self.assertTrue(s2["ai_google_search"])

                # Скользящее окно диалога
                for i in range(6):
                    await db.add_history(1, "user", f"u{i}")
                recent = await db.get_recent_history(1, limit=4)
                self.assertEqual([m["content"] for m in recent], ["u2", "u3", "u4", "u5"])

                # Долгая память (лимит 25 фактов и сохранение после clear_history)
                for i in range(MAX_MEMORY_FACTS + 5):
                    await db.add_memory_fact(1, f"Факт {i}")
                facts = await db.list_memory_facts(1)
                self.assertEqual(len(facts), MAX_MEMORY_FACTS)
                self.assertEqual(facts[-1]["fact"], f"Факт {MAX_MEMORY_FACTS + 4}")

                await db.clear_history(1)
                self.assertEqual(await db.get_recent_history(1, limit=10), [])
                self.assertEqual(len(await db.list_memory_facts(1)), MAX_MEMORY_FACTS)

        asyncio.run(scenario())


class TestGeminiAndFormatting(unittest.TestCase):
    def test_system_instruction_and_multimodal_contents(self) -> None:
        sys_on = build_system_instruction("База", ["Имя: Руслан", "ОС: Ubuntu"], True)
        self.assertIn("Имя: Руслан", sys_on)
        sys_off = build_system_instruction("База", ["Имя: Руслан"], False)
        self.assertEqual(sys_off, "База")

        contents = build_contents(
            [{"role": "user", "content": "Привет"}, {"role": "model", "content": "Привет!"}],
            "Что на фото?",
            media_bytes=b"\x89PNG\r\n",
            media_mime="image/png",
        )
        self.assertEqual(len(contents), 3)
        self.assertEqual(contents[0].role, "user")
        self.assertEqual(contents[1].role, "model")
        self.assertEqual(contents[2].role, "user")
        self.assertEqual(len(contents[2].parts), 2)

    def test_markdown_to_html_and_split(self) -> None:
        md = "Привет **мир** <script> & `inline`\n```python\nprint('ok')\n```"
        out = markdown_to_telegram_html(md)
        self.assertIn("<b>мир</b>", out)
        self.assertIn("&lt;script&gt; &amp;", out)
        self.assertIn("<code>inline</code>", out)
        self.assertIn("<pre><code>print('ok')</code></pre>", out)

        long_txt = ("Параграф текста.\n\n" * 400).strip()
        chunks = split_telegram_text(long_txt, max_len=1000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))

    def test_pick_replacement_model(self) -> None:
        avail = ["gemini-4-neon", "gemini-4-argon", "gemini-3.8-flash", "gemini-3.5-flash-lite"]
        # 1. Прямая подсказка в тексте ошибки Google
        err = "This model models/gemini-2.5-flash is no longer available. Please update your code to use models/gemini-3.8-flash"
        self.assertEqual(pick_replacement_model("gemini-2.5-flash", err, avail), "gemini-3.8-flash")
        # 2. Подбор по классу без подсказки (flash -> flash, flash-lite -> flash-lite, pro -> newest)
        self.assertEqual(pick_replacement_model("gemini-2.5-flash", "404 NOT_FOUND", avail), "gemini-3.8-flash")
        self.assertEqual(pick_replacement_model("gemini-2.5-flash-lite", "404 NOT_FOUND", avail), "gemini-3.5-flash-lite")
        self.assertEqual(pick_replacement_model("gemini-2.5-pro", "404 NOT_FOUND", avail), "gemini-4-neon")
        # 3. Защита от зацикливания на самой себе
        self.assertIsNone(pick_replacement_model("gemini-4-neon", "404", ["gemini-4-neon"]))


if __name__ == "__main__":
    unittest.main()
