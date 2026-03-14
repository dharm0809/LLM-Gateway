"""Image safety analysis via LlamaGuard Vision.

Sends images to LlamaGuard 3 Vision via Ollama /api/chat for S1-S14 classification.
Fail-open: returns PASS with confidence=0.0 on timeout, connection error, or model
unavailability.

Category mapping mirrors text LlamaGuard (S1-S14):
  S1  -> violent_crimes
  S2  -> nonviolent_crimes
  S3  -> sex_crimes
  S4  -> child_safety
  S5  -> defamation
  S6  -> specialized_advice
  S7  -> privacy_pii
  S8  -> intellectual_property
  S9  -> indiscriminate_weapons
  S10 -> hate_discrimination
  S11 -> self_harm
  S12 -> sexual_content
  S13 -> elections
  S14 -> code_interpreter_abuse

Unlike text LlamaGuard (which WARNs on most categories), image safety BLOCKs on
ALL unsafe categories because unsafe visual content carries higher risk.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx
from starlette.responses import JSONResponse

from gateway.content.base import Decision, Verdict

logger = logging.getLogger(__name__)

# Same category map as text LlamaGuard — see llama_guard.py
_CATEGORY_MAP: dict[str, str] = {
    "S1": "violent_crimes",
    "S2": "nonviolent_crimes",
    "S3": "sex_crimes",
    "S4": "child_safety",
    "S5": "defamation",
    "S6": "specialized_advice",
    "S7": "privacy_pii",
    "S8": "intellectual_property",
    "S9": "indiscriminate_weapons",
    "S10": "hate_discrimination",
    "S11": "self_harm",
    "S12": "sexual_content",
    "S13": "elections",
    "S14": "code_interpreter_abuse",
}


class ImageSafetyAnalyzer:
    """LlamaGuard 3 Vision image safety classifier via local Ollama.

    Sends base64-encoded images to Ollama's /api/chat endpoint with the
    LlamaGuard Vision model for S1-S14 safety classification.

    Fail-open: if Ollama is unavailable, times out, or returns an
    unparseable response, returns PASS with confidence=0.0 so the
    pipeline is never blocked by infrastructure issues.
    """

    _analyzer_id = "walacor.image_safety.v1"

    def __init__(
        self,
        ollama_url: str,
        model: str = "llama-guard3-vision:11b",
        timeout_ms: int = 10000,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._ollama_url = ollama_url.rstrip("/")
        self._model = model
        self._timeout_ms = timeout_ms
        self._http_client = http_client

    @property
    def analyzer_id(self) -> str:
        return self._analyzer_id

    @property
    def timeout_ms(self) -> int:
        return self._timeout_ms

    def _parse_response(self, response_text: str) -> Decision:
        """Parse 'safe' or 'unsafe\\nS4' from LlamaGuard Vision output.

        All unsafe categories result in BLOCK for image content (higher
        risk than text).
        """
        lines = response_text.strip().splitlines()
        first = lines[0].strip().lower() if lines else ""

        if first == "safe":
            return Decision(
                verdict=Verdict.PASS,
                confidence=1.0,
                analyzer_id=self.analyzer_id,
                category="safety",
                reason="safe",
            )

        if first == "unsafe":
            # Parse category codes from second line
            categories: list[str] = []
            if len(lines) > 1:
                codes = [c.strip().upper() for c in lines[1].split(",") if c.strip()]
                categories = [_CATEGORY_MAP[c] for c in codes if c in _CATEGORY_MAP]

            if not categories:
                categories = ["unknown"]

            category = categories[0]
            reason = ",".join(categories)

            return Decision(
                verdict=Verdict.BLOCK,
                confidence=0.95,
                analyzer_id=self.analyzer_id,
                category=category,
                reason=reason,
            )

        # Unexpected format — fail-open
        logger.warning(
            "LlamaGuard Vision unexpected response format: %r",
            response_text[:100],
        )
        return Decision(
            verdict=Verdict.PASS,
            confidence=0.0,
            analyzer_id=self.analyzer_id,
            category="safety",
            reason="parse_error",
        )

    async def analyze_image(
        self, image_bytes: bytes, mime_type: str = "image/png"
    ) -> Decision:
        """Analyze an image for safety violations via LlamaGuard Vision.

        Args:
            image_bytes: Raw image bytes.
            mime_type: MIME type of the image (e.g. "image/png", "image/jpeg").

        Returns:
            Decision with BLOCK verdict for unsafe content, PASS for safe.
            Fail-open: returns PASS with confidence=0.0 on any error.
        """
        b64_image = base64.b64encode(image_bytes).decode("ascii")

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": "Analyze this image for safety.",
                    "images": [b64_image],
                }
            ],
            "stream": False,
        }
        url = f"{self._ollama_url}/api/chat"
        timeout = self._timeout_ms / 1000.0

        try:
            if self._http_client is not None:
                resp = await self._http_client.post(url, json=payload, timeout=timeout)
            else:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(url, json=payload, timeout=timeout)

            resp.raise_for_status()
            data = resp.json()
            content = (data.get("message") or {}).get("content") or ""
            return self._parse_response(content)

        except httpx.TimeoutException:
            logger.warning(
                "LlamaGuard Vision timeout after %.1fs: model=%s",
                timeout,
                self._model,
            )
            return Decision(
                verdict=Verdict.PASS,
                confidence=0.0,
                analyzer_id=self.analyzer_id,
                category="safety",
                reason="timeout",
            )
        except Exception as exc:
            logger.warning("LlamaGuard Vision unavailable (fail-open): %s", exc)
            return Decision(
                verdict=Verdict.PASS,
                confidence=0.0,
                analyzer_id=self.analyzer_id,
                category="safety",
                reason="unavailable",
            )


async def evaluate_image_safety(
    analyzer: ImageSafetyAnalyzer,
    images: list[dict[str, Any]],
    max_images: int = 5,
) -> tuple[bool, JSONResponse | None, list[dict[str, Any]]]:
    """Run image safety on extracted images.

    Returns (is_blocked, error_response_or_None, image_analysis_results).
    """
    analysis_results: list[dict[str, Any]] = []

    if len(images) > max_images:
        logger.warning("Too many images (%d > %d), skipping image safety", len(images), max_images)
        for img in images:
            analysis_results.append({
                "image_index": img["index"],
                "hash_sha3_512": img["hash_sha3_512"],
                "safety_verdict": "skip",
                "safety_category": None,
                "safety_reason": f"exceeded_max_images_{max_images}",
            })
        return False, None, analysis_results

    for img in images:
        decision = await analyzer.analyze_image(img["raw_bytes"], img.get("mimetype", "image/png"))

        result = {
            "image_index": img["index"],
            "hash_sha3_512": img["hash_sha3_512"],
            "safety_verdict": decision.verdict.value,
            "safety_category": decision.category if decision.verdict != Verdict.PASS else None,
            "safety_reason": decision.reason,
        }
        analysis_results.append(result)

        if decision.verdict == Verdict.BLOCK:
            logger.critical(
                "IMAGE SAFETY BLOCK: category=%s hash=%.16s...",
                decision.category, img["hash_sha3_512"],
            )
            error_body = {
                "error": f"Request blocked: image content violates safety policy ({decision.category})",
                "category": decision.category,
            }
            return True, JSONResponse(error_body, status_code=403), analysis_results

    return False, None, analysis_results
