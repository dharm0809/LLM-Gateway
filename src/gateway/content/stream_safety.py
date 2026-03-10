"""Mid-stream S4 (child safety) abort via compiled regex.

This module provides a fast, sub-millisecond check for S4 child-safety
patterns in accumulated streaming text.  It is intentionally keyword-based
(no ML model invocation) to keep latency negligible during SSE streaming.
"""

from __future__ import annotations

import re

# Curated S4 child-safety patterns.  These target explicit child exploitation
# terminology while avoiding common programming / parenting vocabulary.
_S4_PATTERNS = re.compile(
    r"|".join([
        r"\bchild\s+exploitation\s+material\b",
        r"\bcsam\b",
        r"\bchild\s+sexual\s+abuse\b",
        r"\bminor\s+exploitation\b",
        r"\bchild\s+pornograph\w*\b",
        r"\bpedophil\w*\s+content\b",
        r"\bunderage\s+sexual\b",
    ]),
    re.IGNORECASE,
)


def check_stream_safety(text: str) -> bool:
    """Return True if accumulated text triggers an S4 safety abort.

    Fast compiled-regex check — no ML model invocation.
    """
    return bool(_S4_PATTERNS.search(text))
