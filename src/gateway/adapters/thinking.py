"""Utility: strip <think>...</think> reasoning traces from model output."""

from __future__ import annotations

import re

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


def strip_thinking_tokens(text: str) -> tuple[str, str | None]:
    """Return (clean_text, thinking_text_or_None).

    Finds all <think>...</think> blocks, removes them from the output text,
    and returns the concatenated block contents as thinking_text.
    Returns the original text unchanged if no think blocks are found.
    """
    parts = _THINK_RE.findall(text)
    if not parts:
        return text, None
    clean = _THINK_RE.sub("", text).strip()
    thinking = "\n---\n".join(p.strip() for p in parts)
    return clean, thinking
