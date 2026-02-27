"""Ollama adapter: OpenAI-compatible chat completions + model digest via /api/show.

Phase 14 additions:
  - parse_response and parse_streamed_response now extract tool_calls via the shared
    OpenAI-compat helpers (_parse_chat_completions_choice, _build_interactions_from_map).
  - build_tool_result_call appends assistant tool_calls + tool result messages to support
    the active strategy loop for local Ollama models.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from starlette.requests import Request

from gateway.adapters.base import ModelCall, ModelResponse, ProviderAdapter, ToolInteraction
from gateway.adapters.openai import (
    _accumulate_tool_call_delta,
    _build_interactions_from_map,
    _detect_multimodal,
    _extract_inference_params,
    _extract_system_prompt,
    _parse_chat_completions_choice,
    _process_sse_line,
)

logger = logging.getLogger(__name__)

# Module-level cache: model_name -> digest string
_digest_cache: dict[str, str] = {}


async def _fetch_model_digest(base_url: str, model_name: str, client: httpx.AsyncClient | None = None) -> str | None:
    """Call Ollama /api/show to get the model digest (SHA256 of weights). Cached per model name."""
    if model_name in _digest_cache:
        return _digest_cache[model_name]
    url = f"{base_url.rstrip('/')}/api/show"
    try:
        if client is not None:
            resp = await client.post(url, json={"name": model_name}, timeout=5.0)
        else:
            async with httpx.AsyncClient() as c:
                resp = await c.post(url, json={"name": model_name}, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            # Ollama returns digest under "details.digest" or top-level "digest"
            digest = (data.get("details") or {}).get("digest") or data.get("digest")
            if digest:
                _digest_cache[model_name] = str(digest)
                return str(digest)
    except Exception as e:
        logger.warning("Failed to fetch Ollama model digest for %s: %s", model_name, e)
    return None


def _concat_messages(messages: list[dict]) -> str:
    parts = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
        else:
            parts.append(str(content) if content is not None else "")
    return "\n".join(parts)


class OllamaAdapter(ProviderAdapter):
    """Adapter for Ollama using its OpenAI-compatible /v1/chat/completions endpoint.

    Fetches the model digest from /api/show and stores it as model_hash on ModelResponse.
    Set WALACOR_PROVIDER_OLLAMA_URL to point at your Ollama instance (default: http://localhost:11434).
    """

    def __init__(self, base_url: str, api_key: str = "") -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def get_provider_name(self) -> str:
        return "ollama"

    def supports_streaming(self) -> bool:
        return True

    async def parse_request(self, request: Request) -> ModelCall:
        body_bytes = await request.body()
        try:
            data = json.loads(body_bytes.decode("utf-8"))
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON body")
        model_id = data.get("model") or ""
        messages = data.get("messages", [])
        prompt_text = _concat_messages(messages) if messages else data.get("prompt", "") or ""
        is_streaming = data.get("stream", False)
        metadata: dict[str, Any] = {}
        if request.headers.get("x-user-id"):
            metadata["user"] = request.headers["x-user-id"]
        if request.headers.get("x-session-id"):
            metadata["session_id"] = request.headers["x-session-id"]
        params = _extract_inference_params(data)
        if params:
            metadata["inference_params"] = params
        system_prompt = _extract_system_prompt(messages)
        if system_prompt:
            metadata["system_prompt"] = system_prompt
        has_mm, mm_count = _detect_multimodal(messages)
        if has_mm:
            metadata["has_multimodal_input"] = True
            metadata["multimodal_input_count"] = mm_count
        return ModelCall(
            provider=self.get_provider_name(),
            model_id=model_id,
            prompt_text=prompt_text,
            raw_body=body_bytes,
            is_streaming=is_streaming,
            metadata=metadata,
        )

    async def build_forward_request(self, call: ModelCall, original: Request) -> httpx.Request:
        # Ollama's OpenAI-compat endpoint mirrors the same path
        url = f"{self._base_url}{original.url.path}"
        if original.url.query:
            url += f"?{original.url.query}"
        skip = {"origin", "referer", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest"}
        headers = {k: v for k, v in original.headers.items() if k.lower() not in skip}
        # Strip content-length so httpx recomputes it from the actual (possibly modified) body.
        headers.pop("content-length", None)
        if self._api_key:
            headers.setdefault("Authorization", f"Bearer {self._api_key}")
        return httpx.Request(
            method=original.method,
            url=url,
            headers=headers,
            content=call.raw_body,
        )

    def parse_response(self, response: httpx.Response) -> ModelResponse:
        try:
            data = response.json()
        except Exception:
            return ModelResponse(content="", usage=None, raw_body=response.content)

        content, tool_interactions, has_pending = _parse_chat_completions_choice(data)

        return ModelResponse(
            content=content,
            usage=data.get("usage"),
            raw_body=response.content,
            provider_request_id=data.get("id"),
            tool_interactions=tool_interactions if tool_interactions else None,
            has_pending_tool_calls=has_pending,
            # model_hash is set later by the orchestrator after /api/show
        )

    def parse_streamed_response(self, chunks: list[bytes]) -> ModelResponse:
        content_parts: list[str] = []
        tool_call_map: dict[int, dict[str, Any]] = {}
        state: dict[str, Any] = {"provider_request_id": None, "has_pending_tool_calls": False}

        for chunk in chunks:
            for line in chunk.decode("utf-8", errors="replace").splitlines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    continue
                _process_sse_line(payload, content_parts, tool_call_map, state)

        tool_interactions = _build_interactions_from_map(tool_call_map)
        return ModelResponse(
            content="".join(content_parts),
            usage=None,
            raw_body=b"".join(chunks),
            provider_request_id=state["provider_request_id"],
            tool_interactions=tool_interactions if tool_interactions else None,
            has_pending_tool_calls=state["has_pending_tool_calls"],
        )

    def build_tool_result_call(
        self,
        original_call: ModelCall,
        tool_calls: list[ToolInteraction],
        tool_results: list[dict],
    ) -> ModelCall:
        """Append assistant tool_calls + tool result messages (OpenAI-compat format).

        Ollama uses the same multi-turn tool format as OpenAI Chat Completions.
        prompt_text is preserved so the audit hash covers the original user intent.
        """
        body = json.loads(original_call.raw_body)
        messages: list[dict] = body.get("messages", [])

        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc.tool_id,
                    "type": "function",
                    "function": {
                        "name": tc.tool_name or "",
                        "arguments": (
                            json.dumps(tc.input_data)
                            if isinstance(tc.input_data, dict)
                            else (tc.input_data or "{}")
                        ),
                    },
                }
                for tc in tool_calls
            ],
        })
        for result in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": result["tool_call_id"],
                "content": str(result["content"]),
            })

        body["messages"] = messages
        new_raw_body = json.dumps(body).encode("utf-8")
        return ModelCall(
            provider=original_call.provider,
            model_id=original_call.model_id,
            prompt_text=original_call.prompt_text,
            raw_body=new_raw_body,
            is_streaming=original_call.is_streaming,
            metadata=original_call.metadata,
        )

    async def fetch_model_hash(self, model_name: str, client: httpx.AsyncClient | None = None) -> str | None:
        """Fetch and cache the Ollama model digest for model_name."""
        return await _fetch_model_digest(self._base_url, model_name, client)
