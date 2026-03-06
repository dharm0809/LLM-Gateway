"""Caller identity resolution from request headers and JWT claims."""

from __future__ import annotations

import dataclasses

from starlette.requests import Request


@dataclasses.dataclass(frozen=True)
class CallerIdentity:
    """Immutable caller identity resolved from JWT claims or request headers."""

    user_id: str
    email: str = ""
    roles: list[str] = dataclasses.field(default_factory=list)
    team: str | None = None
    source: str = "header_unverified"  # "jwt" (trusted) or "header_unverified" (advisory only)


def resolve_identity_from_headers(request: Request) -> CallerIdentity | None:
    """Extract caller identity from well-known request headers.

    Returns None if no identity headers are present.
    """
    user_id = (request.headers.get("x-user-id") or "").strip()
    if not user_id:
        return None
    team = (request.headers.get("x-team-id") or "").strip() or None
    roles_raw = (request.headers.get("x-user-roles") or "").strip()
    roles = [r.strip() for r in roles_raw.split(",") if r.strip()] if roles_raw else []
    return CallerIdentity(
        user_id=user_id,
        roles=roles,
        team=team,
        source="header_unverified",
    )
