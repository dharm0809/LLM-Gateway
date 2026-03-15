"""Policy evaluation result model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class PolicyEvalResult(BaseModel):
    """Result of evaluating one policy."""

    policy_id: str
    policy_name: str
    result: Literal["pass", "fail"]
    details: dict | None = None
