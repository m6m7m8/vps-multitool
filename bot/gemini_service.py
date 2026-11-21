"""Обёртка над официальным SDK google-genai для гибких ответов Gemini и авто-обновления моделей."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_MODELS_CACHE: dict[str, tuple[float, list[str]]] = {}
_MODELS_CACHE_TTL = 6 * 3600
_HINT_MODEL_RE = re.compile(r"use\s+models/([a-zA-Z0-9._-]+)", re.IGNORECASE)


def _is_model_deprecated_error(err_text: str) -> bool:
    low = err_text.lower()
    return (
        "404" in low
        or "not_found" in low
        or "no longer available" in low
        or "not found for api version" in low
    )


def pick_replacement_model(
    old_model: str, error_text: str, available: list[str]
) -> str | None:
    """Подбирает актуальную замену, если старая модель была отключена Google."""
    m = _HINT_MODEL_RE.search(error_text or "")
    if m:
        hint = m.group(1).strip()
        if hint and hint != old_model and (not available or hint in available):
            return hint

    candidates = [item for item in available if item and item != old_model]
    if not candidates:
        return None

    low_old = old_model.lower()
    if "lite" in low_old:
        for item in candidates:
            if "lite" in item.lower():
                return item
    elif "flash" in low_old:
        for item in candidates:
            low_c = item.lower()
            if "flash" in low_c and "lite" not in low_c:
                return item

    return candidates[0]


def build_system_instruction(
    base_prompt: str, facts: list[str], memory_enabled: bool
) -> str:
    """Собирает системный промпт с аккуратной подмешкой долгой компактной памяти."""
    prompt = base_prompt.strip()
    if memory_enabled and facts:
        bullets = "\n".join(f"- {f}" for f in facts)
        prompt += (
            "\n\nКороткая долговременная память о пользователе "
            "(учитывай в ответах, но не пересказывай без необходимости):\n"
            f"{bullets}"
        )
    return prompt


def build_contents(
    history: list[dict[str, str]],
    user_text: str,
    media_bytes: bytes | None = None,
    media_mime: str | None = None,
) -> list[types.Content]:
    """Формирует список Content для многоходового и мультимодального диалога."""
    contents: list[types.Content] = []
    for item in history:
        role = "model" if item.get("role") == "model" else "user"
        text = (item.get("content") or "").strip()
        if text:
            contents.append(
                types.Content(role=role, parts=[types.Part.from_text(text=text)])
            )

    current_parts: list[types.Part] = []
    if media_bytes and media_mime:
        current_parts.append(types.Part.from_bytes(data=media_bytes, mime_type=media_mime))
    if user_text.strip():
        current_parts.append(types.Part.from_text(text=user_text.strip()))
    elif media_bytes:
        current_parts.append(
            types.Part.from_text(text="Проанализируй и подробно опиши прикреплённый файл/изображение.")
        )

    contents.append(types.Content(role="user", parts=current_parts))
    return contents


def _extract_grounding_sources(response: Any) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return []
        gm = getattr(candidates[0], "grounding_metadata", None)
        chunks = getattr(gm, "grounding_chunks", None) or []
        for ch in chunks:
            web = getattr(ch, "web", None)
            uri = getattr(web, "uri", None)
            title = getattr(web, "title", None) or uri
            if uri and uri not in seen:
                seen.add(uri)
                sources.append({"title": str(title), "uri": str(uri)})
    except Exception:  # noqa: BLE001
        return []
    return sources[:5]


async def generate_reply(
    api_key: str,
    settings: dict[str, Any],
    history: list[dict[str, str]],
    facts: list[str],
    user_text: str,
    media_bytes: bytes | None = None,
    media_mime: str | None = None,
) -> dict[str, Any]:
    """Отправляет запрос в Gemini API с текущими гибкими настройками."""
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY не задан. Откройте «⚙️ Настройки ИИ» -> «🔑 API-ключ» и вставьте ключ."
        )

    client = genai.Client(api_key=api_key)
    sys_instruction = build_system_instruction(
        settings["ai_system_prompt"],
        facts,
        bool(settings["ai_memory_enabled"]),
    )
    contents = build_contents(history, user_text, media_bytes, media_mime)

    tools = None
    if settings.get("ai_google_search"):
        tools = [types.Tool(google_search=types.GoogleSearch())]

    config = types.GenerateContentConfig(
        system_instruction=sys_instruction,
        temperature=float(settings.get("ai_temperature", 0.7)),
        max_output_tokens=int(settings.get("ai_max_tokens", 2048)),
        tools=tools,
    )

    current_model = settings.get("ai_model", "gemini-3.8-flash")
    switched_model: str | None = None

    try:
        response = await client.aio.models.generate_content(
            model=current_model,
            contents=contents,
            config=config,
        )
    except Exception as exc:
        if not _is_model_deprecated_error(str(exc)):
            raise
        available = await list_available_models(api_key, force_refresh=True)
        replacement = pick_replacement_model(current_model, str(exc), available)
        if not replacement:
            raise
        logger.warning(
            "Модель %s больше недоступна, автоматически переключаюсь на %s",
            current_model,
            replacement,
        )
        response = await client.aio.models.generate_content(
            model=replacement,
            contents=contents,
            config=config,
        )
        switched_model = replacement

    text = (getattr(response, "text", None) or "").strip()
    if not text:
        text = "(Модель вернула пустой ответ)"

    return {
        "text": text,
        "sources": _extract_grounding_sources(response),
        "switched_model": switched_model,
    }


async def list_available_models(api_key: str, force_refresh: bool = False) -> list[str]:
    """Возвращает актуальный список текстовых Gemini-моделей из API (с кешем на 6 часов)."""
    cleaned = api_key.strip()
    if not cleaned:
        return []

    now = time.time()
    cached = _MODELS_CACHE.get(cleaned)
    if not force_refresh and cached and (now - cached[0]) < _MODELS_CACHE_TTL:
        return list(cached[1])

    client = genai.Client(api_key=cleaned)
    found: list[str] = []
    models = await client.aio.models.list()
    async for m in models:
        actions = getattr(m, "supported_actions", None) or []
        name = (getattr(m, "name", "") or "").removeprefix("models/")
        if "generateContent" in actions and "gemini" in name and "image" not in name:
            found.append(name)
    result = list(reversed(found))[:12]
    _MODELS_CACHE[cleaned] = (now, result)
    return list(result)


async def validate_gemini_key(api_key: str) -> tuple[bool, str]:
    """Быстрая проверка валидности ключа Gemini через список моделей."""
    cleaned = api_key.strip()
    if not cleaned:
        return False, "Пустой ключ"
    try:
        client = genai.Client(api_key=cleaned)
        models = await client.aio.models.list(config={"page_size": 1})
        async for _ in models:
            return True, "Ключ валиден"
        return True, "Ключ принят"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
