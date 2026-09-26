"""Supabase providers (#186). Auth is a preset over ``oidc``; asymmetric keys only.

A project still on the legacy HS256 JWT secret publishes no JWKS and is not
supported: switch it to asymmetric signing keys (RS256/ES256) first.
"""

from harness.adapters.supabase.auth import supabase_auth

__all__ = ["supabase_auth"]
