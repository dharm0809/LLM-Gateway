# Phases 23–29 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Walacor Gateway a complete standalone product with 13 new features across 7 phases.

**Architecture:** Compliance-moat-first build order. Quick wins (Phase 23) unblock OpenAI-compatible UIs, then Compliance Export (Phase 24) establishes the product's unique differentiator before building infrastructure features (Phases 25-29). All features extend the existing 8-step governance pipeline without breaking the completeness invariant or dual-write pattern.

**Tech Stack:** Python 3.11+, Starlette/ASGI, SQLite WAL, WeasyPrint (PDF), tenacity (retry), pybreaker (circuit breakers), React/Vite dashboard, Canvas rendering.

**Design doc:** `docs/plans/2026-03-10-standalone-gateway-features-design.md`

---

## Phase 23: Quick Wins (2-3 days)

### Task 23.1: GET /v1/models Endpoint

**Files:**
- Create: `src/gateway/models_api.py`
- Modify: `src/gateway/main.py` (add route + init)
- Test: `tests/unit/test_models_api.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_models_api.py
import pytest
from unittest.mock import MagicMock, patch
from starlette.testclient import TestClient

@pytest.mark.anyio
async def test_models_endpoint_returns_openai_format():
    """GET /v1/models returns OpenAI-compatible model list."""
    from gateway.models_api import list_models
    from starlette.requests import Request

    mock_store = MagicMock()
    mock_store.list_attestations.return_value = [
        {"model_id": "qwen3:4b", "provider": "ollama", "status": "active"},
        {"model_id": "gpt-4o", "provider": "openai", "status": "active"},
    ]

    with patch("gateway.models_api.get_pipeline_context") as mock_ctx:
        mock_ctx.return_value.control_store = mock_store
        mock_ctx.return_value.skip_governance = False

        scope = {"type": "http", "method": "GET", "path": "/v1/models", "query_string": b"", "headers": []}
        request = Request(scope)
        response = await list_models(request)
        data = response.body  # JSONResponse

    import json
    body = json.loads(data)
    assert body["object"] == "list"
    assert len(body["data"]) == 2
    assert body["data"][0]["id"] == "qwen3:4b"
    assert body["data"][0]["object"] == "model"
    assert body["data"][0]["owned_by"] == "ollama"


@pytest.mark.anyio
async def test_models_endpoint_no_control_store_uses_discovery():
    """When no control store, returns empty list (no crash)."""
    from gateway.models_api import list_models
    from starlette.requests import Request

    with patch("gateway.models_api.get_pipeline_context") as mock_ctx:
        mock_ctx.return_value.control_store = None
        mock_ctx.return_value.skip_governance = True

        scope = {"type": "http", "method": "GET", "path": "/v1/models", "query_string": b"", "headers": []}
        request = Request(scope)
        response = await list_models(request)

    import json
    body = json.loads(response.body)
    assert body["object"] == "list"
    assert body["data"] == []


@pytest.mark.anyio
async def test_models_excludes_revoked():
    """Revoked attestations are excluded from model list."""
    from gateway.models_api import list_models
    from starlette.requests import Request

    mock_store = MagicMock()
    mock_store.list_attestations.return_value = [
        {"model_id": "qwen3:4b", "provider": "ollama", "status": "active"},
        {"model_id": "bad-model", "provider": "ollama", "status": "revoked"},
    ]

    with patch("gateway.models_api.get_pipeline_context") as mock_ctx:
        mock_ctx.return_value.control_store = mock_store
        mock_ctx.return_value.skip_governance = False

        scope = {"type": "http", "method": "GET", "path": "/v1/models", "query_string": b"", "headers": []}
        request = Request(scope)
        response = await list_models(request)

    import json
    body = json.loads(response.body)
    assert len(body["data"]) == 1
    assert body["data"][0]["id"] == "qwen3:4b"
```

**Step 2: Run test to verify it fails**

Run: `cd src && python -m pytest ../tests/unit/test_models_api.py -v`
Expected: FAIL with ImportError (module not found)

**Step 3: Write minimal implementation**

```python
# src/gateway/models_api.py
"""GET /v1/models — OpenAI-compatible model listing."""
import time
from starlette.requests import Request
from starlette.responses import JSONResponse
from gateway.pipeline.context import get_pipeline_context


async def list_models(request: Request) -> JSONResponse:
    ctx = get_pipeline_context()
    models = []

    if ctx.control_store:
        attestations = ctx.control_store.list_attestations()
        for att in attestations:
            if att.get("status") != "active":
                continue
            models.append({
                "id": att["model_id"],
                "object": "model",
                "created": int(time.time()),
                "owned_by": att.get("provider", "unknown"),
            })

    return JSONResponse({
        "object": "list",
        "data": models,
    })
```

**Step 4: Wire route in main.py**

Add to route table (after existing lineage routes, before catch-all):
```python
Route("/v1/models", list_models, methods=["GET"]),
```

Add import at top of main.py:
```python
from gateway.models_api import list_models
```

Ensure `/v1/models` is skipped by completeness_middleware (add to skip list in `middleware/completeness.py`).

**Step 5: Run tests to verify they pass**

Run: `cd src && python -m pytest ../tests/unit/test_models_api.py -v`
Expected: 3 PASS

**Step 6: Commit**

```bash
git add src/gateway/models_api.py tests/unit/test_models_api.py src/gateway/main.py src/gateway/middleware/completeness.py
git commit -m "feat(phase-23): add GET /v1/models endpoint (OpenAI-compatible)"
```

---

### Task 23.2: Governance Response Headers (Non-Streaming)

**Files:**
- Modify: `src/gateway/pipeline/orchestrator.py` (add headers to response)
- Test: `tests/unit/test_governance_headers.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_governance_headers.py
import pytest

def test_governance_headers_added_to_non_streaming_response():
    """Non-streaming responses include X-Walacor-* governance headers."""
    from starlette.responses import JSONResponse
    from gateway.pipeline.orchestrator import _add_governance_headers

    response = JSONResponse({"choices": [{"message": {"content": "hello"}}]})
    _add_governance_headers(
        response,
        execution_id="exec-123",
        attestation_id="self-attested:qwen3:4b",
        chain_seq=5,
        policy_result="allowed",
    )

    assert response.headers["x-walacor-execution-id"] == "exec-123"
    assert response.headers["x-walacor-attestation-id"] == "self-attested:qwen3:4b"
    assert response.headers["x-walacor-chain-seq"] == "5"
    assert response.headers["x-walacor-policy-result"] == "allowed"


def test_governance_headers_missing_values_omitted():
    """Headers with None values are not added."""
    from starlette.responses import JSONResponse
    from gateway.pipeline.orchestrator import _add_governance_headers

    response = JSONResponse({})
    _add_governance_headers(response, execution_id="exec-1", attestation_id=None, chain_seq=None, policy_result="allowed")

    assert "x-walacor-execution-id" in response.headers
    assert "x-walacor-attestation-id" not in response.headers
    assert "x-walacor-chain-seq" not in response.headers
```

**Step 2: Run test to verify it fails**

Run: `cd src && python -m pytest ../tests/unit/test_governance_headers.py -v`
Expected: FAIL (function not found)

**Step 3: Implement `_add_governance_headers` in orchestrator.py**

Add helper function (near top of orchestrator.py, after imports):

```python
def _add_governance_headers(response, execution_id=None, attestation_id=None, chain_seq=None, policy_result=None):
    """Add X-Walacor-* governance metadata headers to response."""
    if execution_id:
        response.headers["x-walacor-execution-id"] = str(execution_id)
    if attestation_id:
        response.headers["x-walacor-attestation-id"] = str(attestation_id)
    if chain_seq is not None:
        response.headers["x-walacor-chain-seq"] = str(chain_seq)
    if policy_result:
        response.headers["x-walacor-policy-result"] = str(policy_result)
```

Then call `_add_governance_headers(response, ...)` in `handle_request()` before returning the response, passing the execution_id, attestation_id, chain sequence number, and policy_result that are already available in the function scope.

**Step 4: Run tests**

Run: `cd src && python -m pytest ../tests/unit/test_governance_headers.py -v`
Expected: 2 PASS

**Step 5: Commit**

```bash
git add src/gateway/pipeline/orchestrator.py tests/unit/test_governance_headers.py
git commit -m "feat(phase-23): add X-Walacor-* governance response headers"
```

---

### Task 23.3: Governance SSE Event (Streaming)

**Files:**
- Modify: `src/gateway/pipeline/forwarder.py` (inject governance event after [DONE])
- Modify: `src/gateway/pipeline/orchestrator.py` (pass governance metadata to streamer)
- Test: `tests/unit/test_governance_sse.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_governance_sse.py
import pytest
import json

def test_governance_sse_event_format():
    """Governance SSE event has correct format."""
    from gateway.pipeline.forwarder import build_governance_sse_event

    event = build_governance_sse_event(
        execution_id="exec-123",
        attestation_id="self-attested:qwen3:4b",
        chain_seq=5,
        policy_result="allowed",
    )

    assert event.startswith(b"event: governance\n")
    assert b"data: " in event
    assert event.endswith(b"\n\n")

    # Extract JSON from data line
    lines = event.decode().strip().split("\n")
    data_line = [l for l in lines if l.startswith("data: ")][0]
    payload = json.loads(data_line[6:])
    assert payload["execution_id"] == "exec-123"
    assert payload["attestation_id"] == "self-attested:qwen3:4b"
    assert payload["chain_seq"] == 5
    assert payload["policy_result"] == "allowed"
```

**Step 2: Run test to verify it fails**

Run: `cd src && python -m pytest ../tests/unit/test_governance_sse.py -v`
Expected: FAIL (function not found)

**Step 3: Implement in forwarder.py**

```python
def build_governance_sse_event(execution_id=None, attestation_id=None, chain_seq=None, policy_result=None):
    """Build an SSE event with governance metadata, sent after data: [DONE]."""
    import json
    payload = {}
    if execution_id:
        payload["execution_id"] = execution_id
    if attestation_id:
        payload["attestation_id"] = attestation_id
    if chain_seq is not None:
        payload["chain_seq"] = chain_seq
    if policy_result:
        payload["policy_result"] = policy_result
    return f"event: governance\ndata: {json.dumps(payload)}\n\n".encode()
```

The governance SSE event is injected in the streaming generator in `stream_with_tee()`. After the upstream stream completes (after the final chunk), yield the governance event. The governance metadata (execution_id, attestation_id, etc.) must be passed through from the orchestrator via a mutable dict or callback — use `request.state.governance_meta` dict set by the orchestrator before streaming begins, read by the generator after the stream completes.

**Step 4: Run tests**

Run: `cd src && python -m pytest ../tests/unit/test_governance_sse.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/gateway/pipeline/forwarder.py src/gateway/pipeline/orchestrator.py tests/unit/test_governance_sse.py
git commit -m "feat(phase-23): add governance SSE event for streaming responses"
```

---

### Task 23.4: SSE Keepalives

**Files:**
- Modify: `src/gateway/pipeline/forwarder.py` (add keepalive task)
- Test: `tests/unit/test_sse_keepalive.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_sse_keepalive.py
import pytest
import asyncio

@pytest.mark.anyio
async def test_keepalive_produces_sse_comments():
    """Keepalive task produces SSE comment lines."""
    from gateway.pipeline.forwarder import sse_keepalive_generator

    chunks = []
    gen = sse_keepalive_generator(interval_seconds=0.05)  # fast for test
    task = asyncio.create_task(_collect(gen, chunks, max_items=3))
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk == b": keepalive\n\n"


async def _collect(gen, out, max_items):
    count = 0
    async for item in gen:
        out.append(item)
        count += 1
        if count >= max_items:
            break
```

**Step 2: Run test to verify it fails**

Run: `cd src && python -m pytest ../tests/unit/test_sse_keepalive.py -v`
Expected: FAIL (function not found)

**Step 3: Implement**

Add to `forwarder.py`:

```python
async def sse_keepalive_generator(interval_seconds: float = 15.0):
    """Yield SSE comment keepalives at a regular interval."""
    while True:
        await asyncio.sleep(interval_seconds)
        yield b": keepalive\n\n"
```

In `stream_with_tee()`, start a keepalive task that feeds keepalive bytes into the response stream. The implementation merges the upstream provider chunks with keepalive chunks using `asyncio.Queue` — the keepalive task puts keepalive bytes into the queue at intervals, and the upstream reader puts provider chunks. The generator reads from the queue and yields both.

Alternative simpler approach: track `last_chunk_time` and in the generator loop, if more than 15s since last chunk, yield a keepalive before the next `await` on upstream. This avoids the queue complexity.

**Step 4: Run tests**

Run: `cd src && python -m pytest ../tests/unit/test_sse_keepalive.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `cd src && python -m pytest ../tests/ -v --timeout=60`
Expected: All existing tests still pass (207+ pass)

**Step 6: Commit**

```bash
git add src/gateway/pipeline/forwarder.py tests/unit/test_sse_keepalive.py
git commit -m "feat(phase-23): add SSE keepalive heartbeats (15s interval)"
```

---

### Task 23.5: Update CLAUDE.md + docs

**Files:**
- Modify: `CLAUDE.md` (add Phase 23 section)
- Modify: `README.md` (document new features)

Add Phase 23 section to CLAUDE.md documenting:
- GET /v1/models endpoint and its data source (control plane attestations)
- X-Walacor-* response headers (non-streaming + streaming governance SSE event)
- SSE keepalive behavior (15s interval, `: keepalive\n\n` comments)
- `/v1/models` skips completeness_middleware and api_key_middleware

**Commit:**
```bash
git add CLAUDE.md README.md
git commit -m "docs(phase-23): document /v1/models, governance headers, SSE keepalives"
```

---

## Phase 24: Compliance Export API (2-3 weeks)

### Task 24.1: Add WeasyPrint Dependency

**Files:**
- Modify: `pyproject.toml` (add `[compliance]` optional extra)

**Step 1: Add optional dependency**

```toml
[project.optional-dependencies]
compliance = ["weasyprint>=62.0"]
```

**Step 2: Verify install**

Run: `pip install -e ".[compliance]"`
Expected: WeasyPrint installed successfully

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build(phase-24): add weasyprint optional dependency for compliance export"
```

---

### Task 24.2: Compliance Query Layer (LineageReader Extensions)

**Files:**
- Modify: `src/gateway/lineage/reader.py` (add 4 new query methods)
- Test: `tests/unit/test_compliance_queries.py`

**New methods on LineageReader:**

```python
def get_compliance_summary(self, start: str, end: str) -> dict:
    """Aggregate stats for compliance report: total requests, pass/fail rates,
    model usage, content analysis summary, chain integrity."""

def get_execution_export(self, start: str, end: str, limit: int = 10000) -> list[dict]:
    """Full execution records for date range (JSON/CSV export)."""

def get_attestation_summary(self, start: str, end: str) -> list[dict]:
    """Model attestation inventory with usage counts in period."""

def get_chain_verification_report(self, start: str, end: str) -> list[dict]:
    """Run verify_chain for all sessions active in period, return results."""
```

**Write tests first** (TDD):
- `test_compliance_summary_counts_by_disposition` — verify allowed/denied/blocked counts
- `test_compliance_summary_empty_range` — no data returns zeroed stats
- `test_execution_export_respects_date_range` — only returns records in range
- `test_execution_export_limit` — honors limit param
- `test_attestation_summary_groups_by_model` — groups by model_id with request counts
- `test_chain_verification_report_includes_all_sessions` — runs verify for each active session

Use the same test setup pattern as `test_lineage_reader.py` — create a temporary SQLite WAL database, insert test records, query via LineageReader.

**Commit:**
```bash
git add src/gateway/lineage/reader.py tests/unit/test_compliance_queries.py
git commit -m "feat(phase-24): add compliance query methods to LineageReader"
```

---

### Task 24.3: Compliance Export Endpoints (JSON + CSV)

**Files:**
- Create: `src/gateway/compliance/__init__.py`
- Create: `src/gateway/compliance/api.py`
- Modify: `src/gateway/main.py` (add routes)
- Modify: `src/gateway/middleware/completeness.py` (skip `/v1/compliance`)
- Test: `tests/unit/test_compliance_api.py`

**Endpoints:**

```python
async def compliance_export(request: Request) -> Response:
    """GET /v1/compliance/export?format=json|csv&framework=eu_ai_act|nist|soc2|iso42001&start=YYYY-MM-DD&end=YYYY-MM-DD"""
```

- `format=json` → returns JSONResponse with full compliance data
- `format=csv` → returns StreamingResponse with `text/csv` content-type, `Content-Disposition: attachment` header
- `format=pdf` → (Task 24.5, separate)

**JSON structure:**

```json
{
  "report": {
    "generated_at": "2026-03-10T15:00:00Z",
    "period": {"start": "2026-03-01", "end": "2026-03-10"},
    "framework": "eu_ai_act",
    "gateway_version": "0.23.0"
  },
  "summary": {
    "total_requests": 1234,
    "allowed": 1200, "denied": 34,
    "models_used": ["qwen3:4b", "gpt-4o"],
    "content_analysis": {"pii_detections": 5, "toxicity_flags": 2, "llama_guard_blocks": 1},
    "chain_integrity": {"sessions_verified": 50, "all_valid": true}
  },
  "attestations": [...],
  "executions": [...],
  "framework_mapping": {...}
}
```

**CSV columns:**
`execution_id, timestamp, session_id, model_id, provider, attestation_id, policy_result, prompt_hash, response_hash, latency_ms, prompt_tokens, completion_tokens, total_tokens, content_pii, content_toxicity, chain_seq, chain_hash`

**Tests:**
- `test_json_export_returns_valid_structure`
- `test_csv_export_has_correct_headers`
- `test_export_requires_start_and_end_params`
- `test_export_skips_completeness_middleware`
- `test_export_requires_api_key` (uses api_key_middleware)

**Commit:**
```bash
git add src/gateway/compliance/ tests/unit/test_compliance_api.py src/gateway/main.py src/gateway/middleware/completeness.py
git commit -m "feat(phase-24): add compliance export API (JSON + CSV)"
```

---

### Task 24.4: Framework Mappings

**Files:**
- Create: `src/gateway/compliance/frameworks.py`
- Test: `tests/unit/test_compliance_frameworks.py`

**Each framework mapping is a function** that takes the compliance summary data and returns a structured mapping:

```python
def map_eu_ai_act(summary: dict, attestations: list, executions: list) -> dict:
    """Map gateway data to EU AI Act Article 12 + Annex IV requirements."""
    return {
        "framework": "EU AI Act",
        "articles": {
            "article_12": {
                "title": "Record-Keeping",
                "status": "compliant" | "partial" | "non_compliant",
                "evidence": [...],
                "requirements": [
                    {"id": "12.1", "description": "Automatic recording of events",
                     "status": "compliant", "evidence_ref": "execution_records"},
                    ...
                ]
            },
            "article_19": {...},
        }
    }

def map_nist_ai_rmf(summary, attestations, executions) -> dict
def map_soc2(summary, attestations, executions) -> dict
def map_iso42001(summary, attestations, executions) -> dict
```

**Tests:**
- `test_eu_ai_act_mapping_has_article_12` — verify Article 12 requirements are present
- `test_nist_mapping_has_four_functions` — Govern, Map, Measure, Manage
- `test_soc2_mapping_has_trust_criteria` — CC7.2, CC7.3, CC8.1
- `test_mapping_with_broken_chain_shows_non_compliant`

**Commit:**
```bash
git add src/gateway/compliance/frameworks.py tests/unit/test_compliance_frameworks.py
git commit -m "feat(phase-24): add regulatory framework mappings (EU AI Act, NIST, SOC 2, ISO 42001)"
```

---

### Task 24.5: PDF Report Generation

**Files:**
- Create: `src/gateway/compliance/pdf_report.py`
- Create: `src/gateway/compliance/templates/report.html`
- Create: `src/gateway/compliance/templates/styles.css`
- Test: `tests/unit/test_compliance_pdf.py`

**Approach:** HTML template with Jinja2 + CSS → WeasyPrint renders to PDF.

**Template sections:**
1. Cover page (Walacor branding, date range, framework)
2. Executive summary (key metrics, pass/fail gauges)
3. Attestation inventory table
4. Policy evaluation summary (by policy, pass/warn/block counts)
5. Content analysis summary (PII/toxicity/Llama Guard)
6. Session chain integrity verification results
7. Token/cost breakdown by model
8. Framework-specific compliance mapping (checklist format)
9. Appendix: sample execution records with full hash chains

**PDF endpoint integration:** `format=pdf` in `compliance_export()` calls `generate_pdf_report()` which returns bytes. Response is `Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": ...})`.

**Tests:**
- `test_pdf_generation_produces_valid_pdf` — check output starts with `%PDF-`
- `test_pdf_contains_executive_summary` — parse PDF text for expected headings
- `test_pdf_with_empty_data_no_crash` — zero records produces valid (empty) report

Note: PDF tests require WeasyPrint installed. Use `pytest.importorskip("weasyprint")` to skip gracefully when not available.

**Commit:**
```bash
git add src/gateway/compliance/pdf_report.py src/gateway/compliance/templates/ tests/unit/test_compliance_pdf.py
git commit -m "feat(phase-24): add PDF compliance report generation via WeasyPrint"
```

---

### Task 24.6: Dashboard Compliance View

**Files:**
- Create: `src/gateway/lineage/dashboard/src/views/Compliance.jsx`
- Modify: `src/gateway/lineage/dashboard/src/App.jsx` (add tab)
- Modify: `src/gateway/lineage/dashboard/src/api.js` (add export function)
- Modify: `src/gateway/lineage/dashboard/src/styles/index.css` (compliance styles)

**UI:**
- Date range picker (start/end date inputs)
- Framework selector (dropdown: EU AI Act, NIST AI RMF, SOC 2, ISO 42001)
- Format selector (JSON, CSV, PDF buttons)
- Preview panel showing summary stats before download
- Download triggers browser download via `<a href="..." download>`

**Commit:**
```bash
git add src/gateway/lineage/dashboard/src/
git commit -m "feat(phase-24): add Compliance export tab to dashboard"
```

---

### Task 24.7: Phase 24 Integration Test + Docs

**Files:**
- Modify: `CLAUDE.md` (Phase 24 section)

**Integration test:** Create a temporary WAL with known test data, start the compliance endpoints, verify JSON/CSV/PDF exports contain expected data. Run chain verification across all test sessions.

Run full suite: `cd src && python -m pytest ../tests/ -v --timeout=60`
Expected: All tests pass (220+ total)

**Commit:**
```bash
git add CLAUDE.md tests/
git commit -m "docs(phase-24): document compliance export API and framework mappings"
```

---

## Phase 25: Fallback / Retry / Circuit Breakers + Model Groups (8-10 days)

### Task 25.1: Add tenacity + pybreaker Dependencies

**Files:**
- Modify: `pyproject.toml`

Add to core dependencies (not optional — resilience is essential):
```toml
"tenacity>=9.0",
"pybreaker>=1.2",
```

**Commit:**
```bash
git add pyproject.toml
git commit -m "build(phase-25): add tenacity and pybreaker dependencies"
```

---

### Task 25.2: Model Groups Config + Weighted Selection

**Files:**
- Create: `src/gateway/routing/__init__.py`
- Create: `src/gateway/routing/balancer.py`
- Modify: `src/gateway/config.py` (add `model_groups_json` field)
- Test: `tests/unit/test_balancer.py`

**Config:**
```python
# config.py — new field
model_groups_json: str = Field("", env="WALACOR_MODEL_GROUPS_JSON")
# Format: {"gpt-4": [{"url": "https://...", "key": "sk-1", "weight": 7}, {"url": "https://...", "key": "sk-2", "weight": 3}]}
```

**ModelGroup dataclass:**
```python
@dataclass
class Endpoint:
    url: str
    api_key: str
    weight: float = 1.0
    healthy: bool = True
    cooldown_until: float = 0.0  # timestamp

@dataclass
class ModelGroup:
    pattern: str
    endpoints: list[Endpoint]

class LoadBalancer:
    def __init__(self, groups: list[ModelGroup]):
        ...

    def select_endpoint(self, model_id: str) -> Endpoint | None:
        """Weighted random selection from healthy endpoints matching model_id."""

    def mark_unhealthy(self, model_id: str, endpoint_url: str, cooldown_seconds: float = 30.0):
        """Mark endpoint as unhealthy with cooldown."""

    def check_health(self):
        """Re-enable endpoints past their cooldown."""
```

**Tests:**
- `test_weighted_selection_distributes_proportionally` — 1000 selections, verify ~70/30 split for weights 7/3
- `test_unhealthy_endpoint_skipped` — mark one endpoint unhealthy, verify only healthy one selected
- `test_cooldown_expires` — mark unhealthy, advance time past cooldown, verify re-enabled
- `test_all_unhealthy_returns_none` — all endpoints in cooldown returns None
- `test_no_matching_group_returns_none` — unmatched model_id returns None

**Commit:**
```bash
git add src/gateway/routing/ src/gateway/config.py tests/unit/test_balancer.py
git commit -m "feat(phase-25): add model groups with weighted load balancing"
```

---

### Task 25.3: Circuit Breakers

**Files:**
- Create: `src/gateway/routing/circuit.py`
- Test: `tests/unit/test_circuit_breaker.py`

**Design:** Per-model circuit breaker registry using pybreaker.

```python
class CircuitBreakerRegistry:
    def __init__(self, fail_max: int = 5, reset_timeout: int = 30):
        self._breakers: dict[str, pybreaker.CircuitBreaker] = {}

    def get_breaker(self, model_id: str) -> pybreaker.CircuitBreaker:
        """Get or create circuit breaker for model."""

    def is_open(self, model_id: str) -> bool:
        """Check if circuit is open (tripped)."""

    def record_success(self, model_id: str):
        """Record successful call."""

    def record_failure(self, model_id: str):
        """Record failed call."""
```

**Tests:**
- `test_breaker_trips_after_n_failures` — 5 failures → circuit opens
- `test_breaker_allows_after_reset_timeout` — wait past timeout → half-open → success → closed
- `test_separate_breakers_per_model` — model A tripped, model B still works
- `test_success_resets_failure_count`

**Commit:**
```bash
git add src/gateway/routing/circuit.py tests/unit/test_circuit_breaker.py
git commit -m "feat(phase-25): add per-model circuit breaker registry"
```

---

### Task 25.4: Retry Logic with Tenacity

**Files:**
- Create: `src/gateway/routing/retry.py`
- Test: `tests/unit/test_retry.py`

**Design:** Retry wrapper for provider forward calls.

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

def is_retryable(exc: Exception) -> bool:
    """Returns True for transient errors (503, 429, network errors)."""

async def forward_with_retry(adapter, call, request, max_attempts=3):
    """Forward with exponential backoff retry on transient errors.
    Returns (response, model_response) on success.
    Raises last exception if all retries exhausted."""
```

**Tests:**
- `test_retry_on_503_succeeds_on_second_attempt` — mock forward to fail once then succeed
- `test_no_retry_on_400` — 400 errors are not retried
- `test_max_attempts_exhausted_raises` — all attempts fail → raises
- `test_retry_on_network_error` — httpx.ConnectError triggers retry
- `test_429_triggers_retry` — rate limited response retried

**Commit:**
```bash
git add src/gateway/routing/retry.py tests/unit/test_retry.py
git commit -m "feat(phase-25): add retry with exponential backoff for transient errors"
```

---

### Task 25.5: Error-Specific Fallback Routing

**Files:**
- Create: `src/gateway/routing/fallback.py`
- Test: `tests/unit/test_fallback.py`

**Design:** Classify errors and route to appropriate fallback.

```python
def classify_error(status_code: int, body: str) -> str:
    """Classify error into category: 'context_overflow', 'content_policy', 'rate_limited', 'server_error', 'other'."""

def select_fallback(error_class: str, model_id: str, balancer: LoadBalancer) -> Endpoint | None:
    """Select fallback endpoint based on error class.
    context_overflow → next endpoint in group (potentially larger model)
    rate_limited → next endpoint in group
    server_error → next endpoint in group
    content_policy → None (don't retry, return error)
    """
```

**Tests:**
- `test_classify_context_overflow` — "maximum context length" → context_overflow
- `test_classify_rate_limited` — 429 → rate_limited
- `test_classify_content_policy` — "content policy violation" → content_policy
- `test_content_policy_no_fallback` — content_policy returns None (don't retry)
- `test_fallback_skips_failed_endpoint` — excludes the endpoint that just failed

**Commit:**
```bash
git add src/gateway/routing/fallback.py tests/unit/test_fallback.py
git commit -m "feat(phase-25): add error-specific fallback routing"
```

---

### Task 25.6: Integrate Resilience into Orchestrator

**Files:**
- Modify: `src/gateway/pipeline/orchestrator.py` (wire retry + circuit breaker + fallback into handle_request)
- Modify: `src/gateway/pipeline/context.py` (add `load_balancer`, `circuit_breakers` fields)
- Modify: `src/gateway/main.py` (init load balancer + circuit breakers in on_startup)
- Modify: `src/gateway/pipeline/hasher.py` (add `retry_of` field to execution record)

**Integration pattern in handle_request():**

```python
# After _resolve_adapter and _run_pre_checks:
# 1. Check circuit breaker — if open, try fallback endpoint
# 2. Forward with retry (tenacity wrapper)
# 3. On success: record_success on circuit breaker
# 4. On failure: record_failure, classify error, select fallback
# 5. If fallback available: retry with new endpoint
# 6. Each provider call generates its own execution record
# 7. Only final successful execution advances session chain
```

**Audit trail for retries:**
- `build_execution_record()` gets new optional field `retry_of: str | None`
- First attempt: `retry_of=None`
- Subsequent attempts: `retry_of=original_execution_id`
- Only the final successful execution calls `_apply_session_chain`

**Tests:** Run full suite + manual integration test with multiple endpoints.

**Commit:**
```bash
git add src/gateway/pipeline/ src/gateway/main.py
git commit -m "feat(phase-25): integrate retry, circuit breaker, and fallback into orchestrator pipeline"
```

---

### Task 25.7: Phase 25 Docs + Full Test Run

Update CLAUDE.md with Phase 25 section. Run full suite.

**Commit:**
```bash
git add CLAUDE.md
git commit -m "docs(phase-25): document resilience layer (retry, circuit breakers, fallback, model groups)"
```

---

## Phase 26: Rate Limiting + Alerting (5-7 days)

### Task 26.1: Sliding Window Rate Limiter

**Files:**
- Create: `src/gateway/pipeline/rate_limiter.py`
- Test: `tests/unit/test_rate_limiter.py`

**Design:**

```python
class SlidingWindowRateLimiter:
    """In-memory sliding window counter for RPM rate limiting."""

    def __init__(self):
        self._windows: dict[str, list[float]] = {}  # key → [timestamps]

    async def check(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        """Check if request is within rate limit.
        Returns (allowed, remaining_count)."""

    def _key(self, user: str, model: str) -> str:
        return f"{user}:{model}"


class RedisRateLimiter:
    """Redis-backed sliding window using sorted sets."""

    def __init__(self, redis_client):
        self._redis = redis_client

    async def check(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        """Atomic check using ZSET + Lua script."""
```

**Config fields:**
```python
# config.py
rate_limit_enabled: bool = Field(False, env="WALACOR_RATE_LIMIT_ENABLED")
rate_limit_rpm: int = Field(60, env="WALACOR_RATE_LIMIT_RPM")  # requests per minute
rate_limit_per_model: bool = Field(True, env="WALACOR_RATE_LIMIT_PER_MODEL")
```

**Tests:**
- `test_within_limit_allowed` — 5 requests under limit of 10 → all allowed
- `test_exceeds_limit_denied` — 11th request under limit of 10 → denied
- `test_window_slides` — requests expire after window_seconds
- `test_per_user_per_model_isolation` — different keys don't interfere
- `test_remaining_count_accurate`

**Commit:**
```bash
git add src/gateway/pipeline/rate_limiter.py tests/unit/test_rate_limiter.py src/gateway/config.py
git commit -m "feat(phase-26): add sliding window rate limiter (in-memory + Redis)"
```

---

### Task 26.2: Rate Limit Response Headers + 429

**Files:**
- Modify: `src/gateway/pipeline/orchestrator.py` (check rate limit before forward, add headers)

**Headers on every response:**
```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 45
X-RateLimit-Reset: 1710100800 (Unix timestamp when window resets)
```

**On 429:**
```
Retry-After: 15 (seconds until limit resets)
```

Return `JSONResponse(status_code=429, content={"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}})`.

**Commit:**
```bash
git add src/gateway/pipeline/orchestrator.py
git commit -m "feat(phase-26): add rate limit response headers and 429 responses"
```

---

### Task 26.3: Alert Event Bus + Webhook Dispatcher

**Files:**
- Create: `src/gateway/alerts/__init__.py`
- Create: `src/gateway/alerts/bus.py`
- Create: `src/gateway/alerts/dispatcher.py`
- Modify: `src/gateway/config.py` (webhook config fields)
- Test: `tests/unit/test_alerts.py`

**Event bus:**
```python
class AlertBus:
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._dispatchers: list[AlertDispatcher] = []

    async def emit(self, event: AlertEvent):
        """Non-blocking put to queue. Drop if full (fail-open)."""

    async def run(self):
        """Background task: read from queue, dispatch to all dispatchers."""
```

**AlertEvent:**
```python
@dataclass
class AlertEvent:
    type: str  # "budget_threshold", "policy_violation", "error_spike", "chain_integrity"
    severity: str  # "info", "warning", "critical"
    message: str
    metadata: dict
    timestamp: str
```

**Dispatchers:**
```python
class WebhookDispatcher:
    """POST JSON to configured URLs."""

class SlackDispatcher(WebhookDispatcher):
    """Format payload as Slack Block Kit message."""

class PagerDutyDispatcher:
    """POST to PagerDuty Events API v2."""
```

**Config:**
```python
webhook_urls: str = Field("", env="WALACOR_WEBHOOK_URLS")  # comma-separated
pagerduty_routing_key: str = Field("", env="WALACOR_PAGERDUTY_ROUTING_KEY")
alert_budget_thresholds: str = Field("70,90,100", env="WALACOR_ALERT_BUDGET_THRESHOLDS")
```

**Tests:**
- `test_emit_and_dispatch` — emit event, verify dispatcher receives it
- `test_slack_format` — verify Slack Block Kit message structure
- `test_pagerduty_format` — verify PD Events API v2 payload
- `test_queue_full_drops_gracefully` — overfill queue, no crash
- `test_dispatcher_failure_no_crash` — webhook 500 doesn't crash bus

**Commit:**
```bash
git add src/gateway/alerts/ tests/unit/test_alerts.py src/gateway/config.py
git commit -m "feat(phase-26): add alert event bus with webhook + Slack + PagerDuty dispatchers"
```

---

### Task 26.4: Budget Threshold Hooks

**Files:**
- Modify: `src/gateway/pipeline/budget_tracker.py` (emit alert at thresholds)
- Test: `tests/unit/test_budget_alerts.py`

In `record_usage()`, after updating token count, check if usage crossed any configured threshold (70%, 90%, 100%). If so, emit AlertEvent to the bus.

**Commit:**
```bash
git add src/gateway/pipeline/budget_tracker.py tests/unit/test_budget_alerts.py
git commit -m "feat(phase-26): add budget threshold alert hooks (70/90/100%)"
```

---

### Task 26.5: Prometheus Gauges + Integration + Docs

**Files:**
- Modify: `src/gateway/metrics/prometheus.py` (add gauges)
- Modify: `src/gateway/main.py` (init alert bus in on_startup)
- Modify: `src/gateway/pipeline/context.py` (add `alert_bus` field)

**New Prometheus metrics:**
```python
gateway_budget_utilization_ratio = Gauge("gateway_budget_utilization_ratio", "Budget utilization 0-1", ["tenant_id"])
gateway_content_blocks_total = Counter("gateway_content_blocks_total", "Content analysis blocks", ["analyzer"])
gateway_rate_limit_hits_total = Counter("gateway_rate_limit_hits_total", "Rate limit 429 responses", ["model"])
```

Run full suite. Update CLAUDE.md.

**Commit:**
```bash
git add src/gateway/ CLAUDE.md
git commit -m "feat(phase-26): add Prometheus gauges for AlertManager integration"
```

---

## Phase 27: Governance Waterfall Trace View (5-7 days)

### Task 27.1: Capture Pipeline Timing Data

**Files:**
- Modify: `src/gateway/pipeline/orchestrator.py` (add timing dict)
- Modify: `src/gateway/pipeline/hasher.py` (add `timings` to execution record)
- Test: `tests/unit/test_pipeline_timings.py`

**Design:** Add a `timings` dict to the handle_request flow:

```python
timings = {}
t0 = time.monotonic()
# ... auth check ...
timings["auth_ms"] = round((time.monotonic() - t0) * 1000, 1)

t0 = time.monotonic()
# ... attestation check ...
timings["attestation_ms"] = round((time.monotonic() - t0) * 1000, 1)
# ... etc for each pipeline step
```

Store `timings` in the execution record JSON alongside existing fields.

**Tests:**
- `test_timings_dict_has_all_steps` — verify all expected keys present
- `test_timings_are_positive_numbers`

**Commit:**
```bash
git add src/gateway/pipeline/orchestrator.py src/gateway/pipeline/hasher.py tests/unit/test_pipeline_timings.py
git commit -m "feat(phase-27): capture per-step pipeline timing data in execution records"
```

---

### Task 27.2: Trace API Endpoint

**Files:**
- Modify: `src/gateway/lineage/reader.py` (add `get_execution_trace`)
- Modify: `src/gateway/lineage/api.py` (add trace endpoint)
- Test: `tests/unit/test_trace_api.py`

```python
def get_execution_trace(self, execution_id: str) -> dict | None:
    """Return execution + tool events + timings for waterfall view."""
    execution = self.get_execution(execution_id)
    if not execution:
        return None
    tool_events = self.get_tool_events(execution_id)
    return {
        "execution": execution,
        "tool_events": tool_events,
        "timings": json.loads(execution.get("record", "{}")).get("timings", {}),
    }
```

Endpoint: `GET /v1/lineage/trace/{execution_id}`

**Commit:**
```bash
git add src/gateway/lineage/ tests/unit/test_trace_api.py
git commit -m "feat(phase-27): add trace API endpoint for waterfall view"
```

---

### Task 27.3: Canvas Waterfall Component

**Files:**
- Create: `src/gateway/lineage/dashboard/src/components/TraceWaterfall.jsx`
- Modify: `src/gateway/lineage/dashboard/src/views/Execution.jsx` (integrate waterfall)
- Modify: `src/gateway/lineage/dashboard/src/api.js` (add `getTrace` function)
- Modify: `src/gateway/lineage/dashboard/src/styles/index.css` (waterfall styles)

**Canvas rendering pattern** (matches existing ThroughputChart):

```jsx
function TraceWaterfall({ trace }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    if (!trace?.timings) return;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    // Draw horizontal bars for each pipeline step
    // Color: green=pass, red=block, gold=tool, blue=forward
    // Labels: step name + duration
    // Governance annotations: policy verdict, hash badge, content analysis
  }, [trace]);

  return <canvas ref={canvasRef} className="trace-waterfall" />;
}
```

**Steps to render:**
1. Auth → Attestation → Pre-Policy → Forward → [Tool calls] → Content Analysis → Post-Policy → Chain → Write
2. Each bar width proportional to duration
3. Nested bars for tool calls within Forward
4. Color-coded verdict badges (green checkmark, red X, gold gear)
5. Hover tooltip with details (hash values, policy rule names, analyzer results)

**Commit:**
```bash
git add src/gateway/lineage/dashboard/src/
git commit -m "feat(phase-27): add governance waterfall trace view to dashboard"
```

---

### Task 27.4: Phase 27 Docs

Update CLAUDE.md. Run full suite.

**Commit:**
```bash
git add CLAUDE.md
git commit -m "docs(phase-27): document governance waterfall trace view"
```

---

## Phase 28: Provider Caching + Streaming Governance (5-7 days)

### Task 28.1: Anthropic Cache Control Auto-Injection

**Files:**
- Create: `src/gateway/adapters/caching.py`
- Modify: `src/gateway/adapters/anthropic.py` (call auto-inject before forward)
- Modify: `src/gateway/config.py` (add `prompt_caching_enabled` field)
- Test: `tests/unit/test_prompt_caching.py`

**Design:**

```python
def inject_cache_control(messages: list[dict]) -> list[dict]:
    """Auto-inject cache_control breakpoints on system messages.
    Only modifies system role messages. Adds {"type": "ephemeral"} cache_control
    to the last content block of each system message."""

def detect_cache_hit(usage: dict) -> dict:
    """Detect Anthropic cache hits from usage response.
    Returns {"cache_hit": bool, "cached_tokens": int, "cache_creation_tokens": int}."""
```

**Config:**
```python
prompt_caching_enabled: bool = Field(True, env="WALACOR_PROMPT_CACHING_ENABLED")
```

**Tests:**
- `test_inject_adds_cache_control_to_system` — system message gets cache_control
- `test_inject_preserves_user_messages` — user messages unchanged
- `test_inject_idempotent` — already has cache_control → no duplicate
- `test_detect_cache_hit_anthropic` — usage with `cache_read_input_tokens > 0` → hit
- `test_detect_no_cache_hit` — usage without cache fields → miss

**Commit:**
```bash
git add src/gateway/adapters/caching.py src/gateway/adapters/anthropic.py src/gateway/config.py tests/unit/test_prompt_caching.py
git commit -m "feat(phase-28): add Anthropic cache_control auto-injection for system prompts"
```

---

### Task 28.2: OpenAI Cached Tokens Detection

**Files:**
- Modify: `src/gateway/adapters/openai.py` (detect cached_tokens in usage)
- Modify: `src/gateway/pipeline/hasher.py` (add cache fields to execution record)

OpenAI prefix caching is automatic — no injection needed. Just detect `usage.prompt_tokens_details.cached_tokens` in the response and record it.

**Commit:**
```bash
git add src/gateway/adapters/openai.py src/gateway/pipeline/hasher.py
git commit -m "feat(phase-28): detect OpenAI cached_tokens and record in execution"
```

---

### Task 28.3: Mid-Stream S4 Safety Abort

**Files:**
- Create: `src/gateway/content/stream_safety.py`
- Modify: `src/gateway/pipeline/forwarder.py` (check during chunk accumulation)
- Test: `tests/unit/test_stream_safety.py`

**Design:**

```python
import re

# Compiled regex for S4 (child safety) patterns — fast, sub-millisecond
_S4_PATTERNS = re.compile(r"...", re.IGNORECASE)  # curated keyword list

def check_stream_safety(accumulated_text: str) -> bool:
    """Returns True if accumulated text triggers S4 safety abort.
    Fast regex check — no ML model invocation."""
    return bool(_S4_PATTERNS.search(accumulated_text))
```

In `stream_with_tee()`, after each chunk is accumulated, call `check_stream_safety()`. If triggered, send `event: error` SSE event with safety message and close the stream.

**Tests:**
- `test_safe_content_passes` — normal text → False
- `test_s4_content_triggers` — S4 keyword → True
- `test_coding_context_not_flagged` — "kill the process" → False (not S4)
- `test_check_is_fast` — <1ms for 10KB text

**Commit:**
```bash
git add src/gateway/content/stream_safety.py src/gateway/pipeline/forwarder.py tests/unit/test_stream_safety.py
git commit -m "feat(phase-28): add mid-stream S4 safety abort (keyword regex)"
```

---

### Task 28.4: Phase 28 Docs

Update CLAUDE.md. Run full suite.

**Commit:**
```bash
git add CLAUDE.md
git commit -m "docs(phase-28): document provider caching and streaming governance"
```

---

## Phase 29: Playground + Weighted Routing (7-10 days)

### Task 29.1: Playground Dashboard Tab

**Files:**
- Create: `src/gateway/lineage/dashboard/src/views/Playground.jsx`
- Modify: `src/gateway/lineage/dashboard/src/App.jsx` (add playground tab)
- Modify: `src/gateway/lineage/dashboard/src/styles/index.css` (playground styles)

**UI Components:**
1. **Model selector** — dropdown populated from `GET /v1/models`
2. **System prompt** — textarea (optional)
3. **User prompt** — textarea (required)
4. **Parameters** — temperature slider, max_tokens input
5. **Send button** — POSTs to gateway's own `/v1/chat/completions`
6. **Response panel** — displays model response
7. **Governance panel** — displays `X-Walacor-*` headers, execution record from `/v1/lineage/executions/{id}`

**Key design:** The playground routes through the gateway itself. Every test generates real audit records. The governance panel shows:
- Execution ID + attestation ID
- Policy evaluation result
- Content analysis verdicts (PII, toxicity, Llama Guard)
- Session chain hash + sequence number
- Token usage + latency

**Commit:**
```bash
git add src/gateway/lineage/dashboard/src/
git commit -m "feat(phase-29): add prompt playground tab to dashboard"
```

---

### Task 29.2: Side-by-Side Model Comparison

**Files:**
- Modify: `src/gateway/lineage/dashboard/src/views/Playground.jsx` (add comparison mode)

**Design:** "Compare" toggle switches to dual-pane mode:
- Left: Model A selector + response
- Right: Model B selector + response
- Both fire simultaneously via `Promise.all([fetch(...), fetch(...)])`
- Results displayed side-by-side with latency, tokens, cost, governance metadata

**Commit:**
```bash
git add src/gateway/lineage/dashboard/src/views/Playground.jsx
git commit -m "feat(phase-29): add side-by-side model comparison in playground"
```

---

### Task 29.3: Weighted Routing (A/B)

**Files:**
- Modify: `src/gateway/routing/balancer.py` (already supports weights from Phase 25)
- Modify: `src/gateway/pipeline/hasher.py` (add `variant_id` to execution record)
- Modify: `src/gateway/pipeline/orchestrator.py` (set variant_id when weight-based selection is used)
- Test: `tests/unit/test_weighted_routing.py`

**Design:** The `LoadBalancer.select_endpoint()` from Phase 25 already does weighted random. This task adds:
- `variant_id` field to execution record (format: `{model_id}@{endpoint_url}`)
- When model groups have >1 endpoint, tag the execution with which variant was selected
- Dashboard can filter/group by variant_id for comparison

**Tests:**
- `test_variant_id_set_when_model_group_used`
- `test_variant_id_none_when_single_endpoint`
- `test_variant_distribution_recorded` — verify variant_ids in execution records match expected weight distribution

**Commit:**
```bash
git add src/gateway/routing/ src/gateway/pipeline/ tests/unit/test_weighted_routing.py
git commit -m "feat(phase-29): add variant tracking for weighted routing / A/B testing"
```

---

### Task 29.4: Phase 29 Docs + Final Test Run

Update CLAUDE.md with Phase 29 section. Run complete test suite.

Expected final test count: 260+ pass

**Commit:**
```bash
git add CLAUDE.md README.md
git commit -m "docs(phase-29): document playground, comparison mode, and weighted routing"
```

---

## Post-Implementation Checklist

After all 7 phases are complete:

- [ ] Run full test suite: `cd src && python -m pytest ../tests/ -v --timeout=60`
- [ ] Run governance stress test: `python tests/governance_stress.py` (if Ollama available)
- [ ] Verify dashboard builds: `cd src/gateway/lineage/dashboard && npm run build`
- [ ] Update `OVERVIEW.md` with new feature summary
- [ ] Update `docs/WIKI-EXECUTIVE.md` with compliance export story
- [ ] Update `.env.example` with all new WALACOR_ env vars
- [ ] Update `deploy/docker-compose.yml` if new services needed
- [ ] Tag release: `git tag v0.29.0`
