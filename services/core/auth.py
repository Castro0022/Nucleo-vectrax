"""
Vectrax Core — Authentication
==============================
Multi-user bearer-token auth with legacy fallback.

Tokens are validated via TokenManager (per-user tokens stored as
SHA-256 hashes).  The legacy VX_API_TOKEN env var is still accepted
for backwards compatibility with existing agents.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.core.tokens import TokenManager

_bearer = HTTPBearer(auto_error=False)

# Module-level singletons (lazy)
_token_manager: TokenManager | None = None


def _get_token_manager() -> TokenManager:
    global _token_manager
    if _token_manager is None:
        _token_manager = TokenManager()
    return _token_manager


# ---------------------------------------------------------------------------
# AuthContext — replaces the old plain-string return
# ---------------------------------------------------------------------------

@dataclass
class AuthContext:
    """Identity resolved from a bearer token."""
    user_id: str
    username: str
    role: str       # owner | operator | viewer
    channel: str    # creator | user
    owner: str      # username (used as 'owner' param for engine/db)


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def require_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> AuthContext:
    """
    Dependency that enforces Bearer token auth.
    Returns an AuthContext with the caller's identity.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )

    tm = _get_token_manager()
    info = tm.validate_token(credentials.credentials)

    if info is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired API token",
        )

    return AuthContext(
        user_id=info.user_id,
        username=info.username,
        role=info.role,
        channel=info.channel,
        owner=info.owner,
    )


async def get_current_user(
    ctx: AuthContext = Depends(require_token),
) -> AuthContext:
    """Alias dependency — returns the authenticated user context."""
    return ctx


def require_role(*allowed_roles: str):
    """
    Factory for a FastAPI dependency that restricts access to specific roles.

    Usage::

        @router.get("/admin", dependencies=[Depends(require_role("owner"))])
    """
    async def _check(ctx: AuthContext = Depends(require_token)) -> AuthContext:
        if ctx.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{ctx.role}' is not permitted. Required: {', '.join(allowed_roles)}",
            )
        return ctx
    return _check
