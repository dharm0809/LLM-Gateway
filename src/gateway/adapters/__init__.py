"""Provider adapters: OpenAI, Anthropic, HuggingFace, Generic, Ollama."""

from gateway.adapters.base import ModelCall, ModelResponse, ProviderAdapter, ToolInteraction
from gateway.adapters.openai import OpenAIAdapter
from gateway.adapters.ollama import OllamaAdapter

__all__ = [
    "ModelCall",
    "ModelResponse",
    "ProviderAdapter",
    "ToolInteraction",
    "OpenAIAdapter",
    "OllamaAdapter",
]
