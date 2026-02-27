"""Generic adapter: configurable JSON paths for model, prompt, response. For custom/on-prem APIs."""

from __future__ import annotations

import json
from typing import Any

import httpx
from starlette.requests import Request

from gateway.adapters.base import ModelCall, ModelResponse, ProviderAdapter


def _json_path(obj: Any, path: str) -> Any:
    """Resolve simple JSON path: $.key1.key2 or $.key. Supports one [*] for array concat."""
    if path.startswith("$."):
        path = path[2:]
    parts = path.split(".")
    current = obj
    for i, p in enumerate(parts):
        if not p:
            continue
        if p == "*" and isinstance(current, list):
            rest = ".".join(parts[i + 1:])
            if rest:
                return " ".join(str(_json_path(x, rest)) for x in current)
            return " ".join(str(x) for x in current)
        if isinstance(current, list):
            return " ".join(str(_json_path(x, ".".join(parts[i:])) if parts[i:] else x) for x in current)
        current = current.get(p) if isinstance(current, dict) else None
    return current


class GenericAdapter(ProviderAdapter):
    """Configurable adapter via env: WALACOR_GENERIC_MODEL_PATH, WALACOR_GENERIC_PROMPT_PATH, etc."""

    def __init__(self, base_url: str, api_key: str = "", model_path: str = "$.model", prompt_path: str = "$.messages[*].content", response_path: str = "$.choices[0].message.content") -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model_path = model_path
        self._prompt_path = prompt_path
        self._response_path = response_path

    def get_provider_name(self) -> str:
        return "generic"

    def supports_streaming(self) -> bool:
        return True

    async def parse_request(self, request: Request) -> ModelCall:
        body_bytes = await request.body()
        try:
            data = json.loads(body_bytes.decode("utf-8"))
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON body")
        model_id = str(_json_path(data, self._model_path) or "")
        prompt_val = _json_path(data, self._prompt_path)
        prompt_text = " ".join(prompt_val) if isinstance(prompt_val, list) else str(prompt_val or "")
        is_streaming = data.get("stream", False)
        return ModelCall(
            provider=self.get_provider_name(),
            model_id=model_id,
            prompt_text=prompt_text,
            raw_body=body_bytes,
            is_streaming=is_streaming,
            metadata={},
        )

    async def build_forward_request(self, call: ModelCall, original: Request) -> httpx.Request:
        url = f"{self._base_url}{original.url.path}"
        if original.url.query:
            url += f"?{original.url.query}"
        headers = dict(original.headers)
        if self._api_key:
            headers.setdefault("Authorization", f"Bearer {self._api_key}")
        return httpx.Request(method=original.method, url=url, headers=headers, content=call.raw_body)

    def parse_response(self, response: httpx.Response) -> ModelResponse:
        try:
            data = response.json()
        except Exception:
            return ModelResponse(content="", usage=None, raw_body=response.content)
        content = str(_json_path(data, self._response_path) or "")
        return ModelResponse(
            content=content,
            usage=data.get("usage"),
            raw_body=response.content,
            provider_request_id=data.get("id"),
        )

    def parse_streamed_response(self, chunks: list[bytes]) -> ModelResponse:
        content_parts = []
        provider_request_id = None
        for chunk in chunks:
            for line in chunk.decode("utf-8", errors="replace").splitlines():
                if line.startswith("data: "):
                    try:
                        obj = json.loads(line[6:].strip())
                        if provider_request_id is None:
                            provider_request_id = obj.get("id")
                        part = _json_path(obj, self._response_path)
                        if part:
                            content_parts.append(str(part))
                    except json.JSONDecodeError:
                        pass
        return ModelResponse(
            content="".join(content_parts),
            usage=None,
            raw_body=b"".join(chunks),
            provider_request_id=provider_request_id,
        )
