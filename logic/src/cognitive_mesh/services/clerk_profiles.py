"""Reads user profile data from Clerk's Backend API (server-to-server, trusted)."""

from dataclasses import dataclass
from typing import Protocol

from clerk_backend_api import Clerk


@dataclass(frozen=True)
class ClerkProfile:
    clerk_user_id: str
    email: str | None
    first_name: str | None
    last_name: str | None
    image_url: str | None


class ProfileFetchError(Exception):
    pass


class ProfileFetcher(Protocol):
    async def __call__(self, clerk_user_id: str) -> ClerkProfile: ...


class ClerkApiProfileFetcher:
    def __init__(self, secret_key: str) -> None:
        self._secret_key = secret_key

    async def __call__(self, clerk_user_id: str) -> ClerkProfile:
        try:
            async with Clerk(bearer_auth=self._secret_key) as clerk:
                user = await clerk.users.get_async(user_id=clerk_user_id)
        except Exception as exc:  # network/API errors from the SDK
            raise ProfileFetchError(f"Could not load Clerk user {clerk_user_id}") from exc

        primary_email = next(
            (
                e.email_address
                for e in user.email_addresses
                if e.id == user.primary_email_address_id
            ),
            None,
        )
        return ClerkProfile(
            clerk_user_id=user.id,
            email=primary_email,
            first_name=user.first_name,
            last_name=user.last_name,
            image_url=user.image_url,
        )
