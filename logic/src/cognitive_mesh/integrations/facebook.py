"""The exact Facebook Page actions that can be requested, and their payload rules.

Tool slugs and inputs come from Composio's Facebook toolkit docs (version 20261116_01), and response
fields from Meta's Pages API docs: POST /{page-id}/feed returns `id`, and POST /{page-id}/photos
returns `id` and `post_id`. Facebook allows posting only to Pages, never to personal profiles.
"""

import re
from typing import Any

from cognitive_mesh.integrations import media

TOOLKIT = "facebook"
POST_ACTION = "facebook.publish_post"
PHOTO_ACTION = "facebook.publish_photo"
PAGES_TOOL = "FACEBOOK_LIST_MANAGED_PAGES"
POST_TOOL = "FACEBOOK_CREATE_POST"
PHOTO_TOOL = "FACEBOOK_CREATE_PHOTO_POST"
WRITE_SCOPES = ("pages_manage_posts",)
POSTING_TASKS = frozenset({"CREATE_CONTENT", "MANAGE"})
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif"})
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_MESSAGE_CHARS = 5000
PAGES_LIMIT = 100

_NUMERIC = re.compile(r"^[0-9]{1,32}$")
_POST_ID = re.compile(r"^[0-9]{1,32}(?:_[0-9]{1,32})?$")


class InvalidSocialActionError(ValueError):
    """The requested post does not meet the approved rules."""


def _check_page(page: Any) -> None:
    if not isinstance(page, dict) or set(page) != {"id", "name"}:
        raise InvalidSocialActionError("A Facebook Page is required")
    if not isinstance(page["id"], str) or not _NUMERIC.match(page["id"]):
        raise InvalidSocialActionError("A valid Facebook Page id is required")


def _check_message(message: Any, *, required: bool) -> None:
    if not isinstance(message, str) or len(message) > MAX_MESSAGE_CHARS:
        raise InvalidSocialActionError(f"The post must be under {MAX_MESSAGE_CHARS} characters")
    if required and not message.strip():
        raise InvalidSocialActionError("The post text is empty")


def _post_arguments(page_id: str, message: str) -> dict[str, Any]:
    return {"page_id": page_id, "message": message, "published": True}


def _photo_arguments(page_id: str, message: str, image_url: str) -> dict[str, Any]:
    arguments: dict[str, Any] = {"page_id": page_id, "url": image_url, "published": True}
    if message:
        arguments["message"] = message
    return arguments


def validate_post(payload: dict[str, Any]) -> None:
    if set(payload) != {"page", "message", "arguments"}:
        raise InvalidSocialActionError("Unexpected post fields")
    _check_page(payload["page"])
    _check_message(payload["message"], required=True)
    if payload["arguments"] != _post_arguments(payload["page"]["id"], payload["message"]):
        raise InvalidSocialActionError("The post must match the approved text and Page")


def check_design(payload: dict[str, Any]) -> None:
    """The Canva design a banner came from. Shown to the user; never sent to Meta."""
    design = payload.get("design")
    if design is None:
        return
    if not isinstance(design, dict) or set(design) != {"id", "edit_url"}:
        raise InvalidSocialActionError("Unexpected design fields")
    if not isinstance(design["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", design["id"]):
        raise InvalidSocialActionError("A valid Canva design id is required")
    edit_url = design["edit_url"]
    if edit_url is not None and not str(edit_url).startswith("https://www.canva.com/"):
        raise InvalidSocialActionError("The Canva link is not valid")


def validate_photo(payload: dict[str, Any]) -> None:
    if set(payload) - {"design"} != {"page", "message", "image", "arguments"}:
        raise InvalidSocialActionError("Unexpected post fields")
    check_design(payload)
    _check_page(payload["page"])
    _check_message(payload["message"], required=False)
    try:
        media.check_image_record(payload["image"])
    except ValueError as exc:
        raise InvalidSocialActionError(str(exc)) from exc
    if payload["image"]["content_type"] not in IMAGE_TYPES:
        raise InvalidSocialActionError("Facebook accepts JPEG, PNG or GIF images")
    expected = _photo_arguments(
        payload["page"]["id"], payload["message"], payload["image"]["url"]
    )
    if payload["arguments"] != expected:
        raise InvalidSocialActionError("The post must match the approved image, text and Page")


def build_post_payload(*, page: dict[str, str], message: str) -> dict[str, Any]:
    payload = {"page": page, "message": message, "arguments": _post_arguments(page["id"], message)}
    validate_post(payload)
    return payload


def build_photo_payload(
    *,
    page: dict[str, str],
    message: str,
    image: dict[str, Any],
    design: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "page": page, "message": message, "image": image,
        "arguments": _photo_arguments(page["id"], message, image["url"]),
    }
    if design is not None:
        payload["design"] = design
    validate_photo(payload)
    return payload


def pages_arguments() -> dict[str, Any]:
    return {"fields": "id,name,tasks", "limit": PAGES_LIMIT}


def _unwrap(data: Any) -> Any:
    if isinstance(data, dict) and isinstance(data.get("response_data"), dict):
        return data["response_data"]
    return data


def pages_from(data: Any) -> tuple[list[dict[str, str]], bool]:
    """(Pages this user can post to, whether more exist). Unreadable results raise."""
    body = _unwrap(data)
    rows = body.get("data") if isinstance(body, dict) else None
    if isinstance(rows, dict):
        body, rows = rows, rows.get("data")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise InvalidSocialActionError("Facebook returned a Page list I couldn't read")
    pages = []
    for row in rows:
        page_id, name, tasks = str(row.get("id") or ""), row.get("name"), row.get("tasks")
        if not _NUMERIC.match(page_id) or not isinstance(name, str):
            raise InvalidSocialActionError("Facebook returned a Page I couldn't read")
        if isinstance(tasks, list) and POSTING_TASKS & set(tasks):
            pages.append({"id": page_id, "name": name})
    paging = body.get("paging") if isinstance(body, dict) else None
    return pages, isinstance(paging, dict) and bool(paging.get("next"))


def post_result_id(data: Any) -> str | None:
    body = _unwrap(data)
    value = body.get("id") if isinstance(body, dict) else None
    return value if isinstance(value, str) and _POST_ID.match(value) else None


def photo_result_id(data: Any) -> str | None:
    body = _unwrap(data)
    if not isinstance(body, dict):
        return None
    value = body.get("post_id") or body.get("id")
    return value if isinstance(value, str) and _POST_ID.match(value) else None


def result_url(_data: Any, post_id: str | None) -> str | None:
    return f"https://www.facebook.com/{post_id}" if post_id else None


async def check_photo_unchanged(payload: dict[str, Any]) -> None:
    await media.verify_unchanged(payload["image"], IMAGE_TYPES)
