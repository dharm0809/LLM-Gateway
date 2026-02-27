"""Provider adapter abstract base and shared types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
import httpx


@dataclass(frozen=True)
class ToolInteraction:
    """One tool call and its result, captured by either passive or active strategy.

    Passive strategy (cloud providers): populated by parsing the provider response.
    Active strategy (local models): populated as the gateway executes each MCP tool call.
    """

    tool_id: str                          # provider-assigned call ID (e.g. "call_abc123")
    tool_type: str                        # "function" | "web_search" | "code_interpreter" | "file_search" | "server_tool"
    tool_name: str | None                 # function/tool name (None for built-in provider tools)
    input_data: dict | str | None         # arguments / search query / code sent to tool
    output_data: dict | str | None        # result returned from tool (None for passive — provider executes internally)
    sources: list[dict] | None            # cited URLs for web_search results
    metadata: dict[str, Any] | None       # extra: iteration, duration_ms, is_error, etc.


@dataclass(frozen=True)
class ModelCall:
    """Normalized representation of an LLM request."""

    provider: str
    model_id: str
    prompt_text: str
    raw_body: bytes
    is_streaming: bool
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ModelResponse:
    """Normalized representation of an LLM response."""

    content: str
    usage: dict[str, Any] | None
    raw_body: bytes
    provider_request_id: str | None = None
    model_hash: str | None = None
    # Phase 14: tool-aware fields
    tool_interactions: list[ToolInteraction] | None = None  # tool calls captured from this response
    has_pending_tool_calls: bool = False                    # active strategy: LLM is requesting tool execution


class ProviderAdapter(ABC):
    """Abstract base for LLM provider adapters."""

    @abstractmethod
    async def parse_request(self, request: Request) -> ModelCall:
        """Extract model_id, prompt, params from raw HTTP request."""

    @abstractmethod
    async def build_forward_request(self, call: ModelCall, original: Request) -> httpx.Request:
        """Build the upstream request to the real provider."""

    @abstractmethod
    def parse_response(self, response: httpx.Response) -> ModelResponse:
        """Extract content, usage from provider response (non-streaming)."""

    @abstractmethod
    def parse_streamed_response(self, chunks: list[bytes]) -> ModelResponse:
        """Assemble full response from buffered stream chunks."""

    @abstractmethod
    def supports_streaming(self) -> bool:
        """Whether this adapter supports streaming responses."""

    @abstractmethod
    def get_provider_name(self) -> str:
        """Provider identifier (openai, anthropic, etc.)."""

    def build_tool_result_call(
        self,
        original_call: ModelCall,
        tool_calls: list[ToolInteraction],
        tool_results: list[dict],
    ) -> ModelCall:
        """Build a new ModelCall with tool results appended to messages (active strategy).

        Adapters that support the active tool-call loop must override this.
        The returned ModelCall carries the updated raw_body with tool call + result
        messages appended. The original prompt_text is preserved for hashing.

        Args:
            original_call: The current ModelCall (may already include prior tool rounds).
            tool_calls: The ToolInteraction list the LLM requested in its last response.
            tool_results: List of {"tool_call_id": str, "content": str} results to inject.

        Raises:
            NotImplementedError: If this adapter does not support active tool execution.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support active tool execution. "
            "Override build_tool_result_call to enable the active strategy."
        )
