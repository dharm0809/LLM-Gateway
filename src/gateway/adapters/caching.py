"""Prompt caching helpers for Anthropic and OpenAI providers."""

from __future__ import annotations

import copy


def inject_cache_control(messages: list[dict]) -> list[dict]:
    """Auto-inject cache_control breakpoints on system messages.

    For Anthropic: adds ``{"cache_control": {"type": "ephemeral"}}`` to the
    last content block of every system message.  String content is normalised
    to the block format first.  Already-annotated blocks are left untouched.
    """
    result = []
    for msg in messages:
        if msg.get("role") != "system":
            result.append(msg)
            continue

        msg = copy.deepcopy(msg)
        content = msg["content"]

        # Normalise string → block list
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
            msg["content"] = content

        if isinstance(content, list) and len(content) > 0:
            last = content[-1]
            if "cache_control" not in last:
                last["cache_control"] = {"type": "ephemeral"}

        result.append(msg)
    return result


def detect_cache_hit(usage: dict) -> dict:
    """Detect Anthropic / OpenAI cache hits from a usage dict.

    Returns a normalised summary::

        {
            "cache_hit": bool,
            "cached_tokens": int,
            "cache_creation_tokens": int,
        }
    """
    # Anthropic fields
    cached = usage.get("cache_read_input_tokens", 0) or 0
    creation = usage.get("cache_creation_input_tokens", 0) or 0

    # OpenAI field (prompt_tokens_details.cached_tokens)
    details = usage.get("prompt_tokens_details") or {}
    if isinstance(details, dict):
        cached = cached or (details.get("cached_tokens", 0) or 0)

    return {
        "cache_hit": cached > 0,
        "cached_tokens": cached,
        "cache_creation_tokens": creation,
    }
