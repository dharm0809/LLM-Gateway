"""Tests for caller identity resolution from headers."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from gateway.auth.identity import CallerIdentity, resolve_identity_from_headers


def _make_request(headers: dict[str, str] | None = None) -> Request:
    """Create a minimal Starlette Request with the given headers."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


class TestCallerIdentity:
    def test_frozen(self):
        identity = CallerIdentity(user_id="alice", email="alice@co.com")
        with pytest.raises(AttributeError):
            identity.user_id = "bob"  # type: ignore[misc]

    def test_defaults(self):
        identity = CallerIdentity(user_id="alice")
        assert identity.email == ""
        assert identity.roles == []
        assert identity.team is None
        assert identity.source == "header_unverified"


class TestResolveIdentityFromHeaders:
    def test_full_headers(self):
        request = _make_request({
            "x-user-id": "alice",
            "x-team-id": "engineering",
            "x-user-roles": "admin, viewer",
        })
        identity = resolve_identity_from_headers(request)
        assert identity is not None
        assert identity.user_id == "alice"
        assert identity.team == "engineering"
        assert identity.roles == ["admin", "viewer"]
        assert identity.source == "header_unverified"

    def test_user_only(self):
        request = _make_request({"x-user-id": "bob"})
        identity = resolve_identity_from_headers(request)
        assert identity is not None
        assert identity.user_id == "bob"
        assert identity.team is None
        assert identity.roles == []

    def test_missing_user_id(self):
        request = _make_request({"x-team-id": "eng"})
        identity = resolve_identity_from_headers(request)
        assert identity is None

    def test_empty_headers(self):
        request = _make_request({})
        identity = resolve_identity_from_headers(request)
        assert identity is None

    def test_whitespace_user_id(self):
        request = _make_request({"x-user-id": "  "})
        identity = resolve_identity_from_headers(request)
        assert identity is None
