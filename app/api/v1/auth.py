"""Authentication routes, E28 (agent.md §7.8, §6)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import AuthServiceDep, require_user
from app.schemas.auth import LoginRequest, ProfileResponse, RefreshRequest, TokenResponse
from app.services.auth import Principal

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in",
    description=(
        "Exchange a username or email and password for an access and refresh token (agent.md "
        "§7.8). Legacy myth/auth password hashes verify unchanged. Inactive, banned, deleted "
        "and locked accounts all answer 401 like a wrong password."
    ),
)
async def login(body: LoginRequest, service: AuthServiceDep) -> TokenResponse:
    """Issue a token pair."""
    pair = await service.login(body.username_or_email, body.password)
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate tokens",
    description=(
        "Exchange a refresh token for a new pair. The old refresh token is revoked; replaying "
        "a revoked token revokes every token of that user."
    ),
)
async def refresh(body: RefreshRequest, service: AuthServiceDep) -> TokenResponse:
    """Rotate the refresh token."""
    pair = await service.refresh(body.refresh_token)
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.get(
    "/me",
    response_model=ProfileResponse,
    summary="Current user",
    description="Profile and roles of the caller (agent.md §7.8).",
)
async def me(
    principal: Annotated[Principal, Depends(require_user)], service: AuthServiceDep
) -> ProfileResponse:
    """Return the caller's profile."""
    profile = await service.me(principal)
    return ProfileResponse(
        id=profile.id,
        email=profile.email,
        username=profile.username,
        fullname=profile.fullname,
        roles=profile.roles,
    )
