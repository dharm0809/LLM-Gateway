"""Request pipeline: resolve -> policy -> forward -> hash -> record."""

from gateway.pipeline.orchestrator import handle_request

__all__ = ["handle_request"]
