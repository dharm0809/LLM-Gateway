"""Phase 10: Post-inference response policy evaluation with pluggable content analyzers."""

from __future__ import annotations

import asyncio
import logging

from starlette.responses import JSONResponse

from gateway.adapters.base import ModelResponse
from gateway.cache.policy_cache import PolicyCache
from gateway.content.base import ContentAnalyzer, Decision, Verdict

logger = logging.getLogger(__name__)


async def _run_analyzer(analyzer: ContentAnalyzer, text: str) -> Decision | None:
    """Run a single analyzer under its declared timeout. Returns None on timeout."""
    try:
        return await asyncio.wait_for(
            analyzer.analyze(text),
            timeout=analyzer.timeout_ms / 1000.0,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Content analyzer %s timed out after %dms — skipping",
            analyzer.analyzer_id,
            analyzer.timeout_ms,
        )
        return None
    except Exception as e:
        logger.warning("Content analyzer %s raised: %s", analyzer.analyzer_id, e)
        return None


async def analyze_text(text: str, analyzers: list[ContentAnalyzer]) -> list[dict]:
    """Run all analyzers on arbitrary text (tool outputs, injected content, etc.).

    Returns a list of decision dicts — same shape as analyzer_decisions in evaluate_post_inference.
    Never raises; timeouts and errors are skipped silently (same contract as _run_analyzer).
    """
    if not analyzers or not text:
        return []
    results = await asyncio.gather(*[_run_analyzer(a, text) for a in analyzers])
    return [
        {
            "analyzer_id": d.analyzer_id,
            "verdict": d.verdict.value,
            "confidence": d.confidence,
            "category": d.category,
            "reason": d.reason,
        }
        for d in results if d is not None
    ]


async def evaluate_post_inference(
    policy_cache: PolicyCache,
    model_response: ModelResponse,
    analyzers: list[ContentAnalyzer],
) -> tuple[bool, int, str, list[dict], JSONResponse | None]:
    """
    Run all content analyzers on model_response.content.

    Returns:
        (blocked, response_policy_version, response_policy_result, analyzer_decisions, error_or_none)

    analyzer_decisions: list of {"analyzer_id", "verdict", "confidence", "category", "reason"}
        — labels only, no content.
    response_policy_result: "pass" | "blocked" | "flagged" | "skipped"
    """
    if not analyzers or not model_response.content:
        return False, policy_cache.version, "skipped", [], None

    # Run all analyzers concurrently, each under its own timeout
    results: list[Decision | None] = await asyncio.gather(
        *[_run_analyzer(a, model_response.content) for a in analyzers]
    )

    decisions = [r for r in results if r is not None]
    analyzer_decisions = [
        {
            "analyzer_id": d.analyzer_id,
            "verdict": d.verdict.value,
            "confidence": d.confidence,
            "category": d.category,
            "reason": d.reason,
        }
        for d in decisions
    ]

    # Determine overall result
    blocks = [d for d in decisions if d.verdict == Verdict.BLOCK]
    warns = [d for d in decisions if d.verdict == Verdict.WARN]

    if blocks:
        # First blocking decision drives the error response
        top = blocks[0]
        logger.warning(
            "Response blocked by analyzer %s: category=%s reason=%s confidence=%.2f",
            top.analyzer_id, top.category, top.reason, top.confidence,
        )
        err = JSONResponse(
            {
                "error": "Response blocked by content policy",
                "category": top.category,
                "analyzer_id": top.analyzer_id,
            },
            status_code=403,
        )
        return True, policy_cache.version, "blocked", analyzer_decisions, err

    if warns:
        return False, policy_cache.version, "flagged", analyzer_decisions, None

    return False, policy_cache.version, "pass", analyzer_decisions, None
