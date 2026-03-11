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

    Checks generic headers first (X-User-Id, X-User-Email, X-User-Roles, X-Team-Id),
    then falls back to OpenWebUI-specific headers (X-OpenWebUI-User-Name,
    X-OpenWebUI-User-Id, X-OpenWebUI-User-Email, X-OpenWebUI-User-Role).

    Returns None if no identity headers are present.
    """
    # User ID: generic → OpenWebUI-User-Name → OpenWebUI-User-Id
    user_id = (
        (request.headers.get("x-user-id") or "").strip()
        or (request.headers.get("x-openwebui-user-name") or "").strip()
        or (request.headers.get("x-openwebui-user-id") or "").strip()
    )
    if not user_id:
        return None

    # Email: generic → OpenWebUI
    email = (
        (request.headers.get("x-user-email") or "").strip()
        or (request.headers.get("x-openwebui-user-email") or "").strip()
    )

    # Roles: generic (comma-separated) → OpenWebUI (single role)
    roles_raw = (request.headers.get("x-user-roles") or "").strip()
    if roles_raw:
        roles = [r.strip() for r in roles_raw.split(",") if r.strip()]
    else:
        owui_role = (request.headers.get("x-openwebui-user-role") or "").strip()
        roles = [owui_role] if owui_role else []

    # Team: generic only (no OpenWebUI equivalent)
    team = (request.headers.get("x-team-id") or "").strip() or None

    return CallerIdentity(
        user_id=user_id,
        email=email,
        roles=roles,
        team=team,
        source="header_unverified",
    )
