"""JSON structured logging formatter with request_id correlation."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from gateway.util.request_context import request_id_var


class JsonFormatter(logging.Formatter):
    """Format every log record as a single JSON line with request_id correlation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        rid = request_id_var.get("")
        if rid:
            payload["request_id"] = rid
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_json_logging(level: str = "INFO") -> None:
    """Replace the root handler with a JSON formatter. Call once at startup."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Remove existing handlers (e.g. basicConfig handler)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
