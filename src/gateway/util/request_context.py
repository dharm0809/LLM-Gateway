"""Request-scoped context variables for completeness invariant and request ID."""

from __future__ import annotations

import contextvars
import uuid

# Unique ID for each request; set by completeness middleware.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

# Final disposition for completeness: allowed | denied_* | error_*
# Set by api_key_middleware (denied_auth) or orchestrator (all other cases).
disposition_var: contextvars.ContextVar[str] = contextvars.ContextVar("disposition", default="error_gateway")

# Execution ID when a request is allowed and a WAL record is written.
execution_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("execution_id", default=None)

# Provider and model_id when known (set by orchestrator after adapter/call resolved).
provider_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("provider", default=None)
model_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("model_id", default=None)


def new_request_id() -> str:
    """Generate a new request ID and set it in context. Returns the ID."""
    rid = str(uuid.uuid4())
    request_id_var.set(rid)
    disposition_var.set("error_gateway")
    execution_id_var.set(None)
    provider_var.set(None)
    model_id_var.set(None)
    return rid
