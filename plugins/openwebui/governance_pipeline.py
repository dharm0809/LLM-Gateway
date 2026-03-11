"""
Walacor Gateway Governance Pipeline for OpenWebUI.

Install: Copy this file into your OpenWebUI Pipelines server.
Requires: Gateway running at GATEWAY_URL with API key.

This outlet filter appends governance metadata (chain position, policy result,
content analysis verdict, budget status) to each assistant message.
The inlet filter polls /v1/openwebui/status for operational alerts.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any

# ── Configuration ──────────────────────────────────────────────
GATEWAY_URL = os.environ.get("WALACOR_GATEWAY_URL", "http://gateway:8000")
GATEWAY_API_KEY = os.environ.get("WALACOR_GATEWAY_API_KEY", "")
STATUS_POLL_INTERVAL = 60  # seconds between status polls
STATUS_CACHE: dict[str, Any] = {"data": None, "fetched_at": 0}


class Pipeline:
    """OpenWebUI Pipeline: Walacor Governance Visibility."""

    class Valves:
        """Pipeline configuration (editable in OpenWebUI admin)."""
        gateway_url: str = GATEWAY_URL
        gateway_api_key: str = GATEWAY_API_KEY
        show_footer: bool = True
        show_alerts: bool = True

    def __init__(self):
        self.name = "Walacor Governance"
        self.valves = self.Valves()

    async def inlet(self, body: dict, __user__: dict | None = None) -> dict:
        """Pre-request hook: check for operational alerts."""
        if not self.valves.show_alerts:
            return body

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

    async def outlet(self, body: dict, __user__: dict | None = None) -> dict:
        """Post-response hook: append governance footer to assistant message."""
        if not self.valves.show_footer:
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

        # Build footer
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
            remaining_str = f"{int(budget_remaining):,}" if budget_remaining else "?"
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

        footer = "\n".join(footer_parts)
        last_msg["content"] = last_msg.get("content", "") + footer

        return body

    def _get_cached_status(self) -> dict | None:
        """Fetch /v1/openwebui/status with caching."""
        now = time.time()
        if STATUS_CACHE["data"] is not None and now - STATUS_CACHE["fetched_at"] < STATUS_POLL_INTERVAL:
            return STATUS_CACHE["data"]
        try:
            url = f"{self.valves.gateway_url.rstrip('/')}/v1/openwebui/status"
            req = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "X-API-Key": self.valves.gateway_api_key,
            })
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                STATUS_CACHE["data"] = data
                STATUS_CACHE["fetched_at"] = now
                return data
        except Exception:
            return STATUS_CACHE.get("data")
