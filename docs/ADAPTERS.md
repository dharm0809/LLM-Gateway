# Writing and configuring adapters

## Built-in adapters

- **OpenAI** — `/v1/chat/completions`, `/v1/completions` (streaming and non-streaming)
- **Anthropic** — `/v1/messages` (x-api-key auth)
- **HuggingFace** — `/generate` when `WALACOR_PROVIDER_HUGGINGFACE_URL` is set
- **Generic** — `/v1/custom` when `WALACOR_GENERIC_UPSTREAM_URL` is set; JSON paths configurable via env

## ProviderAdapter interface

Implement:

- `parse_request(request) -> ModelCall` — extract model_id, prompt_text, raw_body, is_streaming, metadata
- `build_forward_request(call, original_request) -> httpx.Request` — build upstream request
- `parse_response(response) -> ModelResponse` — parse non-streaming response (content, usage, raw_body)
- `parse_streamed_response(chunks: list[bytes]) -> ModelResponse` — assemble full response from buffered stream chunks
- `supports_streaming() -> bool`
- `get_provider_name() -> str`

Register the adapter in `gateway.pipeline.orchestrator._resolve_adapter()` by path and add the route in `gateway.main.create_app()`.

## Generic adapter

For custom or on-prem inference servers, set:

- `WALACOR_GENERIC_UPSTREAM_URL` — base URL of your API
- `WALACOR_GENERIC_MODEL_PATH` — e.g. `$.model`
- `WALACOR_GENERIC_PROMPT_PATH` — e.g. `$.messages[*].content` or `$.input`
- `WALACOR_GENERIC_RESPONSE_PATH` — e.g. `$.choices[0].message.content` or `$.output`

Send requests to `POST /v1/custom` (and optionally `/v1/custom/...`). The gateway forwards to the upstream URL and uses the JSON paths to extract model, prompt, and response for hashing and policy.

## Attestation cache key

The attestation cache is keyed by `(provider, model_id)`. Use the same `get_provider_name()` so that sync and resolution match.
