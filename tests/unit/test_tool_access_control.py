"""Tests for per-key MCP tool access control (B.10)."""
from __future__ import annotations

import hashlib
import os
import tempfile

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def store():
    from gateway.control.store import ControlPlaneStore
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = ControlPlaneStore(db_path)
    yield s
    s.close()
    os.unlink(db_path)


def h(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ── Store CRUD tests ──────────────────────────────────────────────────────────

def test_no_permissions_returns_none(store):
    """Key with no permissions rows → unrestricted (None)."""
    result = store.get_allowed_tools(h("new-key"))
    assert result is None


def test_set_and_get_allowed_tools(store):
    store.set_allowed_tools(h("key1"), ["web_search", "code_exec"])
    result = store.get_allowed_tools(h("key1"))
    assert set(result) == {"web_search", "code_exec"}


def test_set_empty_list_blocks_all(store):
    """Empty allow-list creates a row that marks the key as restricted but allows nothing."""
    store.set_allowed_tools(h("key2"), [])
    result = store.get_allowed_tools(h("key2"))
    # Must be [] (explicitly restricted) not None (unrestricted)
    assert result == []


def test_replace_tool_permissions(store):
    store.set_allowed_tools(h("key"), ["web_search"])
    store.set_allowed_tools(h("key"), ["code_exec"])
    result = store.get_allowed_tools(h("key"))
    assert result == ["code_exec"]
    assert "web_search" not in result


def test_set_tool_permission_upsert(store):
    store.set_tool_permission(h("key"), "web_search", True)
    store.set_tool_permission(h("key"), "web_search", False)
    # After upsert with allowed=False, get_allowed_tools excludes it
    result = store.get_allowed_tools(h("key"))
    # Row exists but allowed=0 → result is [] not None (restricted to nothing)
    assert result == []


def test_set_tool_permission_true_then_get(store):
    store.set_tool_permission(h("key"), "web_search", True)
    result = store.get_allowed_tools(h("key"))
    assert result == ["web_search"]


def test_remove_tool_permission(store):
    store.set_allowed_tools(h("key"), ["web_search", "search"])
    removed = store.remove_tool_permission(h("key"), "web_search")
    assert removed is True
    result = store.get_allowed_tools(h("key"))
    assert "web_search" not in result


def test_remove_nonexistent(store):
    removed = store.remove_tool_permission(h("key"), "nonexistent")
    assert removed is False


def test_key_isolation(store):
    """Two keys have independent tool allow-lists."""
    store.set_allowed_tools(h("admin"), ["web_search", "code_exec"])
    store.set_allowed_tools(h("readonly"), ["web_search"])
    assert set(store.get_allowed_tools(h("admin"))) == {"web_search", "code_exec"}
    assert store.get_allowed_tools(h("readonly")) == ["web_search"]


def test_remove_returns_none_after_all_removed(store):
    """After removing all tools, key still has no rows → unrestricted again
    if we only ever used remove (not set_allowed_tools with empty list)."""
    store.set_tool_permission(h("key"), "web_search", True)
    store.remove_tool_permission(h("key"), "web_search")
    # All rows deleted → None (unrestricted)
    result = store.get_allowed_tools(h("key"))
    assert result is None


# ── Filter function tests ─────────────────────────────────────────────────────

def test_filter_tools_no_control_store():
    """No control_store → all tools pass through."""
    from gateway.pipeline.orchestrator import _filter_tools_for_key
    tools = [{"function": {"name": "web_search"}}, {"function": {"name": "calc"}}]
    ctx = type("Ctx", (), {"control_store": None})()
    result = _filter_tools_for_key(tools, "any-key", ctx)
    assert result == tools


def test_filter_tools_no_api_key():
    """No api_key → all tools pass through."""
    from gateway.pipeline.orchestrator import _filter_tools_for_key
    tools = [{"function": {"name": "web_search"}}]
    ctx = type("Ctx", (), {"control_store": object()})()
    result = _filter_tools_for_key(tools, None, ctx)
    assert result == tools


def test_filter_tools_unrestricted():
    """None allowed list (unrestricted) → all tools pass through."""
    from gateway.pipeline.orchestrator import _filter_tools_for_key
    from unittest.mock import MagicMock
    mock_store = MagicMock()
    mock_store.get_allowed_tools.return_value = None
    ctx = type("Ctx", (), {"control_store": mock_store})()
    tools = [{"function": {"name": "web_search"}}, {"function": {"name": "calc"}}]
    result = _filter_tools_for_key(tools, "test-key", ctx)
    assert result == tools


def test_filter_tools_allow_list():
    """Only allowed tools pass through."""
    from gateway.pipeline.orchestrator import _filter_tools_for_key
    from unittest.mock import MagicMock
    mock_store = MagicMock()
    mock_store.get_allowed_tools.return_value = ["web_search"]
    ctx = type("Ctx", (), {"control_store": mock_store})()
    tools = [
        {"function": {"name": "web_search"}},
        {"function": {"name": "code_exec"}},
    ]
    result = _filter_tools_for_key(tools, "test-key", ctx)
    assert len(result) == 1
    assert result[0]["function"]["name"] == "web_search"


def test_filter_tools_block_all():
    """Empty allow-list blocks all tools."""
    from gateway.pipeline.orchestrator import _filter_tools_for_key
    from unittest.mock import MagicMock
    mock_store = MagicMock()
    mock_store.get_allowed_tools.return_value = []
    ctx = type("Ctx", (), {"control_store": mock_store})()
    tools = [{"function": {"name": "web_search"}}]
    result = _filter_tools_for_key(tools, "test-key", ctx)
    assert result == []


def test_filter_tools_top_level_name_field():
    """Filter also handles tools with top-level 'name' (not nested under 'function')."""
    from gateway.pipeline.orchestrator import _filter_tools_for_key
    from unittest.mock import MagicMock
    mock_store = MagicMock()
    mock_store.get_allowed_tools.return_value = ["my_tool"]
    ctx = type("Ctx", (), {"control_store": mock_store})()
    tools = [
        {"name": "my_tool", "description": "a tool"},
        {"name": "other_tool"},
    ]
    result = _filter_tools_for_key(tools, "test-key", ctx)
    assert len(result) == 1
    assert result[0]["name"] == "my_tool"


def test_filter_uses_sha256_hash():
    """Verify the filter calls get_allowed_tools with the SHA-256 of the api key."""
    from gateway.pipeline.orchestrator import _filter_tools_for_key
    from unittest.mock import MagicMock
    mock_store = MagicMock()
    mock_store.get_allowed_tools.return_value = None
    ctx = type("Ctx", (), {"control_store": mock_store})()
    api_key = "sk-test-abc123"
    _filter_tools_for_key([], api_key, ctx)
    expected_hash = hashlib.sha256(api_key.encode()).hexdigest()
    mock_store.get_allowed_tools.assert_called_once_with(expected_hash)
