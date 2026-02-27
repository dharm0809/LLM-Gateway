# Walacor AI Security Gateway

claude --resume 13dfffec-3d46-4c2b-8c2a-3c3caadb0946 

**The governance enforcement and cryptographic audit layer for enterprise AI infrastructure.**

A production-grade, drop-in ASGI proxy that integrates with any LLM provider without changing application code. The gateway enforces five security guarantees on every inference request — model attestation, full-fidelity audit recording, pre-inference policy, post-inference content analysis, and session chain integrity — while feeding a cryptographic audit trail to the Walacor control plane. Providers stay providers; the governance layer is Walacor's.

---

## What it guarantees

| Guarantee | Description |
|---|---|
| **G1 — Model Attestation** | Every request is matched to a cryptographically attested model record. Unknown or unattested models are fail-closed. |
| **G2 — Full-fidelity audit** | Prompt text, response content, provider request ID, and model hash are persisted directly to Walacor's backend (or SQLite WAL when offline). Walacor's backend hashes on ingest — the gateway sends the full record. |
| **G3 — Pre-inference Policy** | Requests are evaluated against the active policy before being forwarded. Stale policies fail closed. |
| **G4 — Post-inference Content Gate** | Model responses are evaluated by pluggable content analyzers (PII, toxicity, custom) before being returned to the caller. |
| **G5 — Session Chain Integrity** | Conversation turns are linked via a Merkle chain (SHA3-512 over canonical fields), enabling tamper detection by the control plane. |

**Completeness Invariant:** `GEN_ATTEMPT = GEN + GEN_DENY + GEN_ERROR`
Every request — regardless of failure point — produces exactly one row in `gateway_attempts`. Auth failures, parse errors, and provider timeouts are all counted.

---

## Architecture

```
Client
  │  POST /v1/chat/completions (or /v1/messages, /v1/completions, /v1/custom, /generate)
  ▼
┌─────────────────────────────────────────────────────┐
│  completeness_middleware  ← outermost, always runs  │
│  ┌────────────────────────────────────────────────┐ │
│  │  api_key_middleware    ← inner, auth check     │ │
│  │  ┌──────────────────────────────────────────┐ │ │
│  │  │  orchestrator (8-step pipeline)          │ │ │
│  │  │                                          │ │ │
│  │  │  1. G1  Attestation lookup / refresh     │ │ │
│  │  │  2. G3  Pre-inference policy eval        │ │ │
│  │  │  2.5    WAL backpressure gate            │ │ │
│  │  │  2.6    Token budget check               │ │ │
│  │  │  3.     Forward to provider              │ │ │
│  │  │  4. G4  Post-inference content gate      │ │ │
│  │  │  5.     Token usage recording            │ │ │
│  │  │  6. G2  Build execution record            │ │ │
│  │  │  7. G5  Session Merkle chain             │ │ │
│  │  │  8. G2  Audit write (Walacor or WAL)      │ │ │
│  │  └──────────────────────────────────────────┘ │ │
│  └────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
  │
  ▼
Provider (OpenAI / Anthropic / HuggingFace / Ollama / Generic)
```

Streaming responses use a tee-buffer: chunks are forwarded to the caller in real time while being accumulated for post-stream audit recording via a Starlette `BackgroundTask`.

When `WALACOR_SERVER`, `WALACOR_USERNAME`, and `WALACOR_PASSWORD` are all set, the gateway writes directly to Walacor's backend — step 8 calls `POST /envelopes/submit` via `WalacorClient` instead of writing to SQLite. The SQLite WAL and delivery worker are not started. When those variables are unset, the gateway falls back to the SQLite WAL + delivery worker path.

### Data plane ↔ Control plane boundary

```
┌──────────────────────────────────────────────────────────────────┐
│                         DATA PLANE                               │
│                                                                  │
│   Client ──► Gateway ──► Provider                                │
│                │                                                 │
│                │  sync (attestations + policies, pull every 60s) │
│                │◄────────────────────────────────┐               │
│                │                                 │               │
│                │  direct write (WalacorClient)   │               │
│                │  POST /envelopes/submit          │               │
│                │  ── executions (ETId 9000001)   │               │
│                │  ── attempts   (ETId 9000002)   │               │
│                └────────────────────────────────►│               │
│                                                  │               │
└──────────────────────────────────────────────────┼───────────────┘
                                                   │
                                    ┌──────────────▼──────────────┐
                                    │       WALACOR BACKEND        │
                                    │                              │
                                    │  • Attestation registry      │
                                    │  • Policy store              │
                                    │  • walacor_gw_executions     │
                                    │  • walacor_gw_attempts       │
                                    │  • Chain integrity verifier  │
                                    └──────────────────────────────┘
```

The gateway writes full execution records directly to Walacor's backend via an authenticated HTTP client (`WalacorClient`). Walacor's backend handles hashing and long-term storage. In offline/fallback mode (no Walacor credentials), the gateway buffers records in a local SQLite WAL and delivers them when connectivity is restored. If the policy cache expires while the control plane is unreachable, the gateway fails closed.

---

## Execution record fields (G2)

Every allowed request produces one `ExecutionRecord` written to Walacor's backend (or the SQLite WAL in fallback mode):

| Field | Description |
|---|---|
| `execution_id` | UUID generated by the gateway for this turn |
| `prompt_text` | Actual prompt text sent to the model |
| `response_content` | Actual response content returned by the model |
| `provider_request_id` | ID assigned by the provider (e.g. `chatcmpl-xxx`, `msg_xxx`) — the interaction-level identifier from the model/user exchange |
| `model_hash` | Hash of the model weights/binary from the MEE (e.g. Ollama digest from `/api/show`). `null` for cloud providers. |
| `model_attestation_id` | Attestation record ID from the control plane |
| `policy_version` | Policy version applied to this request |
| `policy_result` | `pass`, `blocked`, or `flagged` |
| `tenant_id` | Tenant this request belongs to |
| `gateway_id` | Gateway instance that processed the request |
| `timestamp` | ISO 8601 UTC timestamp |
| `session_id` | Session identifier (if provided by caller) |
| `sequence_number` | Turn index within the session (G5) |
| `previous_record_hash` | Hash of the preceding turn in the session chain (G5) |
| `record_hash` | SHA3-512 Merkle hash of this record's canonical fields (G5) |

---

## Supported providers

| Route | Provider | Adapter | Notes |
|---|---|---|---|
| `/v1/chat/completions`, `/v1/completions` | OpenAI and compatibles | `OpenAIAdapter` | Extracts `chatcmpl-xxx` provider request ID |
| `/v1/chat/completions`, `/v1/completions` | Ollama (local MEE) | `OllamaAdapter` | Fetches model digest from `/api/show`; select with `WALACOR_GATEWAY_PROVIDER=ollama` |
| `/v1/messages` | Anthropic Claude | `AnthropicAdapter` | Extracts `msg_xxx` provider request ID |
| `/generate` | HuggingFace Inference Endpoints | `HuggingFaceAdapter` | |
| `/v1/custom` | Any REST API | `GenericAdapter` | JSONPath config |

### Multi-model routing (single instance, multiple providers)

By default, all `/v1/chat/completions` requests route to the same provider configured via `WALACOR_GATEWAY_PROVIDER`. For deployments that need to serve GPT-4, Llama 3, and Claude from a single gateway instance, use the model routing table.

Set `WALACOR_MODEL_ROUTING_JSON` to a JSON array of routing rules. Each rule uses an fnmatch pattern matched against the request's `model` field (case-insensitive). The first matching rule wins; unmatched models fall through to path-based routing.

```bash
export WALACOR_MODEL_ROUTING_JSON='[
  {"pattern": "gpt-*",    "provider": "openai",    "url": "https://api.openai.com",     "key": "sk-..."},
  {"pattern": "claude-*", "provider": "anthropic", "url": "https://api.anthropic.com",  "key": "sk-ant-..."},
  {"pattern": "llama*",   "provider": "ollama",    "url": "http://localhost:11434",      "key": ""}
]'
```

With this config, `POST /v1/chat/completions` with `"model": "gpt-4"` routes to OpenAI; `"model": "llama3.2"` routes to Ollama — all through the same gateway instance and the same audit trail.

The value can also be a file path to a JSON file (same convention as `WALACOR_MCP_SERVERS_JSON`):

```bash
export WALACOR_MODEL_ROUTING_JSON=/etc/gateway/routes.json
```

### Connecting to a local MEE (Ollama / LM Studio)

The gateway is designed to act as the governance layer in front of Model Execution Environments (MEEs) like Ollama or LM Studio running on-device or on-prem.

**Ollama:**
```bash
# Select the Ollama adapter; point at Ollama's OpenAI-compat endpoint
export WALACOR_GATEWAY_PROVIDER=ollama
export WALACOR_PROVIDER_OLLAMA_URL=http://localhost:11434
walacor-gateway
```
The `OllamaAdapter` calls `POST /api/show` to retrieve the model's SHA256 digest from the Ollama registry and stores it as `model_hash` in the execution record. Digests are cached per model name for the lifetime of the process.

**LM Studio (and other OpenAI-compat servers):**
```bash
# LM Studio exposes the OpenAI-compat API — use the standard OpenAI adapter
export WALACOR_PROVIDER_OPENAI_URL=http://localhost:1234
walacor-gateway
```
LM Studio has no `/api/show` endpoint, so `model_hash` will be `null` in the execution record. The provider request ID (`chatcmpl-xxx`) is still captured.

---

## Quick start

```bash
# Install
pip install -e ./walacor-core
pip install -e "./Gateway[dev]"

# --- Walacor backend storage (recommended) ---
# Copy the example env file and fill in your Walacor credentials
cp .env.gateway.example .env.gateway
# edit .env.gateway: set WALACOR_SERVER, WALACOR_USERNAME, WALACOR_PASSWORD

# Transparent proxy mode + Walacor storage (skip governance, records go to Walacor)
export WALACOR_SKIP_GOVERNANCE=true
export WALACOR_SERVER=https://sandbox.walacor.com/api
export WALACOR_USERNAME=your-username
export WALACOR_PASSWORD=your-password
export WALACOR_GATEWAY_TENANT_ID=your-tenant
export WALACOR_PROVIDER_OPENAI_KEY=sk-...
walacor-gateway

# Full governance mode + Walacor storage — OpenAI
export WALACOR_GATEWAY_TENANT_ID=tenant-abc
export WALACOR_CONTROL_PLANE_URL=https://control.example.com
export WALACOR_SERVER=https://your-walacor-server/api
export WALACOR_USERNAME=your-username
export WALACOR_PASSWORD=your-password
export WALACOR_PROVIDER_OPENAI_KEY=sk-...
walacor-gateway

# Full governance mode — Ollama (local MEE, fallback WAL storage)
export WALACOR_GATEWAY_TENANT_ID=tenant-abc
export WALACOR_CONTROL_PLANE_URL=https://control.example.com
export WALACOR_GATEWAY_PROVIDER=ollama
export WALACOR_PROVIDER_OLLAMA_URL=http://localhost:11434
walacor-gateway
```

Point any OpenAI-compatible client at `http://localhost:8000`.

---

## Configuration

All variables use the `WALACOR_` prefix. Can also be placed in `.env` or `.env.gateway` (see `.env.gateway.example`). The gateway validates required fields at startup and fails fast if anything is missing.

### Walacor backend storage

Set all three to activate direct writes to Walacor. When any one is missing the gateway falls back to the SQLite WAL.

| Variable | Default | Description |
|---|---|---|
| `WALACOR_SERVER` | `""` | Walacor backend URL (e.g. `https://sandbox.walacor.com/api`) |
| `WALACOR_USERNAME` | `""` | Walacor backend username |
| `WALACOR_PASSWORD` | `""` | Walacor backend password |
| `WALACOR_EXECUTIONS_ETID` | `9000001` | Schema ETId for execution records (`walacor_gw_executions`) |
| `WALACOR_ATTEMPTS_ETID` | `9000002` | Schema ETId for attempt records (`walacor_gw_attempts`) |

### Core

| Variable | Default | Description |
|---|---|---|
| `WALACOR_GATEWAY_TENANT_ID` | *(required)* | Tenant identifier |
| `WALACOR_CONTROL_PLANE_URL` | *(required unless skip_governance=true)* | Control plane base URL |
| `WALACOR_GATEWAY_API_KEYS` | `""` | Comma-separated API keys for caller auth. Empty = no auth required. |
| `WALACOR_CONTROL_PLANE_API_KEY` | `""` | Key the gateway sends when calling the control plane |
| `WALACOR_GATEWAY_ID` | `gw-<random>` | Stable instance identifier |
| `WALACOR_SKIP_GOVERNANCE` | `false` | `true` = transparent proxy (no attestation or policy); storage still active. A shared HTTP client with connection pooling is initialised in both modes. |
| `WALACOR_ENFORCEMENT_MODE` | `enforced` | `enforced` blocks on violations; `audit_only` forwards and logs shadow blocks |

### Caches & sync

| Variable | Default | Description |
|---|---|---|
| `WALACOR_ATTESTATION_CACHE_TTL` | `300` | Attestation cache TTL (seconds) |
| `WALACOR_POLICY_STALENESS_THRESHOLD` | `900` | Max policy age before fail-closed (seconds) |
| `WALACOR_SYNC_INTERVAL` | `60` | Pull-sync interval (seconds) |
| `WALACOR_GATEWAY_PROVIDER` | `openai` | Active provider (`openai`, `ollama`, `anthropic`, etc.) — used for attestation sync and adapter selection |

### WAL

| Variable | Default | Description |
|---|---|---|
| `WALACOR_WAL_PATH` | `/var/walacor/wal` | WAL database directory |
| `WALACOR_WAL_MAX_SIZE_GB` | `10.0` | Max WAL disk usage before fail-closed |
| `WALACOR_WAL_MAX_AGE_HOURS` | `72.0` | Max WAL record age |
| `WALACOR_WAL_HIGH_WATER_MARK` | `10000` | Undelivered record limit before rejecting new requests |
| `WALACOR_MAX_STREAM_BUFFER_BYTES` | `10485760` | Streaming tee buffer cap (10 MB) |

### Completeness tracking

| Variable | Default | Description |
|---|---|---|
| `WALACOR_COMPLETENESS_ENABLED` | `true` | Write one `gateway_attempts` row per request |
| `WALACOR_ATTEMPTS_RETENTION_HOURS` | `168` | Retention for attempt records (7 days) |

### Response policy / content analysis

| Variable | Default | Description |
|---|---|---|
| `WALACOR_RESPONSE_POLICY_ENABLED` | `true` | Enable post-inference content gate (G4) |
| `WALACOR_PII_DETECTION_ENABLED` | `true` | Enable built-in PII detector (`walacor.pii.v1`) |
| `WALACOR_TOXICITY_DETECTION_ENABLED` | `false` | Enable built-in toxicity detector (`walacor.toxicity.v1`) |
| `WALACOR_TOXICITY_DENY_TERMS` | `""` | Comma-separated extra deny terms added to toxicity detector |

### Token budget

| Variable | Default | Description |
|---|---|---|
| `WALACOR_TOKEN_BUDGET_ENABLED` | `false` | Enable token budget enforcement |
| `WALACOR_TOKEN_BUDGET_PERIOD` | `monthly` | `daily` or `monthly` |
| `WALACOR_TOKEN_BUDGET_MAX_TOKENS` | `0` | Max tokens per period per tenant (`0` = unlimited) |

### Session chain

| Variable | Default | Description |
|---|---|---|
| `WALACOR_SESSION_CHAIN_ENABLED` | `true` | Enable Merkle chain for session records (G5) |
| `WALACOR_SESSION_CHAIN_MAX_SESSIONS` | `10000` | Max concurrent sessions in memory (ignored when Redis is configured) |
| `WALACOR_SESSION_CHAIN_TTL` | `3600` | Session state TTL — inactive sessions evicted (seconds) |

### Redis (multi-replica state sharing)

| Variable | Default | Description |
|---|---|---|
| `WALACOR_REDIS_URL` | `""` | Redis connection URL (e.g. `redis://redis-svc:6379/0`). When set, session chain and budget state are shared across all replicas via Redis. When empty, in-memory trackers are used (single-replica only). |
| `WALACOR_MODEL_ROUTING_JSON` | `""` | JSON array of model routing rules (see [Multi-model routing](#multi-model-routing-single-instance-multiple-providers)), or a path to a JSON file. |
| `WALACOR_UVICORN_WORKERS` | `1` | Uvicorn worker count. Can be set to `>1` **only when `WALACOR_REDIS_URL` is configured** — without Redis, each worker has independent in-memory state and session chains / budget counters will diverge across workers. |

Install the Redis client library via the optional extra:

```bash
pip install "walacor-gateway[redis]"
```

### Provider URLs and keys

| Variable | Default | Description |
|---|---|---|
| `WALACOR_PROVIDER_OPENAI_URL` | `https://api.openai.com` | OpenAI base URL (also used for LM Studio, vLLM, etc.) |
| `WALACOR_PROVIDER_OPENAI_KEY` | `""` | OpenAI API key |
| `WALACOR_PROVIDER_OLLAMA_URL` | `http://localhost:11434` | Ollama base URL |
| `WALACOR_PROVIDER_OLLAMA_KEY` | `""` | Ollama API key (usually empty for local deployments) |
| `WALACOR_PROVIDER_ANTHROPIC_URL` | `https://api.anthropic.com` | Anthropic base URL |
| `WALACOR_PROVIDER_ANTHROPIC_KEY` | `""` | Anthropic API key |
| `WALACOR_PROVIDER_HUGGINGFACE_URL` | `""` | HuggingFace Inference Endpoint URL |
| `WALACOR_PROVIDER_HUGGINGFACE_KEY` | `""` | HuggingFace API key |
| `WALACOR_GENERIC_UPSTREAM_URL` | `""` | Generic adapter upstream URL |
| `WALACOR_GENERIC_MODEL_PATH` | `$.model` | JSONPath to model ID in request |
| `WALACOR_GENERIC_PROMPT_PATH` | `$.messages[*].content` | JSONPath to prompt in request |
| `WALACOR_GENERIC_RESPONSE_PATH` | `$.choices[0].message.content` | JSONPath to content in response |

### Observability

| Variable | Default | Description |
|---|---|---|
| `WALACOR_METRICS_ENABLED` | `true` | Expose Prometheus metrics at `/metrics` |
| `WALACOR_LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## Endpoints

| Path | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | OpenAI / Ollama / LM Studio chat completions proxy |
| `/v1/completions` | POST | OpenAI text completions proxy |
| `/v1/messages` | POST | Anthropic Messages proxy |
| `/v1/custom` | POST | Generic adapter proxy |
| `/generate` | POST | HuggingFace inference proxy |
| `/health` | GET | JSON health status |
| `/metrics` | GET | Prometheus text format metrics |

### `/health` response

**Walacor backend storage mode:**

```json
{
  "status": "healthy",
  "gateway_id": "gw-a1b2c3d4e5f6",
  "tenant_id": "tenant-abc",
  "enforcement_mode": "enforced",
  "uptime_seconds": 3600,
  "storage": {
    "backend": "walacor",
    "server": "https://sandbox.walacor.com/api",
    "executions_etid": 9000001,
    "attempts_etid": 9000002
  },
  "attestation_cache": { "entries": 12, "last_sync": "2026-02-18T10:00:00Z", "stale": false },
  "policy_cache":      { "version": 7,  "last_sync": "2026-02-18T10:00:00Z", "stale": false },
  "token_budget": {
    "period": "monthly",
    "period_start": "2026-02-01T00:00:00Z",
    "tokens_used": 142000,
    "max_tokens": 1000000,
    "percent_used": 14.2
  },
  "session_chain": { "active_sessions": 3 }
}
```

When the **Redis tracker** is active, `active_sessions` is reported as `"unavailable"` (counting all keys by prefix is too expensive in Redis). The Prometheus gauge `walacor_gateway_session_chain_active` is only updated when the in-memory tracker is in use.

```json
  "session_chain": { "active_sessions": "unavailable" }
}
```

**SQLite WAL fallback mode:**

```json
{
  "status": "healthy",
  ...
  "wal": {
    "pending_records": 0,
    "oldest_pending_seconds": null,
    "disk_usage_bytes": 4096,
    "disk_usage_percent": 0.0
  }
}
```

`status` values: `healthy` → `degraded` → `fail_closed`

---

## Audit-only mode

Set `WALACOR_ENFORCEMENT_MODE=audit_only` to run in shadow mode:

- Every request is **forwarded** regardless of attestation, policy, budget, or content violations
- Violations are logged as warnings and recorded in the execution record as `would_have_blocked: true` with a `would_have_blocked_reason` field
- The control plane receives complete audit trails, enabling safe baseline measurement before switching to `enforced`

---

## Content analyzers (G4)

**This is the extensibility point.** The `ContentAnalyzer` plugin interface is how the gateway addresses the "semantic blindness" gap in traditional proxy-based AI security tools — proxies that hash and record traffic without understanding what the model actually said. Every analyzer runs concurrently under an enforced per-analyzer timeout, returns a typed `Decision` (verdict, confidence, category, reason), and never stores or logs the text it analyzed.

The gateway ships two built-in analyzers. Any number of custom analyzers can be added without touching the pipeline.

### Built-in: `walacor.pii.v1`

Regex-based, deterministic, zero external dependencies. Detects:

| Pattern | Example |
|---|---|
| Credit card numbers | Visa, Mastercard, Amex, Discover (Luhn-plausible) |
| US Social Security Numbers | `123-45-6789` |
| Email addresses | `user@example.com` |
| US phone numbers | All common formats |
| IPv4 addresses | `192.168.1.1` |
| AWS access key IDs | `AKIAIOSFODNN7EXAMPLE` |
| API keys / tokens / secrets | `api_key: abc123...` patterns |

Returns `BLOCK` with `confidence=0.99` on first match. No detected content is logged or stored.

### Built-in: `walacor.toxicity.v1`

Deny-list pattern matching. Default categories:

| Category | Verdict |
|---|---|
| `self_harm_indicator` | WARN |
| `violence_instruction` | WARN |
| `child_safety` | BLOCK |
| `custom_deny_list` | WARN |

Extra terms added at startup via `WALACOR_TOXICITY_DENY_TERMS=term1,term2`.

### Writing a custom analyzer

Implement the `ContentAnalyzer` ABC from `gateway.content`:

```python
from gateway.content import ContentAnalyzer, Decision, Verdict

class MyAnalyzer(ContentAnalyzer):
    @property
    def analyzer_id(self) -> str:
        return "acme.my_analyzer.v1"

    @property
    def timeout_ms(self) -> int:
        return 100  # gateway enforces this via asyncio.wait_for

    async def analyze(self, text: str) -> Decision:
        if "forbidden" in text.lower():
            return Decision(
                verdict=Verdict.BLOCK,
                confidence=0.95,
                analyzer_id=self.analyzer_id,
                category="custom",
                reason="forbidden_term",
            )
        return Decision(
            verdict=Verdict.PASS,
            confidence=1.0,
            analyzer_id=self.analyzer_id,
            category="custom",
            reason="clean",
        )
```

Register it by appending to `ctx.content_analyzers` in `main.py`'s `_init_content_analyzers()`.

Verdict semantics:

| Verdict | Enforced mode | Audit-only mode |
|---|---|---|
| `PASS` | Response returned | Response returned |
| `WARN` | Response returned, `flagged` in WAL record | Same |
| `BLOCK` | 403 returned to caller | Forwarded, `would_have_blocked=true` in WAL |

---

## Horizontal scaling

The gateway supports horizontal scaling when Redis is configured. Without Redis, all replica-level state (session chains, budget counters) is in-process memory and diverges across pods.

```
                  ┌──────────────┐
  ──► replica 1 ──┤              ├── provider
                  │  Redis 7     │
  ──► replica 2 ──┤  (shared     ├── provider
                  │   state)     │
  ──► replica 3 ──┤              ├── provider
                  └──────────────┘
```

**With Redis (`WALACOR_REDIS_URL` set):**
- Session chain state (`gateway:session:{id}`) — `next_chain_values` is a read-only HGET (no pre-increment); `update()` atomically writes both `seq` and `hash` in one pipeline. This eliminates false chain gaps on transient write failures and ensures seq=0 for the first record in every session, matching in-memory behaviour.
- Budget counters (`gateway:budget:{tenant}:{user}:{period}`) — Lua atomic check-and-reserve with `estimated` tokens; after each LLM response, `record_usage` applies the `actual − estimated` delta via `INCRBY`/`DECRBY` so the counter tracks real token consumption.
- `WALACOR_UVICORN_WORKERS` can be set to `>1`

**Without Redis:**
- In-memory trackers are used (original behavior, no dependency change)
- Keep `WALACOR_UVICORN_WORKERS=1`

**Docker Compose with Redis:**
```bash
docker-compose --profile redis up
```

**Kubernetes (Helm):** Set `WALACOR_REDIS_URL: "redis://redis-svc:6379/0"` in `values.yaml` and configure a Redis deployment alongside the gateway.

---

## Session chain (G5)

When a request includes a `session_id`, the gateway links consecutive turns via a Merkle chain:

```
record_hash = SHA3-512(
    execution_id | policy_version | policy_result |
    previous_record_hash | sequence_number | timestamp
)
```

- First record uses `previous_record_hash = "000...000"` (128 zeros — genesis) and `sequence_number = 0` in both in-memory and Redis trackers
- Each subsequent record chains off the previous `record_hash`; `sequence_number` increments per turn within the session
- Sessions are evicted from memory after `WALACOR_SESSION_CHAIN_TTL` seconds of inactivity
- The control plane can detect tampering via broken hash chains and missing sequence numbers
- With Redis: `next_chain_values` is read-only — the seq counter is only advanced inside `update()` after a successful write, so transient write failures never create phantom sequence gaps

**Current scope:** chain integrity is enforced within the Walacor system. External anchoring (RFC 3161 timestamp authority or distributed ledger) — which would make the chain independently verifiable outside of Walacor infrastructure — is on the roadmap for V2 (see [Roadmap](#roadmap)).

---

## Prometheus metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `walacor_gateway_requests_total` | Counter | `provider`, `model`, `outcome` | Request outcomes |
| `walacor_gateway_attempts_total` | Counter | `disposition` | Completeness invariant — every attempt |
| `walacor_gateway_pipeline_duration_seconds` | Histogram | `step` | Pipeline step timing |
| `walacor_gateway_forward_duration_seconds` | Histogram | `provider` | Upstream latency |
| `walacor_gateway_response_policy_total` | Counter | `result` | G4 outcomes (`pass`, `blocked`, `flagged`, `skipped`) |
| `walacor_gateway_token_usage_total` | Counter | `tenant_id`, `provider`, `token_type` | Token consumption |
| `walacor_gateway_budget_exceeded_total` | Counter | `tenant_id` | Budget exhaustion events |
| `walacor_gateway_session_chain_active` | Gauge | — | Active session chain count |
| `walacor_gateway_wal_pending` | Gauge | — | Undelivered WAL records |
| `walacor_gateway_wal_disk_bytes` | Gauge | — | WAL disk usage |
| `walacor_gateway_wal_oldest_pending_seconds` | Gauge | — | Age of oldest undelivered record |
| `walacor_gateway_cache_entries` | Gauge | `cache_type` | Cache entry counts |
| `walacor_gateway_sync_last_success_seconds` | Gauge | `cache_type` | Seconds since last successful sync |
| `walacor_gateway_delivery_total` | Counter | `result` | WAL delivery outcomes |

`disposition` label values for `walacor_gateway_attempts_total`:

| Value | Meaning |
|---|---|
| `allowed` | Request completed and recorded |
| `denied_auth` | API key missing or invalid |
| `denied_attestation` | Model not attested |
| `denied_policy` | Pre-inference policy block |
| `denied_response_policy` | G4 content block |
| `denied_budget` | Token budget exhausted |
| `denied_wal_full` | WAL backpressure limit hit |
| `error_gateway` | Internal gateway error |
| `error_parse` | Request body could not be parsed |
| `error_provider` | Provider returned 5xx |
| `error_no_adapter` | No adapter for requested path |

---

## Storage backends

### Walacor backend (default when credentials are set)

When `WALACOR_SERVER`, `WALACOR_USERNAME`, and `WALACOR_PASSWORD` are all configured, the gateway writes directly to Walacor using `WalacorClient`:

- **`walacor_gw_executions`** (ETId=9000001) — full execution records: prompt text, response content, provider request ID, model hash, session chain fields
- **`walacor_gw_attempts`** (ETId=9000002) — one row per request (completeness invariant), all dispositions

`WalacorClient` authenticates with username/password (`POST /auth/login`), receives a JWT Bearer token, and includes it in every `POST /envelopes/submit` call. Token management features:

- **Proactive refresh** — background task wakes before JWT expiry (5 min lead time) and re-authenticates silently, avoiding 401 latency spikes under load
- **Reactive re-auth** — on any 401 the client re-authenticates immediately and retries the write once
- **Concurrency safety** — `asyncio.Lock` ensures the proactive refresh loop and reactive retry never call `/auth/login` concurrently

### SQLite WAL (fallback)

When Walacor credentials are not set, the gateway uses SQLite in WAL mode (`synchronous=FULL`) as a crash-safe append-only log:

- **`wal_records`** — execution records; delivered to the control plane by the background delivery worker
- **`gateway_attempts`** — one row per request for all dispositions (local telemetry only)

A background delivery worker retries undelivered records with exponential backoff (1 s initial, 60 s cap). The gateway is **fail-closed** when:

- Policy cache is stale beyond `WALACOR_POLICY_STALENESS_THRESHOLD`
- WAL pending count ≥ `WALACOR_WAL_HIGH_WATER_MARK`
- WAL disk usage ≥ `WALACOR_WAL_MAX_SIZE_GB`

### Completeness invariant implementation note

`gateway_attempts` is written by `completeness_middleware`, which wraps every request as the outermost layer. Because Starlette's `BaseHTTPMiddleware` runs `call_next` in a separate anyio task, Python `ContextVar` mutations made inside the handler are not visible in the middleware's `finally` block. Disposition, provider, model ID, and execution ID are therefore propagated via `request.state` (which crosses task boundaries) in addition to `ContextVar` — the middleware reads `request.state` first with a `ContextVar` fallback. This invariant holds in both Walacor backend mode and SQLite WAL mode.

---

## Roadmap

The following capabilities are planned for V2. None of these change the core guarantees — they extend them.

### External Merkle anchoring
Periodically publish the WAL root hash to an RFC 3161 timestamp authority or a public distributed ledger. This makes the audit chain independently verifiable by auditors and regulators without access to Walacor infrastructure. The on-gateway chain structure (G5) is already designed to support this; the anchor publication step is the remaining piece.

### MCP and agentic workflow support
The Model Context Protocol (MCP) introduces a new threat surface: tool calls, multi-agent chains, and automated orchestrators generate LLM traffic that bypasses human review entirely. V2 will add:
- MCP-aware adapter that parses tool call metadata alongside prompt/response content
- Per-tool attestation — tool endpoints attested alongside model endpoints
- Agentic session tracking — chain integrity across multi-agent turns, not just single-session turns
- Configurable policy rules scoped to agent identity (`agent_id` in request metadata)

### Pre-attestation webhook
Before forwarding a request, call a configurable webhook that receives the prompt text, model ID, and policy context. The webhook returns allow/deny. Enables integration with external decisioning systems (DLP, CASB, custom classifiers) without building them into the gateway binary.

### Trusted Execution Environment (TEE) deployment mode
Run the gateway inside an AWS Nitro Enclave or Azure Confidential Container, with remote attestation of the gateway process itself. Combined with G1 model attestation, this closes the remaining trust gap: the gateway's integrity is verifiable by the control plane, not just assumed.

### Per-user token budgets and quota API
Extend the current per-tenant budget tracker to per-user granularity with a management API for dynamic quota updates (no restart required). Enables product-level metering and per-seat cost governance.

---

## Development

```bash
# Install
pip install -e ./walacor-core
pip install -e "./Gateway[dev]"

# Run tests
pytest

# Set up credentials (copy template, fill in values — never committed)
cp .env.gateway.example .env.gateway

# Run locally with hot reload — Walacor backend storage + skip governance
# (fastest dev loop: no control plane needed, records go straight to Walacor)
source .env.gateway   # or: set --env-file .env.gateway in uvicorn
WALACOR_SKIP_GOVERNANCE=true \
WALACOR_PROVIDER_OPENAI_KEY=sk-... \
uvicorn gateway.main:app --reload --port 8000

# Run with full governance + Walacor storage (mock control plane)
python mock_control_plane.py &   # serves /v1/attestation-proofs, /v1/policies, /v1/gateway/executions
source .env.gateway
WALACOR_CONTROL_PLANE_URL=http://127.0.0.1:9000 \
WALACOR_GATEWAY_TENANT_ID=dev-tenant \
WALACOR_PROVIDER_OPENAI_KEY=sk-... \
uvicorn gateway.main:app --port 8000

# Run against local Ollama (full governance, SQLite WAL fallback — no Walacor creds)
python mock_control_plane.py &
WALACOR_CONTROL_PLANE_URL=http://127.0.0.1:9000 \
WALACOR_GATEWAY_TENANT_ID=dev-tenant \
WALACOR_GATEWAY_PROVIDER=ollama \
WALACOR_PROVIDER_OLLAMA_URL=http://localhost:11434 \
WALACOR_WAL_PATH=/tmp/walacor_wal \
uvicorn gateway.main:app --port 8000
```

Requirements: Python 3.12+, `walacor-core` package.

---

## License

Apache 2.0
