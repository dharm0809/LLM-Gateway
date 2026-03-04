# Walacor Gateway — 5-minute quickstart

## 1. Install

From the repo root:

```bash
pip install -e ./Gateway
```

## 2. Run as transparent proxy (no governance)

Skip attestation/policy/WAL for a quick test:

```bash
export WALACOR_SKIP_GOVERNANCE=true
export WALACOR_PROVIDER_OPENAI_KEY=sk-your-key   # optional, for forwarding to OpenAI

uvicorn gateway.main:app --host 0.0.0.0 --port 8002
```

Send a request:

```bash
curl -X POST http://localhost:8002/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4","messages":[{"role":"user","content":"Hi"}]}'
```

## 3. Run with full governance (embedded control plane)

No external control plane needed — the gateway manages attestations and policies locally:

```bash
export WALACOR_GATEWAY_TENANT_ID=dev-tenant
export WALACOR_GATEWAY_API_KEYS=my-secret-key
export WALACOR_PROVIDER_OLLAMA_URL=http://localhost:11434
export WALACOR_GATEWAY_PROVIDER=ollama

uvicorn gateway.main:app --host 0.0.0.0 --port 8002
```

Models are auto-attested on first use. Manage attestations, policies, and budgets via the Control tab in the lineage dashboard or the `/v1/control/*` API.

## 4. Run with remote control plane (fleet mode)

For multi-gateway fleets, point secondary gateways at a primary:

```bash
export WALACOR_GATEWAY_TENANT_ID=dev-tenant
export WALACOR_CONTROL_PLANE_URL=http://primary-gateway:8000
export WALACOR_CONTROL_PLANE_API_KEY=shared-key

uvicorn gateway.main:app --host 0.0.0.0 --port 8002
```

## 5. Health, metrics, and dashboard

- `GET http://localhost:8002/health` — JSON health (cache, WAL, chain status)
- `GET http://localhost:8002/metrics` — Prometheus metrics
- `http://localhost:8002/lineage/` — Lineage dashboard (sessions, chains, tool events, control plane)

## 6. Docker

From repo root:

```bash
docker compose -f deploy/docker-compose.yml up --build
```

With Ollama demo (pulls model, sends test request):

```bash
docker compose -f deploy/docker-compose.yml --profile demo up --build
```

Gateway listens on port 8002.
