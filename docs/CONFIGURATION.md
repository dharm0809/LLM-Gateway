# Gateway configuration

All configuration is via environment variables (prefix `WALACOR_`). Required variables must be set or the gateway refuses to start.

## Required (when governance is on)

When `WALACOR_SKIP_GOVERNANCE` is false (default), the following are required:

| Variable | Description |
|----------|-------------|
| `WALACOR_GATEWAY_TENANT_ID` | Tenant this gateway serves (single-tenant V1) |
| `WALACOR_CONTROL_PLANE_URL` | Base URL of the control plane (e.g. `http://localhost:8000`) |

When `WALACOR_SKIP_GOVERNANCE=true`, both can be omitted (transparent proxy only).

## Optional — identity and auth

| Variable | Default | Description |
|----------|---------|-------------|
| `WALACOR_GATEWAY_ID` | auto (gw-&lt;uuid&gt;) | Unique gateway instance ID |
| `WALACOR_GATEWAY_API_KEYS` | (empty) | Comma-separated API keys for caller auth (production) |
| `WALACOR_CONTROL_PLANE_API_KEY` | (empty) | API key for gateway→control plane; sent as X-API-Key and Authorization: Bearer on sync and execution delivery. Required when control plane has WALACOR_API_KEYS set. |
| `WALACOR_SKIP_GOVERNANCE` | false | If true, run as transparent proxy only (no attestation/policy/WAL); tenant and control plane URL not required |

## Cache and sync

| Variable | Default | Description |
|----------|---------|-------------|
| `WALACOR_ATTESTATION_CACHE_TTL` | 300 | Attestation cache TTL (seconds) |
| `WALACOR_POLICY_STALENESS_THRESHOLD` | 900 | Max policy staleness before fail-closed (seconds) |
| `WALACOR_SYNC_INTERVAL` | 60 | Pull sync interval (seconds) — future use |
| `WALACOR_GATEWAY_PROVIDER` | openai | Provider name for attestation sync (openai, anthropic, etc.) |

## WAL

| Variable | Default | Description |
|----------|---------|-------------|
| `WALACOR_WAL_PATH` | /var/walacor/wal | WAL storage directory |
| `WALACOR_WAL_MAX_SIZE_GB` | 10 | Max WAL disk usage (GB); when exceeded (enforced mode), gateway returns 503 and stops accepting new requests until WAL drains |
| `WALACOR_WAL_MAX_AGE_HOURS` | 72 | Max WAL record age (hours) before action |
| `WALACOR_WAL_HIGH_WATER_MARK` | 10000 | Max undelivered records; when exceeded (enforced mode), gateway returns 503 until backlog drains |

## Mode

| Variable | Default | Description |
|----------|---------|-------------|
| `WALACOR_ENFORCEMENT_MODE` | enforced | `enforced` or `audit_only` |

## Providers

| Variable | Default | Description |
|----------|---------|-------------|
| `WALACOR_PROVIDER_OPENAI_URL` | https://api.openai.com | OpenAI API base URL |
| `WALACOR_PROVIDER_OPENAI_KEY` | (empty) | API key for OpenAI |
| `WALACOR_PROVIDER_ANTHROPIC_URL` | https://api.anthropic.com | Anthropic API base URL |
| `WALACOR_PROVIDER_ANTHROPIC_KEY` | (empty) | API key for Anthropic |
| `WALACOR_PROVIDER_HUGGINGFACE_URL` | (empty) | HuggingFace Inference Endpoints URL |
| `WALACOR_PROVIDER_HUGGINGFACE_KEY` | (empty) | HuggingFace API key |
| `WALACOR_GENERIC_UPSTREAM_URL` | (empty) | Generic adapter upstream URL |
| `WALACOR_GENERIC_MODEL_PATH` | $.model | JSON path for model ID |
| `WALACOR_GENERIC_PROMPT_PATH` | $.messages[*].content | JSON path for prompt |
| `WALACOR_GENERIC_RESPONSE_PATH` | $.choices[0].message.content | JSON path for response |

## Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `WALACOR_METRICS_ENABLED` | true | Enable `/metrics` endpoint |
| `WALACOR_LOG_LEVEL` | INFO | Logging level |

## .env files

The gateway loads `.env` or `.env.gateway` from the current working directory if present. Values in the environment override file values.
