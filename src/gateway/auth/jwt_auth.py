"""JWT validation for SSO/OIDC authentication.

Requires optional dependency: pip install 'walacor-gateway[auth]' (pyjwt[crypto]).
Fails gracefully if pyjwt is not installed.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from gateway.auth.identity import CallerIdentity

logger = logging.getLogger(__name__)

# Module-level JWKS client cache: {jwks_url: (PyJWKClient, fetch_timestamp)}
_jwks_cache: dict[str, tuple[Any, float]] = {}
_JWKS_CACHE_TTL = 300  # 5 minutes (shorter for faster key rotation response)

# Symmetric algorithms (require secret)
_SYMMETRIC_ALGS = {"HS256", "HS384", "HS512"}
# Asymmetric algorithms (require JWKS/public key)
_ASYMMETRIC_ALGS = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"}


def _get_jwks_client(jwks_url: str) -> Any:
    """Return a cached PyJWKClient for the given JWKS URL."""
    import jwt  # noqa: F811

    now = time.time()
    cached = _jwks_cache.get(jwks_url)
    if cached and (now - cached[1]) < _JWKS_CACHE_TTL:
        return cached[0]
    client = jwt.PyJWKClient(jwks_url)
    _jwks_cache[jwks_url] = (client, now)
    return client


def validate_jwt(
    token: str,
    *,
    secret: str = "",
    jwks_url: str = "",
    issuer: str = "",
    audience: str = "",
    algorithms: list[str] | None = None,
    user_claim: str = "sub",
    email_claim: str = "email",
    roles_claim: str = "roles",
    team_claim: str = "",
) -> CallerIdentity | None:
    """Validate a JWT and extract caller identity from its claims.

    Returns CallerIdentity on success, None on any validation failure.
    Supports HS256 (via secret) and RS256/ES256 (via jwks_url).
    """
    try:
        import jwt as pyjwt
    except ImportError:
        logger.error(
            "pyjwt is not installed — JWT auth unavailable. "
            "Install with: pip install 'walacor-gateway[auth]'"
        )
        return None

    if not token:
        return None

    algs = algorithms or ["RS256"]

    try:
        # Determine signing key and enforce algorithm-key type match
        if jwks_url:
            # JWKS = asymmetric keys only — reject symmetric algorithms to prevent algorithm confusion
            safe_algs = [a for a in algs if a in _ASYMMETRIC_ALGS]
            if not safe_algs:
                logger.warning("JWT auth: no asymmetric algorithms in %s for JWKS mode — rejecting", algs)
                return None
            if len(safe_algs) < len(algs):
                logger.warning("JWT auth: stripped symmetric algorithms %s from JWKS mode (algorithm confusion prevention)",
                               [a for a in algs if a not in _ASYMMETRIC_ALGS])
            algs = safe_algs
            jwks_client = _get_jwks_client(jwks_url)
            try:
                signing_key = jwks_client.get_signing_key_from_jwt(token)
            except Exception:
                # JWKS fetch failed — clear cache so next request gets a fresh client
                _jwks_cache.pop(jwks_url, None)
                raise
            key = signing_key.key
        elif secret:
            # Secret = symmetric keys only — reject asymmetric algorithms
            safe_algs = [a for a in algs if a in _SYMMETRIC_ALGS]
            if not safe_algs:
                logger.warning("JWT auth: no symmetric algorithms in %s for secret mode — rejecting", algs)
                return None
            if len(safe_algs) < len(algs):
                logger.warning("JWT auth: stripped asymmetric algorithms %s from secret mode (algorithm confusion prevention)",
                               [a for a in algs if a not in _SYMMETRIC_ALGS])
            algs = safe_algs
            key = secret
        else:
            logger.warning("JWT auth: no secret or jwks_url configured")
            return None

        if not issuer:
            logger.debug("JWT auth: no issuer configured — iss claim not validated")
        if not audience:
            logger.debug("JWT auth: no audience configured — aud claim not validated")

        # Build decode options
        decode_kwargs: dict[str, Any] = {
            "algorithms": algs,
        }
        if issuer:
            decode_kwargs["issuer"] = issuer
        if audience:
            decode_kwargs["audience"] = audience

        claims = pyjwt.decode(token, key, **decode_kwargs)

    except pyjwt.ExpiredSignatureError:
        logger.debug("JWT expired")
        return None
    except pyjwt.InvalidIssuerError:
        logger.debug("JWT invalid issuer")
        return None
    except pyjwt.InvalidAudienceError:
        logger.debug("JWT invalid audience")
        return None
    except pyjwt.InvalidTokenError as e:
        logger.debug("JWT validation failed: %s", e)
        return None
    except Exception as e:
        logger.warning("JWT validation unexpected error: %s", e)
        return None

    # Extract identity from claims
    user_id = str(claims.get(user_claim, "")).strip()
    if not user_id:
        logger.debug("JWT missing user claim '%s'", user_claim)
        return None

    email = str(claims.get(email_claim, "")).strip()

    roles_val = claims.get(roles_claim, [])
    if isinstance(roles_val, str):
        roles = [r.strip() for r in roles_val.split(",") if r.strip()]
    elif isinstance(roles_val, list):
        roles = [str(r) for r in roles_val]
    else:
        roles = []

    team = str(claims.get(team_claim, "")).strip() if team_claim else None
    if team == "":
        team = None

    return CallerIdentity(
        user_id=user_id,
        email=email,
        roles=roles,
        team=team,
        source="jwt",
    )
