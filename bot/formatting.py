"""Форматирование Markdown-ответов Gemini в безопасный Telegram HTML."""

from __future__ import annotations

import html
import re

_FENCED_CODE_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def markdown_to_telegram_html(text: str) -> str:
    """Конвертирует базовый Markdown в валидный HTML Telegram без поломки тегов."""
    if not text:
        return ""

    placeholders: list[str] = []

    def _stash(html_fragment: str) -> str:
        idx = len(placeholders)
        placeholders.append(html_fragment)
        return f"\x00TGPH{idx}\x00"

    def _repl_fenced(match: re.Match[str]) -> str:
        code = html.escape(match.group(1).rstrip("\n"), quote=False)
        return _stash(f"<pre><code>{code}</code></pre>")

    def _repl_inline(match: re.Match[str]) -> str:
        code = html.escape(match.group(1), quote=False)
        return _stash(f"<code>{code}</code>")

    working = _FENCED_CODE_RE.sub(_repl_fenced, text)
    working = _INLINE_CODE_RE.sub(_repl_inline, working)
    working = html.escape(working, quote=False)
    working = _BOLD_RE.sub(r"<b>\1</b>", working)

    for idx, fragment in enumerate(placeholders):
        working = working.replace(f"\x00TGPH{idx}\x00", fragment)
    return working


def split_telegram_text(text: str, max_len: int = 3900) -> list[str]:
    """Разбивает длинный текст на части по абзацам/строкам, не превышая лимит Telegram."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_len:
        cut = remaining.rfind("\n\n", 0, max_len)
        if cut < max_len // 3:
            cut = remaining.rfind("\n", 0, max_len)
        if cut < max_len // 3:
            cut = remaining.rfind(" ", 0, max_len)
        if cut <= 0:
            cut = max_len
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    if remaining:
        chunks.append(remaining)
    return chunks
