"""Tests for JWT authentication module."""

from __future__ import annotations

import time

import pytest

from gateway.auth.jwt_auth import validate_jwt, _jwks_cache


@pytest.fixture(autouse=True)
def _clear_jwks_cache():
    """Clear JWKS cache between tests."""
    _jwks_cache.clear()
    yield
    _jwks_cache.clear()


def _make_hs256_token(payload: dict, secret: str = "test-secret") -> str:
    """Helper to create an HS256 JWT."""
    jwt = pytest.importorskip("jwt")
    return jwt.encode(payload, secret, algorithm="HS256")


class TestValidateJWT:
    def test_valid_hs256(self):
        token = _make_hs256_token({"sub": "alice", "email": "alice@co.com", "roles": ["admin"], "team": "eng"})
        identity = validate_jwt(
            token, secret="test-secret", algorithms=["HS256"],
            user_claim="sub", email_claim="email", roles_claim="roles", team_claim="team",
        )
        assert identity is not None
        assert identity.user_id == "alice"
        assert identity.email == "alice@co.com"
        assert identity.roles == ["admin"]
        assert identity.team == "eng"
        assert identity.source == "jwt"

    def test_expired_token(self):
        token = _make_hs256_token({"sub": "alice", "exp": int(time.time()) - 3600})
        identity = validate_jwt(token, secret="test-secret", algorithms=["HS256"])
        assert identity is None

    def test_wrong_secret(self):
        token = _make_hs256_token({"sub": "alice"}, secret="correct-secret")
        identity = validate_jwt(token, secret="wrong-secret", algorithms=["HS256"])
        assert identity is None

    def test_wrong_issuer(self):
        token = _make_hs256_token({"sub": "alice", "iss": "real-issuer"})
        identity = validate_jwt(token, secret="test-secret", algorithms=["HS256"], issuer="expected-issuer")
        assert identity is None

    def test_wrong_audience(self):
        token = _make_hs256_token({"sub": "alice", "aud": "other-app"})
        identity = validate_jwt(token, secret="test-secret", algorithms=["HS256"], audience="my-app")
        assert identity is None

    def test_missing_user_claim(self):
        token = _make_hs256_token({"email": "alice@co.com"})
        identity = validate_jwt(token, secret="test-secret", algorithms=["HS256"])
        assert identity is None

    def test_custom_claims(self):
        token = _make_hs256_token({
            "user_id": "bob",
            "mail": "bob@co.com",
            "groups": ["viewer", "editor"],
            "department": "sales",
        })
        identity = validate_jwt(
            token, secret="test-secret", algorithms=["HS256"],
            user_claim="user_id", email_claim="mail",
            roles_claim="groups", team_claim="department",
        )
        assert identity is not None
        assert identity.user_id == "bob"
        assert identity.email == "bob@co.com"
        assert identity.roles == ["viewer", "editor"]
        assert identity.team == "sales"

    def test_empty_token(self):
        identity = validate_jwt("", secret="test-secret", algorithms=["HS256"])
        assert identity is None

    def test_no_config(self):
        """No secret or JWKS URL configured."""
        token = _make_hs256_token({"sub": "alice"})
        identity = validate_jwt(token, algorithms=["HS256"])
        assert identity is None

    def test_roles_as_csv_string(self):
        token = _make_hs256_token({"sub": "alice", "roles": "admin,editor"})
        identity = validate_jwt(token, secret="test-secret", algorithms=["HS256"])
        assert identity is not None
        assert identity.roles == ["admin", "editor"]

    def test_frozen_identity(self):
        token = _make_hs256_token({"sub": "alice"})
        identity = validate_jwt(token, secret="test-secret", algorithms=["HS256"])
        assert identity is not None
        with pytest.raises(AttributeError):
            identity.user_id = "bob"  # type: ignore[misc]
