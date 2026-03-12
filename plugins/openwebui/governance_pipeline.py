"""
Walacor Gateway Governance Filter for OpenWebUI.

Install: Upload this file as a Filter Function in OpenWebUI Admin > Functions.
Requires: Gateway running at GATEWAY_URL with API key.

This filter enriches every request with governance context and surfaces
Gateway audit results back to users — chain position, policy verdicts,
content analysis, token budget, and model attestation status.

The inlet hook:
  - Skips system tasks (title/tags/follow-ups) to reduce noise
  - Adds X-Walacor-Request-Type header so Gateway can classify requests
  - Checks for operational alerts from Gateway status endpoint

The outlet hook:
  - Reads governance headers from Gateway's response
  - Appends a compact governance footer to assistant messages
  - Only runs on user_response tasks (not title/tag generation)
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any

from pydantic import BaseModel, Field


class Pipeline:
    """OpenWebUI Filter: Walacor Governance Visibility."""

    class Valves(BaseModel):
        """Pipeline configuration (editable in OpenWebUI admin)."""
        priority: int = Field(default=0, description="Filter execution order (lower = first)")
        gateway_url: str = Field(
            default=os.environ.get("WALACOR_GATEWAY_URL", "http://gateway:8000"),
            description="Walacor Gateway internal URL",
        )
        gateway_api_key: str = Field(
            default=os.environ.get("WALACOR_GATEWAY_API_KEY", ""),
            description="Gateway API key for status endpoint",
        )
        show_footer: bool = Field(default=True, description="Show governance footer on messages")
        show_alerts: bool = Field(default=True, description="Show Gateway operational alerts")
        footer_style: str = Field(
            default="compact",
            description="Footer style: 'compact' (icons only) or 'detailed' (with execution ID)",
        )

    def __init__(self):
        self.name = "Walacor Governance"
        self.valves = self.Valves()
        self._status_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0}

    async def inlet(
        self,
        body: dict,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
        __task__: str | None = None,
    ) -> dict:
        """Pre-request hook: classify request type and check for alerts.

        OpenWebUI injects __task__ which tells us whether this is a real user
        message or an internal task (title_generation, tags_generation, etc.).
        We pass this info to Gateway via a header so it can tag the audit record.
        """
        metadata = __metadata__ or {}

        # Tag the request type so Gateway knows what it is.
        # OpenWebUI sets __task__ = None for user messages, and specific strings
        # like "title_generation", "tags_generation" for internal tasks.
        request_type = "user_message"
        if __task__ and __task__ != "user_response":
            request_type = "system_task"

        # Inject metadata into the body so Gateway's adapter can read it.
        # OpenWebUI forwards these as part of the request context.
        if "metadata" not in body:
            body["metadata"] = {}
        body["metadata"]["request_type"] = request_type
        body["metadata"]["task"] = __task__ or "user_response"

        # Include chat and message IDs for precise audit correlation
        if metadata.get("chat_id"):
            body["metadata"]["chat_id"] = metadata["chat_id"]
        if metadata.get("message_id"):
            body["metadata"]["message_id"] = metadata["message_id"]

        # Inject user context into metadata for richer audit trail
        if __user__:
            body["metadata"]["openwebui_user_id"] = __user__.get("id", "")
            body["metadata"]["openwebui_user_role"] = __user__.get("role", "")

        # Check for operational alerts (only for real user messages)
        if request_type == "user_message" and self.valves.show_alerts:
            status = self._get_cached_status()
            if status and status.get("banners"):
                alert_text = " | ".join(
                    f"{'⚠️' if b['type'] == 'warning' else '🔴' if b['type'] == 'error' else 'ℹ️'} {b['text']}"
                    for b in status["banners"]
                )
                messages = body.get("messages", [])
                if messages and messages[0].get("role") != "system":
                    messages.insert(0, {
                        "role": "system",
                        "content": f"[Gateway Alert] {alert_text}",
                    })
                    body["messages"] = messages

        return body

    async def outlet(
        self,
        body: dict,
        __user__: dict | None = None,
        __task__: str | None = None,
    ) -> dict:
        """Post-response hook: append governance footer to assistant message.

        Only runs on user_response tasks — title/tag generation gets no footer.
        """
        if not self.valves.show_footer:
            return body

        # Skip footer for internal tasks (title, tags, follow-ups)
        if __task__ and __task__ != "user_response":
            return body

        messages = body.get("messages", [])
        if not messages:
            return body

        last_msg = messages[-1]
        if last_msg.get("role") != "assistant":
            return body

        # Read governance metadata from response info (headers stored by OpenWebUI)
        info = last_msg.get("info", {})
        headers = info.get("headers", {})

        execution_id = headers.get("x-walacor-execution-id", "")
        attestation_id = headers.get("x-walacor-attestation-id", "")
        chain_seq = headers.get("x-walacor-chain-seq", "")
        policy_result = headers.get("x-walacor-policy-result", "")
        content_analysis = headers.get("x-walacor-content-analysis", "")
        budget_remaining = headers.get("x-walacor-budget-remaining", "")
        budget_percent = headers.get("x-walacor-budget-percent", "")
        model_id = headers.get("x-walacor-model-id", "")

        if not execution_id and not chain_seq:
            return body  # No governance data available

        # Build footer based on style
        if self.valves.footer_style == "compact":
            footer = self._build_compact_footer(
                chain_seq, policy_result, content_analysis,
                budget_remaining, budget_percent,
            )
        else:
            footer = self._build_detailed_footer(
                execution_id, attestation_id, chain_seq, policy_result,
                content_analysis, budget_remaining, budget_percent, model_id,
            )

        if footer:
            last_msg["content"] = last_msg.get("content", "") + footer

        return body

    def _build_compact_footer(
        self, chain_seq, policy_result, content_analysis,
        budget_remaining, budget_percent,
    ) -> str:
        """Minimal one-line governance indicator."""
        parts = []
        if chain_seq:
            parts.append(f"🔒#{chain_seq}")
        if policy_result:
            parts.append("✅" if policy_result == "pass" else "❌")
        if content_analysis and content_analysis != "clean":
            parts.append(f"⚠️{content_analysis}")
        if budget_percent:
            parts.append(f"💰{budget_percent}%")

        if not parts:
            return ""
        return f"\n\n`{' '.join(parts)} — Walacor Governed`"

    def _build_detailed_footer(
        self, execution_id, attestation_id, chain_seq, policy_result,
        content_analysis, budget_remaining, budget_percent, model_id,
    ) -> str:
        """Full governance footer with all metadata."""
        parts = []
        if chain_seq:
            parts.append(f"🔒 Chain #{chain_seq}")
        if policy_result:
            icon = "✅" if policy_result == "pass" else "❌"
            parts.append(f"{icon} Policy: {policy_result}")
        if content_analysis:
            icon = "🛡️" if content_analysis == "clean" else "⚠️"
            parts.append(f"{icon} {content_analysis.replace('_', ' ').title()}")
        if budget_percent:
            try:
                remaining_str = f"{int(budget_remaining):,}" if budget_remaining else "?"
            except (ValueError, TypeError):
                remaining_str = "?"
            parts.append(f"💰 {remaining_str} tokens remaining ({budget_percent}% used)")

        footer_line1 = "  ".join(parts)
        footer_parts = [f"\n\n---\n**Walacor Governance** {footer_line1}"]

        details = []
        if execution_id:
            details.append(f"Execution: `{execution_id[:12]}...`")
        if model_id:
            att_label = "attested" if attestation_id and "self-attested" not in attestation_id else "self-attested"
            details.append(f"Model: {model_id} ({att_label})")
        if details:
            footer_parts.append(" | ".join(details))

        return "\n".join(footer_parts)

    def _get_cached_status(self) -> dict | None:
        """Fetch /v1/openwebui/status with caching."""
        now = time.time()
        if self._status_cache["data"] is not None and now - self._status_cache["fetched_at"] < 60:
            return self._status_cache["data"]
        try:
            url = f"{self.valves.gateway_url.rstrip('/')}/v1/openwebui/status"
            req = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "X-API-Key": self.valves.gateway_api_key,
            })
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                self._status_cache["data"] = data
                self._status_cache["fetched_at"] = now
                return data
        except Exception:
            return self._status_cache.get("data")
