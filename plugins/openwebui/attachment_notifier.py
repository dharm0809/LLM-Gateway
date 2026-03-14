"""OpenWebUI Pipeline Plugin: Attachment Notifier.

Sends file upload metadata to the Walacor Gateway webhook endpoint
when users upload files in OpenWebUI chats.

Install: copy to OpenWebUI's pipelines directory or upload via admin UI.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import requests
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Pipeline:
    """OpenWebUI Filter pipeline that notifies the gateway about file uploads."""

    class Valves(BaseModel):
        priority: int = Field(default=0, description="Pipeline priority (lower = first)")
        gateway_url: str = Field(default="http://localhost:8000", description="Walacor Gateway base URL")
        gateway_api_key: str = Field(default="", description="Gateway API key for webhook auth")
        enabled: bool = Field(default=True, description="Enable attachment notifications")

    def __init__(self):
        self.name = "Walacor Attachment Notifier"
        self.valves = self.Valves()

    def inlet(self, body: dict, __user__: dict | None = None) -> dict:
        """Pre-request hook: detect file references and notify gateway."""
        if not self.valves.enabled:
            return body

        user_info = __user__ or {}
        user_id = user_info.get("id", "")
        user_email = user_info.get("email", "")
        chat_id = body.get("metadata", {}).get("chat_id", "")

        # Check for files in metadata
        files = body.get("metadata", {}).get("files", [])
        for f in files:
            self._notify_gateway(
                filename=f.get("filename", f.get("name", "unknown")),
                mimetype=f.get("type", "application/octet-stream"),
                size_bytes=f.get("size", 0),
                file_content=f.get("data", {}).get("content", ""),
                chat_id=chat_id,
                user_id=user_id,
                user_email=user_email,
            )

        return body

    def outlet(self, body: dict, __user__: dict | None = None) -> dict:
        """Post-response hook: no-op for this plugin."""
        return body

    def _notify_gateway(
        self,
        filename: str,
        mimetype: str,
        size_bytes: int,
        file_content: str,
        chat_id: str,
        user_id: str,
        user_email: str,
    ) -> None:
        """POST file metadata to gateway webhook."""
        # Compute hash from available content
        content_bytes = file_content.encode("utf-8") if file_content else b""
        file_hash = hashlib.sha3_512(content_bytes).hexdigest() if content_bytes else ""

        payload = {
            "filename": filename,
            "mimetype": mimetype,
            "size_bytes": size_bytes,
            "hash_sha3_512": file_hash,
            "chat_id": chat_id,
            "user_id": user_id,
            "user_email": user_email,
            "upload_timestamp": datetime.now(timezone.utc).isoformat(),
        }

        headers = {"Content-Type": "application/json"}
        if self.valves.gateway_api_key:
            headers["X-API-Key"] = self.valves.gateway_api_key

        try:
            resp = requests.post(
                f"{self.valves.gateway_url}/v1/attachments/notify",
                json=payload,
                headers=headers,
                timeout=5,
            )
            if resp.status_code == 200:
                logger.info("Attachment notified: %s (%.16s...)", filename, file_hash)
            else:
                logger.warning("Attachment notify failed: %d %s", resp.status_code, resp.text[:100])
        except Exception as e:
            logger.warning("Attachment notify error: %s", e)
