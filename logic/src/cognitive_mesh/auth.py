"""Verifies who is calling. Identity comes ONLY from a Clerk-signed session token."""

from dataclasses import dataclass
from typing import Annotated

from clerk_backend_api.security import authenticate_request_async
from clerk_backend_api.security.types import AuthenticateRequestOptions
from fastapi import Depends, HTTPException, Request, status

from cognitive_mesh.config import Settings


@dataclass(frozen=True)
class VerifiedIdentity:
    """Claims from a token whose signature Clerk's keys verified."""

    clerk_user_id: str
    session_id: str | None


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


async def verify_clerk_session(
    request: Request, settings: Annotated[Settings, Depends(get_app_settings)]
) -> VerifiedIdentity:
    """Verify the Clerk session token (Authorization: Bearer <token>)."""
    if not settings.clerk_secret_key:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "CLERK_SECRET_KEY is not configured"
        )

    state = await authenticate_request_async(
        request,
        AuthenticateRequestOptions(
            secret_key=settings.clerk_secret_key,
            authorized_parties=settings.frontend_origins,
            accepts_token=["session_token"],
        ),
    )
    if not state.is_signed_in or not state.payload or not state.payload.get("sub"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, state.message or "Not signed in")

    return VerifiedIdentity(
        clerk_user_id=state.payload["sub"],
        session_id=state.payload.get("sid"),
    )


VerifiedIdentityDep = Annotated[VerifiedIdentity, Depends(verify_clerk_session)]
