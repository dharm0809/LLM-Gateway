"""Unit tests for thinking-mode strip utility and ModelResponse.thinking_content field."""

from __future__ import annotations

import pytest

from gateway.adapters.thinking import strip_thinking_tokens
from gateway.adapters.base import ModelResponse


# ---------------------------------------------------------------------------
# strip_thinking_tokens
# ---------------------------------------------------------------------------

def test_strip_single_think_block():
    text = "<think>this is my reasoning</think>Final answer"
    clean, thinking = strip_thinking_tokens(text)
    assert clean == "Final answer"
    assert thinking == "this is my reasoning"


def test_strip_multiple_think_blocks():
    text = "<think>step 1</think>middle<think>step 2</think>end"
    clean, thinking = strip_thinking_tokens(text)
    assert "middle" in clean
    assert "end" in clean
    assert "step 1" in thinking
    assert "step 2" in thinking
    assert "---" in thinking  # separator between blocks


def test_strip_no_think_block_unchanged():
    text = "Just a plain response with no think blocks."
    clean, thinking = strip_thinking_tokens(text)
    assert clean == text
    assert thinking is None


def test_strip_empty_string():
    clean, thinking = strip_thinking_tokens("")
    assert clean == ""
    assert thinking is None


def test_strip_think_only_no_content_after():
    text = "<think>all reasoning, no visible answer</think>"
    clean, thinking = strip_thinking_tokens(text)
    assert clean == ""
    assert thinking == "all reasoning, no visible answer"


def test_strip_case_insensitive():
    text = "<THINK>upper case</THINK>content"
    clean, thinking = strip_thinking_tokens(text)
    assert clean == "content"
    assert thinking == "upper case"


def test_strip_multiline_think_block():
    text = "<think>\nline 1\nline 2\n</think>The answer is 42."
    clean, thinking = strip_thinking_tokens(text)
    assert clean == "The answer is 42."
    assert "line 1" in thinking
    assert "line 2" in thinking


# ---------------------------------------------------------------------------
# ModelResponse.thinking_content field
# ---------------------------------------------------------------------------

def test_thinking_content_defaults_to_none():
    resp = ModelResponse(content="hello", usage=None, raw_body=b"")
    assert resp.thinking_content is None


def test_thinking_content_populated():
    resp = ModelResponse(
        content="The answer is 42.",
        usage=None,
        raw_body=b"",
        thinking_content="let me reason through this",
    )
    assert resp.thinking_content == "let me reason through this"
    assert resp.content == "The answer is 42."


def test_thinking_content_independent_of_content():
    """thinking_content does not bleed into content."""
    resp = ModelResponse(
        content="clean answer",
        usage=None,
        raw_body=b"",
        thinking_content="private reasoning",
    )
    assert "private reasoning" not in resp.content
    assert resp.thinking_content == "private reasoning"
