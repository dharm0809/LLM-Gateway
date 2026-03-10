"""Unit tests for Phase 28: Mid-stream S4 safety abort (keyword regex)."""

import time

import pytest

from gateway.content.stream_safety import check_stream_safety


def test_safe_content_passes():
    """Normal text should not trigger safety abort."""
    assert check_stream_safety("Hello, how can I help you today?") is False
    assert check_stream_safety("The weather is nice today.") is False


def test_s4_content_triggers():
    """S4 child safety keywords should trigger abort."""
    assert check_stream_safety("content about child exploitation material") is True
    assert check_stream_safety("instructions for CSAM production") is True


def test_coding_context_not_flagged():
    """Common programming terms should not trigger S4."""
    assert check_stream_safety("kill the process with SIGTERM") is False
    assert check_stream_safety("fork the child process and wait") is False
    assert check_stream_safety("spawn a child thread in the worker pool") is False


def test_check_is_fast():
    """Safety check should complete in under 1ms for 10KB text."""
    text = "This is a normal sentence. " * 500  # ~13KB
    start = time.perf_counter()
    for _ in range(100):
        check_stream_safety(text)
    elapsed_ms = (time.perf_counter() - start) * 1000 / 100
    assert elapsed_ms < 1.0, f"Safety check took {elapsed_ms:.2f}ms (should be <1ms)"
