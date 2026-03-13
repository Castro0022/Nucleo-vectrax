"""
Vectrax Core — Auth Routes
============================
Multi-user authentication endpoints.

  POST /v1/auth/register  — create a new user (owner-only, or self-register)
  POST /v1/auth/login     — authenticate and receive a bearer token
  POST /v1/auth/logout    — revoke current token
  GET  /v1/auth/me        — current user info
  GET  /v1/auth/users     — list all users (owner-only)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from services.core.auth import (
    AuthContext,
    get_current_user,
    require_role,
    require_token,
)
from services.core.models import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    UserInfo,
    UserListResponse,
)
from services.core.tokens import TokenManager
from services.core.users import UserStore
from services.core.independence import get_independence_engine

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("vectrax.core.routes.auth")

# Lazy singletons (shared with auth.py via same DB)
_user_store: UserStore | None = None
_token_mgr: TokenManager | None = None


def _store() -> UserStore:
    global _user_store
    if _user_store is None:
        _user_store = UserStore()
    return _user_store


def _tokens() -> TokenManager:
    global _token_mgr
    if _token_mgr is None:
        _token_mgr = TokenManager()
    return _token_mgr


# ---------------------------------------------------------------------------
# POST /v1/auth/register
# ---------------------------------------------------------------------------

@router.post("/register", response_model=RegisterResponse)
async def register(
    body: RegisterRequest,
    ctx: AuthContext = Depends(require_role("owner")),
):
    """
    Create a new user with full independence provisioning.

    Executes the 7-step User Independence Flow:
      1. Identity creation
      2. Secure credentials (auto-generated if no password)
      3. Domain assignment
      4. Isolated base constellation
      5. Cognitive path seed
      6. Personalized portal entry
      7. Nucleus integrity validation
    """
    engine = get_independence_engine()
    pkg = engine.provision(
        username=body.username,
        role=body.role,
        domain=body.domain,
        password=body.password,
    )

    if not pkg.validation_passed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=pkg.validation_errors[0] if pkg.validation_errors else "Provisioning failed",
        )

    # Mark user as fully provisioned
    _store().mark_provisioned(pkg.user_id)

    return RegisterResponse(
        id=pkg.user_id,
        username=pkg.username,
        role=pkg.role,
        channel=pkg.channel,
        message="User created with full independence package",
        domain=pkg.domain,
        domain_context=pkg.domain_context,
        domain_rules=pkg.domain_rules,
        domain_routes=pkg.domain_routes,
        constellation_id=pkg.constellation_id,
        seed_stars_created=pkg.seed_stars_created,
        portal_url=pkg.portal_url,
        must_change_password=pkg.must_change_password,
        token=pkg.token,
        token_expires_in=pkg.token_expires_in,
        nucleus_intact=pkg.nucleus_intact,
        validation_passed=pkg.validation_passed,
        validation_errors=pkg.validation_errors,
    )


# ---------------------------------------------------------------------------
# POST /v1/auth/login
# ---------------------------------------------------------------------------

@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    """
    Authenticate with username + password.  Returns a bearer token.
    """
    user = _store().authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    token = _tokens().issue_token(
        user_id=user.id,
        username=user.username,
        role=user.role,
        channel=user.channel,
    )

    return LoginResponse(
        token=token,
        expires_in=30 * 86400,  # 30 days
        role=user.role,
        channel=user.channel,
        username=user.username,
    )


# ---------------------------------------------------------------------------
# POST /v1/auth/logout
# ---------------------------------------------------------------------------

@router.post("/logout")
async def logout(
    ctx: AuthContext = Depends(require_token),
):
    """
    Revoke all tokens for the current user.
    """
    count = _tokens().revoke_all_for_user(ctx.user_id)
    return {"revoked": count, "message": "Logged out"}


# ---------------------------------------------------------------------------
# GET /v1/auth/me
# ---------------------------------------------------------------------------

@router.get("/me", response_model=UserInfo)
async def me(ctx: AuthContext = Depends(get_current_user)):
    """
    Return info about the currently authenticated user.
    """
    user = _store().get_by_username(ctx.username)
    if user is None:
        # Legacy token (no DB record)
        return UserInfo(
            id=ctx.user_id,
            username=ctx.username,
            role=ctx.role,
            channel=ctx.channel,
            created_at=0,
            is_active=True,
        )
    return UserInfo(**user.to_dict())


# ---------------------------------------------------------------------------
# POST /v1/auth/change-password
# ---------------------------------------------------------------------------

@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    ctx: AuthContext = Depends(require_token),
):
    """
    Change the current user's password.
    Required on first login if must_change_password is set.
    """
    store = _store()
    user = store.authenticate(ctx.username, body.current_password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 8 characters",
        )

    store.change_password(user.id, body.new_password)
    logger.info("Password changed for user '%s'", ctx.username)
    return {"message": "Password changed successfully", "must_change_password": False}


# ---------------------------------------------------------------------------
# GET /v1/auth/users  (owner-only)
# ---------------------------------------------------------------------------

@router.get("/users", response_model=UserListResponse)
async def list_users(
    ctx: AuthContext = Depends(require_role("owner")),
):
    """
    List all registered users.  Owner-only.
    """
    users = _store().list_users(active_only=False)
    return UserListResponse(
        users=[UserInfo(**u.to_dict()) for u in users],
        total=len(users),
    )
