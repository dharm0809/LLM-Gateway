"""Unit tests for Phase 28: Anthropic cache control auto-injection and cache hit detection."""

import pytest

from gateway.adapters.caching import inject_cache_control, detect_cache_hit


def test_inject_adds_cache_control_to_system():
    """System message gets cache_control on last content block."""
    messages = [
        {"role": "system", "content": [{"type": "text", "text": "You are helpful."}]},
        {"role": "user", "content": "Hello"},
    ]
    result = inject_cache_control(messages)
    system_block = result[0]["content"][-1]
    assert system_block.get("cache_control") == {"type": "ephemeral"}


def test_inject_adds_cache_control_to_string_system():
    """System message with string content gets converted and annotated."""
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
    ]
    result = inject_cache_control(messages)
    assert isinstance(result[0]["content"], list)
    assert result[0]["content"][-1].get("cache_control") == {"type": "ephemeral"}


def test_inject_preserves_user_messages():
    """User messages are not modified."""
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]
    result = inject_cache_control(messages)
    assert result[0] == messages[0]
    assert result[1] == messages[1]


def test_inject_idempotent():
    """Already has cache_control → no duplicate."""
    messages = [
        {"role": "system", "content": [
            {"type": "text", "text": "You are helpful.", "cache_control": {"type": "ephemeral"}}
        ]},
    ]
    result = inject_cache_control(messages)
    assert result[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    # No duplicate keys — same dict structure
    assert len([k for k in result[0]["content"][-1] if k == "cache_control"]) == 1


def test_detect_cache_hit_anthropic():
    """Usage with cache_read_input_tokens > 0 → cache hit."""
    usage = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_input_tokens": 80,
        "cache_creation_input_tokens": 0,
    }
    result = detect_cache_hit(usage)
    assert result["cache_hit"] is True
    assert result["cached_tokens"] == 80
    assert result["cache_creation_tokens"] == 0


def test_detect_no_cache_hit():
    """Usage without cache fields → no hit."""
    usage = {"input_tokens": 100, "output_tokens": 50}
    result = detect_cache_hit(usage)
    assert result["cache_hit"] is False
    assert result["cached_tokens"] == 0


def test_detect_cache_creation():
    """Usage with cache_creation_input_tokens > 0."""
    usage = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 100,
    }
    result = detect_cache_hit(usage)
    assert result["cache_hit"] is False
    assert result["cache_creation_tokens"] == 100
