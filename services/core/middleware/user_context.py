"""
Vectrax Core — User Context Middleware
=========================================
FastAPI dependency helpers that extract AuthContext from the request
and enforce permission checks using the existing ``core.roles`` system.

This module bridges the auth layer with the permission layer without
duplicating either.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status

from core.roles import Permission, has_permission
from services.core.auth import AuthContext, require_token


def require_permission(perm: Permission | str):
    """
    Factory for a FastAPI dependency that checks a specific permission.

    Uses ``core.roles.has_permission()`` — the existing RBAC engine.

    Usage::

        @router.post("/events", dependencies=[Depends(require_permission("event.write"))])
    """
    async def _check(ctx: AuthContext = Depends(require_token)) -> AuthContext:
        if not has_permission(ctx.role, perm):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{perm}' required. Role '{ctx.role}' does not have it.",
            )
        return ctx
    return _check


def inject_user_context(ctx: AuthContext = Depends(require_token)) -> dict:
    """
    Dependency that returns a dict with channel/owner ready to pass
    to engine.ingest(), db queries, etc.

    Usage::

        @router.post("/ingest")
        async def ingest(uctx: dict = Depends(inject_user_context)):
            engine.ingest(text, channel=uctx["channel"], owner=uctx["owner"])
    """
    return {
        "user_id": ctx.user_id,
        "username": ctx.username,
        "role": ctx.role,
        "channel": ctx.channel,
        "owner": ctx.owner,
    }
