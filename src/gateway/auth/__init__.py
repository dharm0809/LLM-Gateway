"""Request authentication (API key)."""

from gateway.auth.api_key import get_api_key_from_request, require_api_key_if_configured

__all__ = ["get_api_key_from_request", "require_api_key_if_configured"]
