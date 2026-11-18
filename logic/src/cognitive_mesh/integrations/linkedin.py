"""The exact LinkedIn action AI employees may request, and its payload rules.

Payload = input of Composio's LINKEDIN_CREATE_LINKED_IN_POST (verified in the
toolkit docs): author URN, commentary (<= 3000 chars), visibility, lifecycleState.
This payload is what the user approves and what gets signed.
"""

from typing import Any, Literal

from cognitive_mesh.agents.schemas import LINKEDIN_MAX_CHARS

ACTION_TYPE = "linkedin.publish_post"
TOOLKIT = "linkedin"
POST_TOOL = "LINKEDIN_CREATE_LINKED_IN_POST"
REQUIRED_SCOPE = "w_member_social"

Visibility = Literal["PUBLIC", "CONNECTIONS"]
VISIBILITIES: tuple[str, ...] = ("PUBLIC", "CONNECTIONS")
DEFAULT_VISIBILITY: Visibility = "PUBLIC"

_KEYS = {"author", "commentary", "visibility", "lifecycleState"}


class InvalidPayloadError(ValueError):
    pass


def person_urn(person_id: str) -> str:
    if not person_id or ":" in person_id:
        raise InvalidPayloadError("A LinkedIn person id is required")
    return f"urn:li:person:{person_id}"


def build_post_payload(*, author_urn: str, commentary: str, visibility: str) -> dict[str, Any]:
    payload = {
        "author": author_urn,
        "commentary": commentary,
        "visibility": visibility,
        "lifecycleState": "PUBLISHED",
    }
    validate_post_payload(payload)
    return payload


def validate_post_payload(payload: dict[str, Any]) -> None:
    """Server-side rules; the LLM and the browser can't widen them."""
    if set(payload) != _KEYS:
        raise InvalidPayloadError(f"Payload must have exactly {sorted(_KEYS)}")
    if payload["visibility"] not in VISIBILITIES:
        raise InvalidPayloadError(f"Visibility must be one of {VISIBILITIES}")
    if not str(payload["author"]).startswith("urn:li:person:"):
        raise InvalidPayloadError("Author must be the connected member's person URN")
    text = payload["commentary"]
    if not isinstance(text, str) or not text.strip() or len(text) > LINKEDIN_MAX_CHARS:
        raise InvalidPayloadError(f"Commentary must be 1-{LINKEDIN_MAX_CHARS} characters")
    if payload["lifecycleState"] != "PUBLISHED":
        raise InvalidPayloadError("lifecycleState must be PUBLISHED")
