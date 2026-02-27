# Walacor AI Security Gateway — Detailed Implementation Plan

**Status:** Implementation Ready  
**Version:** 1.0 | February 16, 2026  
**Classification:** Company Confidential  
**Prepared by:** Quantitative Analytics Engineering, Walacor Corporation, Bethesda, MD

---

## 1. Executive Summary

This document provides the complete implementation plan for the Walacor AI Security Gateway — a separate, standalone product that serves as the data plane for the AI Security Lifecycle Orchestration Platform (AI-SLOP). The gateway intercepts all AI/LLM traffic at the network boundary, enforcing attestation verification, policy compliance, and cryptographic audit recording with zero application code changes.

The plan covers repository structure, dependency architecture, eight implementation phases with weekly milestones, detailed technical specifications for every component, testing strategy with compliance evidence generation, deployment packaging, and production hardening. Total estimated implementation time is 8 weeks to production-ready MVP.

---

## 2. Product Architecture: Three-Package Model

The gateway is a separate product from the FastAPI control plane. They share code through a common library but are independently versioned, deployed, and scaled.

### 2.1 Package Structure

```
walacor-platform/                    ← monorepo root
│
├── walacor-core/                    ← shared library (PyPI package)
│   ├── walacor_core/
│   │   ├── __init__.py
│   │   ├── hashing.py               ← SHA3-512 (single source of truth)
│   │   ├── models/
│   │   │   ├── attestation.py       ← AttestationProof, AttestationStatus
│   │   │   ├── execution.py         ← ExecutionRecord, GatewayExecutionRequest
│   │   │   ├── policy.py            ← Policy, PolicyEvalResult
│   │   │   └── prompt.py            ← PromptAttestation
│   │   ├── policy_engine.py         ← evaluate_policies() logic
│   │   ├── crypto.py                ← SHA3-512 wrapper, hash validation
│   │   └── constants.py             ← shared constants, status enums
│   ├── pyproject.toml
│   └── tests/
│
├── walacor-control-plane/           ← FastAPI server (existing)
│   ├── depends on: walacor-core
│   ├── src/walacor_mcp/
│   └── ...
│
└── walacor-gateway/                 ← AI Security Gateway (NEW)
    ├── depends on: walacor-core
    ├── src/gateway/
    ├── tests/
    ├── Dockerfile
    ├── helm/
    └── pyproject.toml
```

### 2.2 Why Three Packages

- **walacor-core:** Ensures SHA3-512 hashing, policy evaluation, and data models are identical in both products. A hash computed in the gateway must match what the control plane would compute. Single source of truth eliminates drift.
- **walacor-control-plane:** Existing FastAPI server. Receives only hashes from the gateway. Manages the ledger, lineage DAG, audit log, and compliance reports. Unchanged except for the new `/v1/gateway/executions` endpoint.
- **walacor-gateway:** New product. Stateless request pipeline + local state (attestation cache, policy cache, WAL). Independently deployable, scalable, and versionable.

### 2.3 Dependency Direction

Both walacor-control-plane and walacor-gateway depend on walacor-core. They never depend on each other. The gateway communicates with the control plane exclusively over HTTP APIs. This means the control plane can be upgraded without redeploying the gateway (and vice versa), as long as the API contract holds.

---

## 3. Gateway Internal Architecture

### 3.1 Directory Structure

```
walacor-gateway/
├── src/
│   ├── gateway/
│   │   ├── __init__.py
│   │   ├── main.py                  ← ASGI app entry point (uvicorn)
│   │   ├── config.py                ← env-based configuration
│   │   ├── pipeline/
│   │   │   ├── __init__.py
│   │   │   ├── orchestrator.py      ← 5-step pipeline coordinator
│   │   │   ├── model_resolver.py    ← Step 1: attestation lookup
│   │   │   ├── policy_evaluator.py  ← Step 2: pre-inference policy
│   │   │   ├── forwarder.py         ← Step 3: transparent forward + stream tee
│   │   │   ├── hasher.py            ← Step 4: SHA3-512 hashing
│   │   │   └── recorder.py          ← Step 4+5: WAL write + async delivery
│   │   ├── adapters/
│   │   │   ├── __init__.py
│   │   │   ├── base.py              ← ProviderAdapter ABC
│   │   │   ├── openai.py            ← OpenAI adapter (MVP)
│   │   │   ├── anthropic.py         ← Anthropic adapter (Phase 2)
│   │   │   ├── huggingface.py       ← HuggingFace adapter (Phase 3)
│   │   │   └── generic.py           ← Configurable adapter (Phase 3)
│   │   ├── cache/
│   │   │   ├── __init__.py
│   │   │   ├── attestation_cache.py ← TTL cache with fail-closed
│   │   │   └── policy_cache.py      ← Versioned cache with fail-closed
│   │   ├── wal/
│   │   │   ├── __init__.py
│   │   │   ├── writer.py            ← Append-only WAL with fsync
│   │   │   └── delivery_worker.py   ← Async background delivery
│   │   ├── sync/
│   │   │   ├── __init__.py
│   │   │   ├── sync_client.py       ← Pull sync + push listener
│   │   │   └── push_handler.py      ← SSE/WebSocket handler
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   └── api_key.py           ← Request authentication
│   │   ├── metrics/
│   │   │   ├── __init__.py
│   │   │   └── prometheus.py        ← Metric definitions + /metrics
│   │   └── health.py                ← GET /health endpoint
│   └── gateway_cli.py               ← CLI entry point
├── tests/
│   ├── unit/
│   ├── integration/
│   └── compliance/                  ← tests that generate compliance evidence
├── deploy/
│   ├── Dockerfile
│   ├── Dockerfile.fips              ← FIPS 140-2 compliant build
│   ├── docker-compose.yml           ← gateway + control plane for dev
│   ├── helm/
│   │   ├── Chart.yaml
│   │   ├── values.yaml
│   │   ├── values-govcloud.yaml
│   │   └── templates/
│   └── network-policies/
│       ├── k8s-egress-policy.yaml
│       ├── aws-sg-reference.tf
│       └── airgapped-iptables.sh
├── docs/
│   ├── QUICKSTART.md
│   ├── CONFIGURATION.md
│   ├── ADAPTERS.md
│   └── COMPLIANCE-EVIDENCE.md
├── pyproject.toml
├── README.md
└── Makefile
```

### 3.2 Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Runtime | Python 3.12+ / uvicorn | Ecosystem alignment with control plane; async-native |
| HTTP Framework | Starlette (raw ASGI) | Lighter than FastAPI for proxy use; no Pydantic overhead on hot path |
| HTTP Client | httpx (async) | Streaming support, connection pooling, HTTP/2 |
| WAL | SQLite WAL mode | Proven durability, built-in crash recovery, zero external dependencies |
| Hashing | hashlib (SHA3-512) | Standard library; from walacor-core shared package |
| Policy Engine | walacor-core | Identical evaluation logic as control plane |
| Metrics | prometheus_client | Industry standard; K8s native scraping |
| Configuration | pydantic-settings | Env-based config with validation |
| Testing | pytest + pytest-asyncio | Async test support; compliance evidence generation |
| Container | Docker (multi-stage) | Minimal attack surface; separate FIPS variant |
| Orchestration | Helm 3 | K8s-native deployment; GovCloud values overlay |

### 3.3 Why Starlette Instead of FastAPI

The gateway is a proxy, not an API server. FastAPI adds Pydantic model validation, OpenAPI schema generation, and dependency injection — all valuable for the control plane, but overhead on the proxy hot path where every millisecond counts. Starlette provides raw ASGI routing and streaming without that overhead. The gateway's internal validation uses walacor-core's model types directly.

---

## 4. Configuration System

All configuration is environment-variable based with pydantic-settings validation on startup. Fail-fast: if required configuration is missing or invalid, the gateway refuses to start.

### 4.1 Configuration Variables

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `WALACOR_GATEWAY_TENANT_ID` | — | Yes | Tenant this gateway serves (single-tenant V1) |
| `WALACOR_CONTROL_PLANE_URL` | — | Yes | Base URL of the control plane |
| `WALACOR_GATEWAY_API_KEYS` | — | Production | Comma-separated API keys for caller auth |
| `WALACOR_GATEWAY_ID` | auto-generated | No | Unique gateway instance identifier |
| `WALACOR_ATTESTATION_CACHE_TTL` | 300 | No | Attestation cache TTL in seconds |
| `WALACOR_POLICY_STALENESS_THRESHOLD` | 900 | No | Max policy staleness before fail-closed (seconds) |
| `WALACOR_SYNC_INTERVAL` | 60 | No | Pull sync interval in seconds |
| `WALACOR_WAL_PATH` | /var/walacor/wal | No | WAL storage directory |
| `WALACOR_WAL_MAX_SIZE_GB` | 10 | No | Max WAL disk usage before action |
| `WALACOR_WAL_MAX_AGE_HOURS` | 72 | No | Max WAL record age before action |
| `WALACOR_ENFORCEMENT_MODE` | enforced | No | `enforced` or `audit_only` |
| `WALACOR_PROVIDER_OPENAI_URL` | https://api.openai.com | No | OpenAI API base URL |
| `WALACOR_PROVIDER_OPENAI_KEY` | — | Per-provider | API key for OpenAI forwarding |
| `WALACOR_PROVIDER_ANTHROPIC_URL` | https://api.anthropic.com | No | Anthropic API base URL |
| `WALACOR_METRICS_ENABLED` | true | No | Enable Prometheus /metrics endpoint |
| `WALACOR_LOG_LEVEL` | INFO | No | Logging level |

---

## 5. Detailed Component Specifications

### 5.1 Provider Adapter Interface

Every LLM provider is abstracted behind a common interface. The pipeline never knows which provider it's talking to — it only works with `ModelCall` and `ModelResponse` objects.

#### Abstract Base Class

```python
class ProviderAdapter(ABC):
    @abstractmethod
    async def parse_request(self, request: Request) -> ModelCall:
        """Extract model_id, prompt, params from raw HTTP request."""

    @abstractmethod
    async def build_forward_request(
        self, call: ModelCall, original: Request
    ) -> httpx.Request:
        """Build the upstream request to the real provider."""

    @abstractmethod
    def parse_response(self, response: httpx.Response) -> ModelResponse:
        """Extract content, usage from provider response."""

    @abstractmethod
    def parse_streamed_response(self, chunks: list[bytes]) -> ModelResponse:
        """Assemble full response from buffered stream chunks."""

    @abstractmethod
    def supports_streaming(self) -> bool: ...

    @abstractmethod
    def get_provider_name(self) -> str: ...
```

#### ModelCall and ModelResponse

```python
@dataclass(frozen=True)
class ModelCall:
    provider: str            # 'openai', 'anthropic', etc.
    model_id: str            # 'gpt-4', 'claude-sonnet-4-20250514', etc.
    prompt_text: str         # full prompt as string (for hashing)
    raw_body: bytes          # original request body (for forwarding)
    is_streaming: bool       # whether client requested streaming
    metadata: dict           # user, session_id, etc. from headers

@dataclass(frozen=True)
class ModelResponse:
    content: str             # full response text (for hashing)
    usage: dict | None       # token usage if available
    raw_body: bytes          # original response body (returned to caller)
```

#### OpenAI Adapter (MVP)

The OpenAI adapter handles `/v1/chat/completions` (primary) and `/v1/completions` (legacy). It supports both streaming (SSE) and non-streaming modes.

- Route matching: requests with path starting `/v1/chat/completions` or `/v1/completions`
- Model extraction: `body["model"]` field
- Prompt extraction: concatenate all `messages[].content` for hashing
- Streaming: detect `stream: true` in body; use SSE tee pattern
- Auth forwarding: copy `Authorization` header to upstream request

#### Generic Adapter

For custom/on-prem inference servers, the GenericAdapter uses configurable JSON paths:

```bash
WALACOR_GENERIC_MODEL_PATH=$.model
WALACOR_GENERIC_PROMPT_PATH=$.messages[*].content
WALACOR_GENERIC_RESPONSE_PATH=$.choices[0].message.content
WALACOR_GENERIC_UPSTREAM_URL=https://my-internal-model:8080
```

### 5.2 Pipeline Orchestrator

The orchestrator is the central coordinator. Every request flows through it. It enforces the step order and handles errors at each stage.

#### Request Flow (Pseudocode)

```python
async def handle_request(request: Request) -> Response:
    adapter = router.resolve_adapter(request.url.path)
    if not adapter:
        return Response(404, 'No adapter for this path')

    # Step 1: Model Resolution (sync)
    call = await adapter.parse_request(request)
    attestation = attestation_cache.lookup(call.provider, call.model_id)
    if attestation is None or attestation.status in (REVOKED, TAMPERED):
        metrics.blocked_attestation.inc()
        return Response(403, 'Model not attested or revoked')
    if attestation.is_stale and not control_plane.reachable:
        metrics.blocked_stale.inc()
        return Response(503, 'Attestation cache stale, control plane unreachable')

    # Step 2: Pre-Inference Policy (sync)
    prompt_hash = walacor_core.hash_sha3_512(call.prompt_text)
    policy_result = policy_cache.evaluate(call, prompt_hash)
    if policy_result.blocked:
        metrics.blocked_policy.inc()
        wal.write(build_blocked_record(call, attestation, policy_result))
        return Response(403, 'Blocked by policy')

    # Step 3: Forward (sync)
    if call.is_streaming:
        response, buffered_chunks = await forwarder.stream_with_tee(
            adapter, call, request
        )  # streams to caller while buffering
        model_response = adapter.parse_streamed_response(buffered_chunks)
    else:
        upstream_response = await forwarder.forward(adapter, call, request)
        model_response = adapter.parse_response(upstream_response)
        response = Response(upstream_response.status, upstream_response.body)

    # Step 4: Hash + WAL (async, but before connection close)
    response_hash = walacor_core.hash_sha3_512(model_response.content)
    record = ExecutionRecord(
        execution_id=uuid4(),
        model_attestation_id=attestation.attestation_id,
        prompt_hash=prompt_hash,
        response_hash=response_hash,
        policy_version=policy_cache.version,
        tenant_id=config.tenant_id,
        gateway_id=config.gateway_id,
        timestamp=utcnow(),
    )
    await wal.write_and_fsync(record)  # durable before close

    # Step 5: Async delivery (background)
    delivery_worker.enqueue(record.execution_id)

    metrics.requests_allowed.inc()
    return response
```

### 5.3 Attestation Cache

The attestation cache holds the mapping of `(provider, model_id)` to attestation status. It is the enforcement point for Guarantee G1.

#### Data Structure

```python
@dataclass
class CachedAttestation:
    attestation_id: str
    model_id: str
    provider: str
    status: AttestationStatus  # VERIFIED, REVOKED, TAMPERED, PENDING
    fetched_at: datetime       # when this entry was fetched
    ttl_seconds: int           # from config

    @property
    def is_expired(self) -> bool:
        return (utcnow() - self.fetched_at).total_seconds() > self.ttl_seconds
```

#### Lookup Logic

1. Check in-memory dict for `(provider, model_id)` key.
2. If found and not expired: return cached entry.
3. If found but expired: attempt refresh from control plane.
4. If refresh succeeds: update cache, return fresh entry.
5. If refresh fails (control plane unreachable): **FAIL-CLOSED**. Return `None`, request will be blocked.
6. If not found: attempt fetch from control plane. If unreachable: **FAIL-CLOSED**.

> *Design principle: The gateway never serves a request against a model whose attestation status it cannot confirm. Stale-but-available is not acceptable for G1.*

### 5.4 Policy Cache

The policy cache holds the current tenant's policy set. It is the enforcement point for Guarantee G3.

#### Versioning

- Every policy set has an integer version, incremented by the control plane on any policy change.
- The gateway records which version it evaluated in every execution record.
- On sync, the gateway compares its local version with the control plane's version. If different, it pulls the full policy set.

#### Staleness Threshold

- If the gateway has not successfully synced policies within the staleness threshold (default: 15 minutes), it enters fail-closed mode for policy evaluation.
- In fail-closed mode: all requests are blocked with HTTP 503, regardless of policy content.
- The health endpoint reports `degraded` status when approaching the threshold and `fail_closed` when exceeded.

> *Compliance implication: Every execution record includes `policy_version`. If the gateway operated with a within-threshold but not latest policy set, the audit trail shows exactly which version was used. The control plane can retroactively flag those executions.*

### 5.5 Write-Ahead Log (WAL)

The WAL is the durability backbone for Guarantee G2. It ensures no execution record is lost, even if the gateway crashes or the control plane is unreachable.

#### Implementation: SQLite WAL Mode

- SQLite database in WAL mode (`journal_mode=WAL`, `synchronous=FULL`).
- Single table: `wal_records (execution_id TEXT PK, record_json TEXT, created_at TEXT, delivered BOOLEAN DEFAULT FALSE, delivered_at TEXT NULL)`.
- Write path: INSERT with fsync (`synchronous=FULL` ensures data hits disk).
- Read path: `SELECT WHERE delivered = FALSE ORDER BY created_at ASC LIMIT batch_size`.
- Delivery confirmation: `UPDATE SET delivered = TRUE, delivered_at = utcnow()`.
- Cleanup: `DELETE WHERE delivered = TRUE AND delivered_at < (now - retention)`.

#### Why SQLite Over Custom File

- Built-in crash recovery (WAL mode survives mid-write crashes).
- Built-in indexing (fast lookup of undelivered records).
- Proven durability (SQLite is the most deployed database engine in the world).
- Zero external dependencies (no Redis, no Kafka, no RabbitMQ).
- Suitable for single-writer (one gateway process writes to one WAL).

#### Retention and Exhaustion Behavior

| Mode | When Retention Exhausted | Rationale |
|------|--------------------------|-----------|
| Enforced | Stop accepting new requests (HTTP 503). Resume when WAL drains below threshold. | No ungoverned inference. Government customers accept brief interruption over incomplete audit. |
| Audit-Only | Drop oldest delivered records. If all undelivered: configurable (drop oldest undelivered with alert, or stop). | Best-effort recording. Alert operators to investigate control plane connectivity. |

### 5.6 Async Delivery Worker

The delivery worker runs as a background asyncio task within the gateway process. It reads undelivered records from the WAL and POSTs them to the control plane.

#### Delivery Loop

1. Query WAL for undelivered records (batch size: configurable, default 50).
2. For each record: POST to `/v1/gateway/executions` on the control plane.
3. On HTTP 200/201: mark record as delivered in WAL.
4. On HTTP 409 (duplicate): mark as delivered (idempotent, already recorded).
5. On HTTP 4xx (client error): log error, mark as failed, do not retry (bad data).
6. On HTTP 5xx or network error: exponential backoff (1s, 2s, 4s, 8s, ... max 60s). Record stays undelivered.
7. Sleep for `delivery_interval` (default: 1 second) between batches.

#### Backpressure

- If WAL undelivered count exceeds a high-water mark (default: 10,000 records), the delivery worker logs a warning and the health endpoint reports `degraded`.
- If WAL disk usage exceeds 80% of max, the health endpoint reports `critical`.
- In enforced mode, if WAL reaches 100% of max retention: gateway stops accepting requests.

### 5.7 Sync Client

The sync client keeps the attestation and policy caches current.

#### Startup Sync

1. On gateway startup: full sync of all attestations and policies for the configured tenant.
2. If control plane is unreachable at startup: **gateway refuses to start** (fail-closed). It cannot serve requests without at least one successful sync.
3. This ensures the gateway never operates with empty caches.

#### Runtime Sync

- **Pull (always active):** Every `sync_interval` (default: 60s), query control plane for changes since last sync timestamp. Update local caches.
- **Push (when available):** Maintain SSE connection to control plane. On attestation/policy change events, update local cache immediately. If SSE connection drops, fall back to pull-only until reconnected.

#### Control Plane Endpoints Used

```
# Attestation sync
GET /v1/gateway/sync/attestations?tenant_id={}&since={iso_timestamp}

# Policy sync
GET /v1/gateway/sync/policies?tenant_id={}&version={current_version}

# Push channel (future)
GET /v1/gateway/events?tenant_id={}  (SSE stream)
```

V1 implementation uses pull-only with polling. Push (SSE) is added when the control plane exposes the events endpoint.

### 5.8 Streaming Tee Pattern

For streaming responses (SSE from OpenAI/Anthropic), the gateway must simultaneously stream to the caller and buffer for hashing. This is a critical implementation detail.

#### Implementation

```python
async def stream_with_tee(adapter, call, request):
    upstream_req = await adapter.build_forward_request(call, request)
    buffer = []

    async def response_generator():
        async with httpx.AsyncClient(http2=True) as client:
            async with client.stream('POST', ...) as upstream:
                async for chunk in upstream.aiter_bytes():
                    buffer.append(chunk)    # buffer copy for hashing
                    yield chunk              # stream copy to caller

    # Return streaming response to caller
    # After generator exhausts, buffer contains full response
    response = StreamingResponse(response_generator(), ...)
    return response, buffer
```

The WAL write happens after the streaming response generator is fully consumed (all chunks yielded). A Starlette background task attached to the response performs the hash + WAL write after the last byte is sent.

### 5.9 Metrics and Health

The gateway exposes Prometheus metrics on `/metrics` and a health endpoint on `/health`. These are not routed through the pipeline — they are direct Starlette routes.

#### Key Metrics

| Metric | Type | Labels |
|--------|------|--------|
| `walacor_gateway_requests_total` | Counter | provider, model, outcome (allowed, blocked_attestation, blocked_policy, blocked_stale, error) |
| `walacor_gateway_pipeline_duration_seconds` | Histogram | step (resolve, policy, forward, hash, wal_write) |
| `walacor_gateway_forward_duration_seconds` | Histogram | provider |
| `walacor_gateway_wal_pending` | Gauge | — |
| `walacor_gateway_wal_oldest_pending_seconds` | Gauge | — |
| `walacor_gateway_wal_disk_bytes` | Gauge | — |
| `walacor_gateway_sync_last_success_seconds` | Gauge | cache_type (attestation, policy) |
| `walacor_gateway_cache_entries` | Gauge | cache_type |
| `walacor_gateway_delivery_total` | Counter | result (success, duplicate, error) |

#### Health Endpoint Response

```json
{
  "status": "healthy | degraded | fail_closed",
  "gateway_id": "gw-prod-east-001",
  "tenant_id": "walacor-bethesda",
  "enforcement_mode": "enforced",
  "attestation_cache": {
    "entries": 42,
    "last_sync": "2026-02-16T10:30:00Z",
    "stale": false
  },
  "policy_cache": {
    "version": 17,
    "last_sync": "2026-02-16T10:30:05Z",
    "stale": false
  },
  "wal": {
    "pending_records": 3,
    "oldest_pending_seconds": 2.1,
    "disk_usage_bytes": 1048576,
    "disk_usage_percent": 0.01
  },
  "uptime_seconds": 86423
}
```

---

## 6. Control Plane Changes Required

The existing FastAPI control plane needs minimal changes to support the gateway. These should be implemented before or in parallel with gateway Phase 1.

### 6.1 New Endpoint: POST /v1/gateway/executions

Accepts hash-only execution records from gateways. No `prompt_text` or `response_text` in the request body.

#### Request Schema

```python
class GatewayExecutionRequest(BaseModel):
    execution_id: str          # UUID generated by gateway
    model_attestation_id: str   # att_* or ver_*
    prompt_hash: str            # SHA3-512, 128 hex chars
    response_hash: str          # SHA3-512, 128 hex chars
    policy_version: int         # version of policy set evaluated
    policy_result: str          # 'pass' or 'blocked_by_policy'
    tenant_id: str
    gateway_id: str
    timestamp: str              # ISO 8601
    user: str | None = None
    session_id: str | None = None
    metadata: dict | None = None
```

#### Validation

- `execution_id`: valid UUID format
- `prompt_hash`, `response_hash`: exactly 128 hex characters
- `model_attestation_id`: exists in attestation store
- `tenant_id`: matches the gateway's registered tenant
- Idempotent: if `execution_id` already exists, return 409 Conflict (not an error)

### 6.2 New Endpoints: Gateway Sync

```
GET /v1/gateway/sync/attestations?tenant_id={}&since={iso_timestamp}
  Returns: list of attestation proofs changed since timestamp

GET /v1/gateway/sync/policies?tenant_id={}&version={int}
  Returns: full policy set if version differs, or 304 Not Modified
```

These are optional for V1 (the gateway can poll existing endpoints), but recommended for production to minimize payload size and control plane load.

---

## 7. Implementation Phases (8-Week Plan)

Each phase has clear deliverables, test checkpoints that double as compliance evidence, and acceptance criteria. Phases can overlap where dependencies allow.

### Phase 1: Foundation (Week 1–2)

**Goal:** Repository setup, shared library extraction, provider adapter interface, and the OpenAI adapter with basic forwarding (no governance yet).

#### Deliverables

1. **walacor-core package:** extract `hashing.py`, `policy_engine.py`, and shared models from the existing FastAPI codebase into a standalone Python package.
2. **walacor-gateway repository:** initial structure, `pyproject.toml` with walacor-core dependency, Starlette app skeleton.
3. **ProviderAdapter** abstract base class with `ModelCall`/`ModelResponse` types.
4. **OpenAI adapter:** parse request, build forward request, parse response, streaming support.
5. **Basic forwarder:** forward request to OpenAI, return response (pass-through, no governance).
6. **Streaming tee:** forward SSE stream while buffering.
7. **Configuration system:** pydantic-settings based config with validation.

#### Test Checkpoints

- Unit: `ModelCall`/`ModelResponse` serialization round-trip
- Unit: OpenAI adapter correctly extracts `model_id`, prompt from various request formats
- Integration: Forward a real (or mocked) OpenAI request, verify response identity (same bytes out as received)
- Integration: Stream tee produces identical bytes to caller AND identical buffered content for hashing
- Unit: walacor-core hash produces identical output to existing FastAPI hashing code

#### Acceptance Criteria

> *A request to the gateway's OpenAI endpoint is forwarded to OpenAI and the response is returned identically. Streaming works. The gateway adds zero governance at this point — it is a transparent proxy.*

---

### Phase 2: Attestation Gate (Week 2–3)

**Goal:** Attestation cache with fail-closed behavior. The gateway blocks requests to unattested models. Guarantee G1 is enforced.

#### Deliverables

1. **Attestation cache:** in-memory dict with TTL, populated from control plane on startup.
2. **Model resolver:** Step 1 of the pipeline — look up `(provider, model_id)` in cache.
3. **Fail-closed logic:** if cache expired and control plane unreachable, block request.
4. **Pull sync client:** periodic polling of `GET /v1/attestation-proofs`.
5. **Startup sync:** refuse to start if initial sync fails.

#### Test Checkpoints (Compliance Evidence)

- Integration (G1): Request with attested model → forwarded. Request with unknown model → HTTP 403.
- Integration (G1): Request with revoked model → HTTP 403.
- **Compliance (G1-fail-closed):** Make control plane unreachable, wait for cache TTL to expire, send request → verify HTTP 503 (not forwarded).
- **Compliance (G1-startup):** Start gateway with control plane unreachable → verify gateway refuses to start.
- Unit: Cache TTL expiry logic, refresh logic.

#### Acceptance Criteria

> *Only requests targeting attested, non-revoked models are forwarded. The gateway blocks all requests when it cannot verify attestation status. This is Guarantee G1 complete.*

---

### Phase 3: Policy Enforcement (Week 3–4)

**Goal:** Pre-inference policy evaluation. The gateway blocks requests that violate tenant policy. Guarantee G3 is enforced.

#### Deliverables

1. **Policy cache:** versioned, populated from control plane on startup.
2. **Policy evaluator:** Step 2 of the pipeline — hash prompt, evaluate against cached policies using `walacor-core.policy_engine`.
3. **Fail-closed logic:** if policy set stale beyond threshold and control plane unreachable, block request.
4. **Policy version tracking:** every execution record includes `policy_version`.
5. **Prompt redaction wrapper:** prompt text held in `RedactedString` type that returns `[REDACTED]` on `__repr__`/`__str__` to prevent accidental logging.

#### Test Checkpoints (Compliance Evidence)

- Integration (G3): Request with compliant prompt → forwarded. Request violating blocking policy → HTTP 403.
- **Compliance (G3-fail-closed):** Make control plane unreachable, wait for staleness threshold, send request → verify HTTP 503.
- **Compliance (G3-version):** Verify every execution record includes the correct `policy_version`.
- Unit: Policy evaluation produces identical results to control plane's evaluation (same walacor-core code).
- Security: Log output with `RedactedString` → verify no prompt text appears in logs.

#### Acceptance Criteria

> *Prompts that violate tenant policy are blocked before reaching the provider. The response never exists. Policy version is recorded for every execution. This is Guarantee G3 complete.*

---

### Phase 4: WAL and Cryptographic Recording (Week 4–5)

**Goal:** WAL implementation, SHA3-512 hashing of prompt and response, hash-only delivery to control plane. Guarantee G2 is enforced.

#### Deliverables

1. **SQLite WAL:** create database, `write_and_fsync`, read undelivered, mark delivered, cleanup.
2. **Post-inference capture:** hash prompt + response, build execution record, write to WAL.
3. **WAL timing:** non-streaming (WAL before response return), streaming (WAL after stream close via Starlette background task).
4. **Delivery worker:** background asyncio task, batch delivery, exponential backoff, idempotent handling.
5. **`POST /v1/gateway/executions`** endpoint on control plane (hash-only, idempotent).
6. **Retention monitoring:** disk usage tracking, high-water alerts, exhaustion behavior (enforced vs audit-only).

#### Test Checkpoints (Compliance Evidence)

- **Compliance (G2-durability):** Kill gateway process mid-request. Restart. Verify WAL record exists and is replayed to control plane.
- **Compliance (G2-hash-only):** Capture network traffic between gateway and control plane. Verify no plaintext prompt or response in any request body.
- **Compliance (G2-idempotent):** Send same `execution_id` twice to control plane. Verify 409 on second attempt, no duplicate in ledger.
- Integration: Verify WAL fsync timing: for non-streaming, WAL write completes before response returned.
- Integration: Verify hash values: gateway-computed hashes match what walacor-core would produce for same input.
- Load: Sustained 100 requests/second for 60 seconds. Verify all records delivered, WAL drains to zero.

#### Acceptance Criteria

> *Every execution is cryptographically recorded. Prompt and response are hashed locally. Only hashes cross the network to the control plane. Records survive gateway crashes. This is Guarantee G2 complete. At this point, all three guarantees (G1 + G2 + G3) are enforced.*

---

### Phase 5: Observability (Week 5–6)

**Goal:** Prometheus metrics, health endpoint, and alerting foundation.

#### Deliverables

- Prometheus metrics: all counters, histograms, and gauges defined in design document Section 11.
- `/metrics` endpoint: Prometheus text format.
- `/health` endpoint: JSON response with cache status, WAL status, sync status, enforcement mode.
- Structured logging: JSON logs with correlation IDs, no prompt/response text.
- Grafana dashboard template (optional but high-value for demos).

#### Test Checkpoints

- Integration: Send requests, verify metrics increment correctly for each outcome type.
- Integration: Make control plane unreachable, verify health endpoint reports `degraded` then `fail_closed`.
- Integration: Fill WAL, verify `wal_pending` and `wal_disk_bytes` metrics track correctly.
- Security: Verify no prompt or response text in any log output at any log level.

---

### Phase 6: Deployment Packaging (Week 6–7)

**Goal:** Docker images, Helm chart, network policy references, and docker-compose for development.

#### Deliverables

- Multi-stage Dockerfile: build stage (compile dependencies) + runtime stage (minimal, non-root, read-only filesystem where possible).
- `Dockerfile.fips`: variant using FIPS 140-2 validated cryptographic modules (for government).
- Helm chart: deployment, service, configmap, secrets, horizontal pod autoscaler, pod disruption budget.
- `values.yaml`: sane defaults for commercial deployment.
- `values-govcloud.yaml`: overrides for GovCloud (FIPS image, strict network policy, enhanced logging).
- Network policy references: Kubernetes NetworkPolicy YAML, AWS Security Group Terraform, air-gapped iptables script.
- `docker-compose.yml`: gateway + control plane + mock OpenAI for local development and demos.

#### Test Checkpoints

- Docker: build, start, forward a request, verify response. Container healthcheck passes.
- Helm: deploy to local K8s (kind/minikube), verify end-to-end flow.
- Network policy: apply egress policy, verify direct-to-provider requests are blocked from non-gateway pods.

---

### Phase 7: Additional Adapters (Week 7–8)

**Goal:** Anthropic adapter, HuggingFace adapter, Generic adapter. Broader provider coverage.

#### Deliverables

- Anthropic adapter: `/v1/messages`, SSE streaming, `x-api-key` auth header.
- HuggingFace adapter: TGI format, Inference Endpoints format.
- Generic adapter: configurable JSON paths for model_id, prompt, response.
- Adapter auto-detection: route incoming requests to the correct adapter based on URL path patterns.

#### Test Checkpoints

- Per adapter: forward + hash + stream correctness (same pattern as OpenAI adapter tests).
- Generic adapter: configure for a custom API format, verify correct extraction and forwarding.
- Multi-adapter: gateway handles requests to different providers on different paths simultaneously.

---

### Phase 8: Hardening and Documentation (Week 8)

**Goal:** Production hardening, security review, compliance documentation, and launch preparation.

#### Deliverables

- Security audit: review all error responses for information leakage (same pattern as 422 sanitization).
- Rate limiting: configurable per-tenant request rate limits at the gateway.
- Graceful shutdown: on SIGTERM, stop accepting new requests, wait for in-flight requests to complete, flush WAL, then exit.
- Connection pooling: httpx connection pool tuning for upstream providers.
- `QUICKSTART.md`: 5-minute guide to running the gateway locally.
- `CONFIGURATION.md`: complete reference for all environment variables.
- `ADAPTERS.md`: guide to writing custom adapters.
- `COMPLIANCE-EVIDENCE.md`: guide to running the compliance test suite and interpreting results for ATO packages.
- Compliance test suite: all G1/G2/G3 compliance tests organized as a runnable suite that generates a structured evidence report.

---

## 8. Timeline Summary

| # | Phase | Timeline | Guarantees | Key Milestone |
|---|-------|----------|------------|---------------|
| 1 | Foundation | Week 1–2 | — | Transparent proxy working |
| 2 | Attestation Gate | Week 2–3 | G1 | Unattested models blocked |
| 3 | Policy Enforcement | Week 3–4 | G1 + G3 | Policy violations blocked pre-inference |
| 4 | WAL + Recording | Week 4–5 | **G1+G2+G3** | All guarantees enforced (MVP) |
| 5 | Observability | Week 5–6 | — | Prometheus + health endpoint |
| 6 | Deployment | Week 6–7 | — | Docker + Helm + network policies |
| 7 | Additional Adapters | Week 7–8 | — | Anthropic + HF + Generic |
| 8 | Hardening | Week 8 | — | Production-ready, compliance evidence |

> *Critical milestone: End of Phase 4 (Week 5). At this point all three guarantees are enforced and the gateway is functionally complete. Phases 5–8 are about operability, packaging, and breadth — not core security.*

---

## 9. Risk Register

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Streaming tee adds latency or drops chunks | High | Medium | Extensive integration tests with real SSE streams; fallback to buffer-then-forward mode |
| 2 | SQLite WAL fsync performance under high throughput | Medium | Low | Benchmark at 1000 req/s; if insufficient, switch to custom append-only file |
| 3 | walacor-core extraction breaks existing control plane | High | Medium | Run full control plane test suite after extraction; pin walacor-core version |
| 4 | Provider API format changes break adapters | Medium | Medium | Version-specific adapters; adapter auto-detection with graceful fallback |
| 5 | Government customer needs transparent proxy for V1 | High | Low | Assess specific customer requirement; transparent proxy is architecturally possible, just deferred |
| 6 | Policy evaluation performance differs between gateway and control plane | High | Low | Shared walacor-core code; add differential test (same input, both paths, compare output) |

---

## 10. Success Metrics

The gateway MVP is successful when the following criteria are met:

| Metric | Target | Measurement |
|--------|--------|-------------|
| Latency overhead (non-streaming) | < 5ms added (excluding provider round-trip) | p99 of `pipeline_duration` minus `forward_duration` |
| Latency overhead (streaming) | Zero visible delay on first chunk | Time-to-first-byte compared to direct |
| Throughput | ≥ 500 requests/second sustained (single instance) | Load test with locust/k6 |
| Audit completeness | 100% of requests have ledger records (at-least-once) | Compare gateway WAL count vs control plane execution count |
| Fail-closed correctness | 0% of requests forwarded when attestation/policy cache stale | Compliance test suite |
| WAL durability | 0 records lost across 100 crash-recovery cycles | Automated crash test |
| Hash consistency | 100% match between gateway and control plane hashes | Differential test with shared inputs |

---

## Document Approval

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Technical Architect | | | |
| Engineering Lead | | | |
| Product Owner | | | |
| Security Officer | | | |