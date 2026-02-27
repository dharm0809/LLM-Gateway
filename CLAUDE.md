## Project
Walacor Gateway — ASGI audit/governance proxy for LLM providers. Source: `src/gateway/`.

## Key Architectural Facts
- Gateway does NOT compute SHA3-512 hashes of prompt/response text — it sends full text; Walacor backend hashes on ingest
- Session chain `record_hash` IS computed by the gateway (metadata fields only: execution_id, policy_version, policy_result, previous_record_hash, sequence_number, timestamp)
- Tool input/output hashes ARE computed by the gateway (orchestrator.py, for MCP/tool interactions)
- Model routing reads `model` field from request body; routes by fnmatch before path-based routing
- One port (8000) serves all providers; audit records are differentiated by model/provider/attestation_id
- `WALACOR_SKIP_GOVERNANCE=true` = audit-only mode; shared HTTP client is initialized in both modes

## Docs
- `docs/WIKI-EXECUTIVE.md` — CEO/leadership-facing; narrative style, no crypto formulas, explains decisions and tradeoffs
- `README.md` — full engineer reference; config, architecture, guarantees
- `docs/FLOW-AND-SOUNDNESS.md` — pipeline flowcharts + soundness analysis (all 9 findings resolved)
- `OVERVIEW.md` — one-page summary

## Doc Conventions
- WIKI-EXECUTIVE.md: plain English only — no SHA3-512 formulas, no code snippets, no `record_hash = ...` blocks
- Session chain formula belongs in README/FLOW docs, not the wiki
- "We compute hashes" only applies to: session chain record_hash (G5) and tool input/output hashes

## Testing
- `pytest-asyncio` not installed; async tests use `@pytest.mark.anyio` with `anyio_backend` fixture
- `get_settings()` uses `lru_cache(maxsize=1)` — call `get_settings.cache_clear()` in test teardown when monkeypatching env
