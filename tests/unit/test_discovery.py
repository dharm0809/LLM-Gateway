"""Unit tests for the model discovery module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.control.discovery import (
    discover_provider_models,
    _discover_ollama,
    _discover_openai,
)


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


def _make_settings(ollama_url="", openai_url="", openai_key=""):
    s = MagicMock()
    s.provider_ollama_url = ollama_url
    s.provider_openai_url = openai_url
    s.provider_openai_key = openai_key
    return s


def _mock_response(status_code, json_data):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


# ── Ollama discovery ──────────────────────────────────────────


@pytest.mark.anyio
async def test_discover_ollama_success():
    http = AsyncMock()
    http.get.return_value = _mock_response(200, {
        "models": [
            {"name": "qwen3:4b", "size": 1234},
            {"name": "gemma3:1b", "size": 5678},
        ]
    })
    result = await _discover_ollama("http://localhost:11434", http)
    assert len(result) == 2
    assert result[0] == {"model_id": "qwen3:4b", "provider": "ollama", "source": "ollama_tags"}
    assert result[1] == {"model_id": "gemma3:1b", "provider": "ollama", "source": "ollama_tags"}
    http.get.assert_called_once()
    assert "/api/tags" in http.get.call_args[0][0]


@pytest.mark.anyio
async def test_discover_ollama_non_200():
    http = AsyncMock()
    http.get.return_value = _mock_response(500, {})
    result = await _discover_ollama("http://localhost:11434", http)
    assert result == []


@pytest.mark.anyio
async def test_discover_ollama_connection_error():
    http = AsyncMock()
    http.get.side_effect = Exception("Connection refused")
    result = await _discover_ollama("http://localhost:11434", http)
    assert result == []


@pytest.mark.anyio
async def test_discover_ollama_empty_models():
    http = AsyncMock()
    http.get.return_value = _mock_response(200, {"models": []})
    result = await _discover_ollama("http://localhost:11434", http)
    assert result == []


# ── OpenAI discovery ──────────────────────────────────────────


@pytest.mark.anyio
async def test_discover_openai_success():
    http = AsyncMock()
    http.get.return_value = _mock_response(200, {
        "data": [
            {"id": "gpt-4o", "object": "model"},
            {"id": "gpt-4o-mini", "object": "model"},
        ]
    })
    result = await _discover_openai("https://api.openai.com", "sk-test", http)
    assert len(result) == 2
    assert result[0] == {"model_id": "gpt-4o", "provider": "openai", "source": "openai_models"}
    # Verify auth header was passed
    call_kwargs = http.get.call_args
    assert "Bearer sk-test" in str(call_kwargs)


@pytest.mark.anyio
async def test_discover_openai_non_200():
    http = AsyncMock()
    http.get.return_value = _mock_response(401, {"error": "invalid key"})
    result = await _discover_openai("https://api.openai.com", "bad-key", http)
    assert result == []


@pytest.mark.anyio
async def test_discover_openai_connection_error():
    http = AsyncMock()
    http.get.side_effect = Exception("Timeout")
    result = await _discover_openai("https://api.openai.com", "sk-test", http)
    assert result == []


# ── Combined discovery ────────────────────────────────────────


@pytest.mark.anyio
async def test_discover_both_providers():
    http = AsyncMock()

    def fake_get(url, **kwargs):
        if "/api/tags" in url:
            return _mock_response(200, {"models": [{"name": "llama3:8b"}]})
        if "/v1/models" in url:
            return _mock_response(200, {"data": [{"id": "gpt-4o"}]})
        return _mock_response(404, {})

    http.get.side_effect = fake_get

    settings = _make_settings(
        ollama_url="http://localhost:11434",
        openai_url="https://api.openai.com",
        openai_key="sk-test",
    )
    result = await discover_provider_models(settings, http)
    assert len(result) == 2
    providers = {m["provider"] for m in result}
    assert providers == {"ollama", "openai"}


@pytest.mark.anyio
async def test_discover_no_providers():
    http = AsyncMock()
    settings = _make_settings()
    result = await discover_provider_models(settings, http)
    assert result == []


@pytest.mark.anyio
async def test_discover_ollama_only():
    http = AsyncMock()
    http.get.return_value = _mock_response(200, {"models": [{"name": "phi3:mini"}]})
    settings = _make_settings(ollama_url="http://localhost:11434")
    result = await discover_provider_models(settings, http)
    assert len(result) == 1
    assert result[0]["provider"] == "ollama"


@pytest.mark.anyio
async def test_discover_partial_failure():
    """One provider fails, the other succeeds — results include the successful one."""
    http = AsyncMock()

    def fake_get(url, **kwargs):
        if "/api/tags" in url:
            raise Exception("Connection refused")
        if "/v1/models" in url:
            return _mock_response(200, {"data": [{"id": "gpt-4o"}]})
        return _mock_response(404, {})

    http.get.side_effect = fake_get
    settings = _make_settings(
        ollama_url="http://localhost:11434",
        openai_url="https://api.openai.com",
        openai_key="sk-test",
    )
    result = await discover_provider_models(settings, http)
    assert len(result) == 1
    assert result[0]["provider"] == "openai"


@pytest.mark.anyio
async def test_discover_ollama_trailing_slash():
    """URL with trailing slash should not double-slash."""
    http = AsyncMock()
    http.get.return_value = _mock_response(200, {"models": [{"name": "test:latest"}]})
    result = await _discover_ollama("http://localhost:11434/", http)
    assert len(result) == 1
    url_called = http.get.call_args[0][0]
    assert "//" not in url_called.replace("http://", "")
