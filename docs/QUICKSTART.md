# Walacor Gateway — 5-minute quickstart

## 1. Install

From the repo root:

```bash
pip install -e ./walacor-core
pip install -e ./Gateway
```

## 2. Run as transparent proxy (no governance)

Skip attestation/policy/WAL for a quick test:

```bash
export WALACOR_GATEWAY_TENANT_ID=dev-tenant
export WALACOR_CONTROL_PLANE_URL=http://localhost:8000
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

## 3. Run with governance (control plane required)

Start the control plane (MCP API) first, then:

```bash
export WALACOR_GATEWAY_TENANT_ID=dev-tenant
export WALACOR_CONTROL_PLANE_URL=http://localhost:8000
export WALACOR_PROVIDER_OPENAI_KEY=sk-your-key
# WALACOR_GATEWAY_SKIP_GOVERNANCE is false by default

uvicorn gateway.main:app --host 0.0.0.0 --port 8002
```

The gateway will sync attestations and policies from the control plane at startup. Only attested models and policy-compliant prompts are forwarded.

## 4. Health and metrics

- `GET http://localhost:8002/health` — JSON health (cache, WAL status when governance on)
- `GET http://localhost:8002/metrics` — Prometheus metrics

## 5. Docker

From repo root:

```bash
export WALACOR_CONTROL_PLANE_URL=http://host.docker.internal:8000
docker-compose -f Gateway/deploy/docker-compose.yml up --build
```

Gateway listens on port 8002.
