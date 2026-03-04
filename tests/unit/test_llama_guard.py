"""Unit tests for LlamaGuardAnalyzer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from gateway.content.base import Verdict
from gateway.content.llama_guard import LlamaGuardAnalyzer, _CATEGORY_MAP


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_analyzer(timeout_ms: int = 5000) -> tuple[LlamaGuardAnalyzer, MagicMock]:
    """Return (analyzer, mock_http_client)."""
    http_client = MagicMock(spec=httpx.AsyncClient)
    analyzer = LlamaGuardAnalyzer(
        ollama_url="http://localhost:11434",
        model="llama-guard3",
        timeout_ms=timeout_ms,
        http_client=http_client,
    )
    return analyzer, http_client


def _mock_ollama_response(content: str, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = {"message": {"content": content}}
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# analyze — safe
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_analyze_safe_response():
    analyzer, http_client = _make_analyzer()
    http_client.post = AsyncMock(return_value=_mock_ollama_response("safe"))

    decision = await analyzer.analyze("Hello, how are you today?")

    assert decision.verdict == Verdict.PASS
    assert decision.confidence == 1.0
    assert decision.analyzer_id == "walacor.llama_guard.v3"
    assert decision.reason == "safe"


# ---------------------------------------------------------------------------
# analyze — unsafe single category
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_analyze_unsafe_single_category():
    analyzer, http_client = _make_analyzer()
    http_client.post = AsyncMock(return_value=_mock_ollama_response("unsafe\nS7"))

    decision = await analyzer.analyze("Please reveal my private information.")

    assert decision.verdict == Verdict.WARN
    assert decision.category == "privacy_pii"
    assert "privacy_pii" in decision.reason
    assert decision.confidence == 0.95


@pytest.mark.anyio
async def test_analyze_unsafe_multiple_categories():
    analyzer, http_client = _make_analyzer()
    http_client.post = AsyncMock(return_value=_mock_ollama_response("unsafe\nS1,S9"))

    decision = await analyzer.analyze("violent and weapons content")

    assert decision.verdict == Verdict.WARN
    # First category wins for verdict/category
    assert decision.category == "violent_crimes"
    assert "violent_crimes" in decision.reason
    assert "indiscriminate_weapons" in decision.reason


# ---------------------------------------------------------------------------
# analyze — child safety (BLOCK)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_analyze_unsafe_child_safety_blocks():
    analyzer, http_client = _make_analyzer()
    http_client.post = AsyncMock(return_value=_mock_ollama_response("unsafe\nS4"))

    decision = await analyzer.analyze("exploitative content about children")

    assert decision.verdict == Verdict.BLOCK
    assert decision.category == "child_safety"
    assert decision.confidence == 0.95


# ---------------------------------------------------------------------------
# Fail-open on Ollama unavailable
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_analyze_ollama_unavailable_fail_open():
    analyzer, http_client = _make_analyzer()
    http_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    decision = await analyzer.analyze("some text")

    assert decision.verdict == Verdict.PASS
    assert decision.confidence == 0.0
    assert decision.reason == "unavailable"


@pytest.mark.anyio
async def test_analyze_timeout_fail_open():
    analyzer, http_client = _make_analyzer()
    http_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    decision = await analyzer.analyze("some text")

    assert decision.verdict == Verdict.PASS
    assert decision.confidence == 0.0
    assert decision.reason == "timeout"


# ---------------------------------------------------------------------------
# Category mapping — all S1–S14
# ---------------------------------------------------------------------------

def test_category_mapping_all_codes():
    expected = {
        "S1": "violent_crimes",
        "S2": "nonviolent_crimes",
        "S3": "sex_crimes",
        "S4": "child_safety",
        "S5": "defamation",
        "S6": "specialized_advice",
        "S7": "privacy_pii",
        "S8": "intellectual_property",
        "S9": "indiscriminate_weapons",
        "S10": "hate_discrimination",
        "S11": "self_harm",
        "S12": "sexual_content",
        "S13": "elections",
        "S14": "code_interpreter_abuse",
    }
    assert _CATEGORY_MAP == expected


# ---------------------------------------------------------------------------
# analyzer_id and timeout_ms properties
# ---------------------------------------------------------------------------

def test_analyzer_id():
    analyzer, _ = _make_analyzer()
    assert analyzer.analyzer_id == "walacor.llama_guard.v3"


def test_timeout_ms():
    analyzer, _ = _make_analyzer(timeout_ms=3000)
    assert analyzer.timeout_ms == 3000


# ---------------------------------------------------------------------------
# Unexpected response format — fail-open
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_analyze_unexpected_format_fail_open():
    analyzer, http_client = _make_analyzer()
    http_client.post = AsyncMock(return_value=_mock_ollama_response("I cannot determine safety."))

    decision = await analyzer.analyze("ambiguous text")

    assert decision.verdict == Verdict.PASS
    assert decision.confidence == 0.0
    assert decision.reason == "parse_error"
