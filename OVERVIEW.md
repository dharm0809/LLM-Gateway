# Walacor Gateway — Overview

## What it is

A security and audit proxy that sits between your application and any AI model. Your app talks to the gateway exactly like it talks to OpenAI. The gateway handles attestation, policy enforcement, and audit logging — then forwards the request to the actual model.

```
Your App  →  Walacor Gateway  →  LLM (OpenAI / Anthropic / Ollama / LM Studio)
```

No code changes required in your application.

---

## What it records

Every request produces one audit record containing:

- The **prompt** and **response** (full text, sent to Walacor backend which hashes on ingest)
- The **provider's own request ID** — the ID the model assigned to that specific exchange
- The **model hash** — a cryptographic fingerprint of the model weights (available for local models like Ollama)
- Policy result, timestamp, tenant, session chain values

Records are written to a local SQLite WAL first (crash-safe), then delivered to the control plane asynchronously.

---

## What it enforces

| Check | What happens on failure |
|---|---|
| **Model attestation** | Request blocked — model must be registered with the control plane |
| **Pre-request policy** | Request blocked — policy rules evaluated before forwarding |
| **Response content** | Response blocked — PII and toxicity checks before returning to caller |
| **Token budget** | Request blocked — per-tenant spend limit enforced |
| **WAL backpressure** | Request blocked — protects against unbounded disk growth during outages |

Set `WALACOR_ENFORCEMENT_MODE=audit_only` to log violations without blocking (useful for baseline measurement before going live).

---

## Supported models

| Provider | How to connect |
|---|---|
| OpenAI | Set `WALACOR_PROVIDER_OPENAI_KEY` |
| Anthropic Claude | Set `WALACOR_PROVIDER_ANTHROPIC_KEY` |
| **Ollama** (local) | Set `WALACOR_GATEWAY_PROVIDER=ollama` and `WALACOR_PROVIDER_OLLAMA_URL=http://localhost:11434` |
| **LM Studio** (local) | Set `WALACOR_PROVIDER_OPENAI_URL=http://localhost:1234` |
| HuggingFace endpoints | Set `WALACOR_PROVIDER_HUGGINGFACE_URL` |

---

## Quick start

```bash
pip install -e ./walacor-core
pip install -e "./Gateway[dev]"

# No governance — just a transparent proxy
WALACOR_SKIP_GOVERNANCE=true \
WALACOR_PROVIDER_OPENAI_KEY=sk-... \
walacor-gateway

# Full governance
WALACOR_GATEWAY_TENANT_ID=my-tenant \
WALACOR_CONTROL_PLANE_URL=https://control.walacor.com \
WALACOR_PROVIDER_OPENAI_KEY=sk-... \
walacor-gateway
```

Gateway listens on `http://localhost:8000`. Point any OpenAI-compatible client there.

---

## Key endpoints

| Path | Description |
|---|---|
| `/v1/chat/completions` | OpenAI / Ollama / LM Studio |
| `/v1/messages` | Anthropic |
| `/health` | Status, cache freshness, WAL backlog |
| `/metrics` | Prometheus |

---

## How the audit trail works

```
Request comes in
    │
    ├─ Blocked (attestation / policy / budget)?  → one row in gateway_attempts (disposition = denied_*)
    │
    └─ Allowed?
           │
           ├─ Forward to model, get response
           ├─ Run content checks (PII, toxicity)
           ├─ Link to session chain (Merkle hash)
           ├─ Write to local WAL (SQLite, fsync)
           └─ Deliver to control plane (async, with retry)
                    ↓
           one row in gateway_attempts (disposition = allowed)
           one row in wal_records (full execution record)
```

Every request — whether allowed, blocked, or errored — always produces exactly one row in `gateway_attempts`. This is the completeness invariant.

---

## Session chains (G5)

Pass `x-walacor-session-id` in your request. The gateway links turns within a session via a hash chain:

```
turn 1  →  record_hash_1
turn 2  →  SHA3-512(turn_2_fields + record_hash_1)  →  record_hash_2
turn 3  →  SHA3-512(turn_3_fields + record_hash_2)  →  record_hash_3
```

The control plane can verify that no turn was deleted, reordered, or modified.

---

## Minimum required config

```bash
WALACOR_GATEWAY_TENANT_ID=your-tenant-id
WALACOR_CONTROL_PLANE_URL=https://your-control-plane
WALACOR_PROVIDER_OPENAI_KEY=sk-...   # or whichever provider you use
```

Everything else has safe defaults. See [README.md](README.md) for the full configuration reference.
