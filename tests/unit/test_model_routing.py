"""Unit tests for model routing table (Fix 1)."""

from __future__ import annotations

import json
import pytest

from gateway.adapters import OpenAIAdapter, OllamaAdapter
from gateway.adapters.anthropic import AnthropicAdapter
from gateway.adapters.huggingface import HuggingFaceAdapter
from gateway.pipeline.orchestrator import _resolve_adapter, _make_adapter_for_route


# ---------------------------------------------------------------------------
# _make_adapter_for_route
# ---------------------------------------------------------------------------

def test_make_adapter_openai():
    route = {"provider": "openai", "url": "https://api.openai.com", "key": "sk-test"}
    adapter = _make_adapter_for_route(route)
    assert isinstance(adapter, OpenAIAdapter)


def test_make_adapter_ollama():
    route = {"provider": "ollama", "url": "http://localhost:11434", "key": ""}
    adapter = _make_adapter_for_route(route)
    assert isinstance(adapter, OllamaAdapter)


def test_make_adapter_anthropic():
    route = {"provider": "anthropic", "url": "https://api.anthropic.com", "key": "key"}
    adapter = _make_adapter_for_route(route)
    assert isinstance(adapter, AnthropicAdapter)


def test_make_adapter_huggingface():
    route = {"provider": "huggingface", "url": "https://hf.co", "key": "hf-key"}
    adapter = _make_adapter_for_route(route)
    assert isinstance(adapter, HuggingFaceAdapter)


def test_make_adapter_unknown_provider_returns_none():
    route = {"provider": "unknown", "url": "http://x.com", "key": ""}
    adapter = _make_adapter_for_route(route)
    assert adapter is None


# ---------------------------------------------------------------------------
# _resolve_adapter — model routing table
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Clear the lru_cache on get_settings between tests."""
    from gateway import config as cfg
    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


def _set_routing_env(monkeypatch, rules: list[dict]) -> None:
    monkeypatch.setenv("WALACOR_MODEL_ROUTING_JSON", json.dumps(rules))
    monkeypatch.setenv("WALACOR_SKIP_GOVERNANCE", "true")
    monkeypatch.setenv("WALACOR_GATEWAY_TENANT_ID", "test")
    monkeypatch.setenv("WALACOR_CONTROL_PLANE_URL", "http://cp")
    # Ensure path-based routing defaults to OpenAI (not ollama) when no routing table matches
    monkeypatch.setenv("WALACOR_GATEWAY_PROVIDER", "openai")


def test_gpt_pattern_routes_to_openai(monkeypatch):
    _set_routing_env(monkeypatch, [
        {"pattern": "gpt-*", "provider": "openai", "url": "https://api.openai.com", "key": "sk-x"},
    ])
    adapter = _resolve_adapter("/v1/chat/completions", "gpt-4")
    assert isinstance(adapter, OpenAIAdapter)


def test_llama_pattern_routes_to_ollama(monkeypatch):
    _set_routing_env(monkeypatch, [
        {"pattern": "llama*", "provider": "ollama", "url": "http://localhost:11434", "key": ""},
    ])
    adapter = _resolve_adapter("/v1/chat/completions", "llama3.2")
    assert isinstance(adapter, OllamaAdapter)


def test_case_insensitive_pattern_matching(monkeypatch):
    _set_routing_env(monkeypatch, [
        {"pattern": "GPT-*", "provider": "openai", "url": "https://api.openai.com", "key": "sk-x"},
    ])
    # model_id is lowercased before matching, pattern is lowercased too
    adapter = _resolve_adapter("/v1/chat/completions", "gpt-4-turbo")
    assert isinstance(adapter, OpenAIAdapter)


def test_unknown_model_falls_through_to_path_routing(monkeypatch):
    _set_routing_env(monkeypatch, [
        {"pattern": "gpt-*", "provider": "openai", "url": "https://api.openai.com", "key": "sk-x"},
    ])
    # "claude-3" doesn't match "gpt-*" → falls back to path-based routing
    adapter = _resolve_adapter("/v1/messages", "claude-3-opus")
    assert isinstance(adapter, AnthropicAdapter)


def test_no_model_hint_uses_path_routing(monkeypatch):
    _set_routing_env(monkeypatch, [
        {"pattern": "gpt-*", "provider": "openai", "url": "https://api.openai.com", "key": "sk-x"},
    ])
    # No model hint → path-based routing for /v1/messages
    adapter = _resolve_adapter("/v1/messages", "")
    assert isinstance(adapter, AnthropicAdapter)


def test_first_matching_route_wins(monkeypatch):
    _set_routing_env(monkeypatch, [
        {"pattern": "gpt-4*", "provider": "openai", "url": "https://api.openai.com", "key": "sk-x"},
        {"pattern": "gpt-*", "provider": "ollama", "url": "http://localhost:11434", "key": ""},
    ])
    # gpt-4 matches first rule → OpenAI
    adapter = _resolve_adapter("/v1/chat/completions", "gpt-4")
    assert isinstance(adapter, OpenAIAdapter)


def test_empty_routing_table_uses_path_routing(monkeypatch):
    _set_routing_env(monkeypatch, [])
    adapter = _resolve_adapter("/v1/chat/completions", "gpt-4")
    # No routing table → path routing → OpenAI by default
    assert isinstance(adapter, OpenAIAdapter)


def test_empty_pattern_does_not_match(monkeypatch):
    """A route with an empty pattern string should not match any model."""
    _set_routing_env(monkeypatch, [
        {"pattern": "", "provider": "openai", "url": "https://api.openai.com", "key": "sk-x"},
    ])
    adapter = _resolve_adapter("/v1/chat/completions", "gpt-4")
    # Empty pattern: fnmatch("gpt-4", "") returns False → falls through to path routing
    assert isinstance(adapter, OpenAIAdapter)


def test_unknown_provider_in_matching_route_falls_through_to_next_route(monkeypatch):
    """A route whose pattern matches but provider is unknown is skipped; next valid route wins."""
    _set_routing_env(monkeypatch, [
        {"pattern": "gpt-*", "provider": "unknown_provider", "url": "http://x.com", "key": ""},
        {"pattern": "gpt-*", "provider": "openai", "url": "https://api.openai.com", "key": "sk-x"},
    ])
    # First route matches "gpt-4" but _make_adapter_for_route returns None → skipped
    # Second route also matches and produces a valid OpenAI adapter
    adapter = _resolve_adapter("/v1/chat/completions", "gpt-4")
    assert isinstance(adapter, OpenAIAdapter)


# ---------------------------------------------------------------------------
# _peek_model_id
# ---------------------------------------------------------------------------

# Pin anyio to asyncio for AsyncMock compatibility
@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.mark.anyio
async def test_peek_model_id_extracts_model_field():
    from unittest.mock import AsyncMock, MagicMock
    from gateway.pipeline.orchestrator import _peek_model_id

    request = MagicMock()
    request.body = AsyncMock(return_value=b'{"model": "gpt-4", "messages": []}')

    result = await _peek_model_id(request)
    assert result == "gpt-4"


@pytest.mark.anyio
async def test_peek_model_id_missing_field_returns_empty():
    from unittest.mock import AsyncMock, MagicMock
    from gateway.pipeline.orchestrator import _peek_model_id

    request = MagicMock()
    request.body = AsyncMock(return_value=b'{"messages": []}')

    result = await _peek_model_id(request)
    assert result == ""


@pytest.mark.anyio
async def test_peek_model_id_invalid_json_returns_empty():
    from unittest.mock import AsyncMock, MagicMock
    from gateway.pipeline.orchestrator import _peek_model_id

    request = MagicMock()
    request.body = AsyncMock(return_value=b"not json at all")

    result = await _peek_model_id(request)
    assert result == ""


@pytest.mark.anyio
async def test_peek_model_id_none_value_returns_empty():
    from unittest.mock import AsyncMock, MagicMock
    from gateway.pipeline.orchestrator import _peek_model_id

    request = MagicMock()
    request.body = AsyncMock(return_value=b'{"model": null}')

    result = await _peek_model_id(request)
    assert result == ""


# ---------------------------------------------------------------------------
# handle_request → _peek_model_id gate (T3)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_handle_request_calls_peek_when_model_routes_set(monkeypatch):
    """_peek_model_id is awaited when model_routes is non-empty, and its result reaches _resolve_adapter."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from gateway.pipeline.orchestrator import handle_request

    _set_routing_env(monkeypatch, [
        {"pattern": "gpt-*", "provider": "openai", "url": "https://api.openai.com", "key": "sk-x"},
    ])

    mock_request = MagicMock()
    mock_request.method = "POST"
    mock_request.url.path = "/v1/chat/completions"
    mock_request.state = MagicMock()

    captured = {}

    def fake_resolve(path, model_id=""):
        captured["model_id"] = model_id
        return None  # triggers 404 early return

    with patch("gateway.pipeline.orchestrator._peek_model_id", new=AsyncMock(return_value="gpt-4")) as mock_peek, \
         patch("gateway.pipeline.orchestrator._resolve_adapter", side_effect=fake_resolve):
        resp = await handle_request(mock_request)

    mock_peek.assert_awaited_once()
    assert captured.get("model_id") == "gpt-4"
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_handle_request_skips_peek_when_no_model_routes(monkeypatch):
    """_peek_model_id is NOT called when model_routes is empty (short-circuit)."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from gateway.pipeline.orchestrator import handle_request

    _set_routing_env(monkeypatch, [])  # empty routing table

    mock_request = MagicMock()
    mock_request.method = "POST"
    mock_request.url.path = "/v1/chat/completions"
    mock_request.state = MagicMock()

    with patch("gateway.pipeline.orchestrator._peek_model_id", new=AsyncMock(return_value="gpt-4")) as mock_peek, \
         patch("gateway.pipeline.orchestrator._resolve_adapter", return_value=None):
        await handle_request(mock_request)

    mock_peek.assert_not_awaited()
