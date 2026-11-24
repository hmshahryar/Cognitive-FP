"""The exact Instagram action that can be requested, and its payload rules.

Tool slugs and inputs come from Composio's Instagram toolkit docs (version 20261112_00), which uses
Instagram Login. Meta's docs: publishing needs a Business or Creator account and the account's
`user_id` (not the app-scoped `id`); images must be JPEG; at most 100 API posts per 24 hours.
Publishing takes two calls, a media container and then publish, under one approval.
"""

import re
from typing import Any

from cognitive_mesh.integrations import media
from cognitive_mesh.integrations.facebook import InvalidSocialActionError, check_design
from cognitive_mesh.integrations.google_sheets import FollowUp

TOOLKIT = "instagram"
PHOTO_ACTION = "instagram.publish_photo"
USER_TOOL = "INSTAGRAM_GET_USER_INFO"
LIMIT_TOOL = "INSTAGRAM_GET_IG_USER_CONTENT_PUBLISHING_LIMIT"
CONTAINER_TOOL = "INSTAGRAM_POST_IG_USER_MEDIA"
PUBLISH_TOOL = "INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH"
WRITE_SCOPES = ("instagram_business_content_publish",)
PROFESSIONAL_TYPES = frozenset({"BUSINESS", "MEDIA_CREATOR"})
IMAGE_TYPES = frozenset({"image/jpeg"})
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_CAPTION_CHARS = 2200
MIN_WIDTH = 320
MIN_RATIO, MAX_RATIO = 0.8, 1.91  # 4:5 to 1.91:1

_NUMERIC = re.compile(r"^[0-9]{1,32}$")


def _arguments(account_id: str, caption: str, image_url: str) -> dict[str, Any]:
    arguments: dict[str, Any] = {"ig_user_id": account_id, "image_url": image_url}
    if caption:
        arguments["caption"] = caption
    return arguments


def check_image_shape(width: int | None, height: int | None) -> None:
    if not width or not height:
        raise InvalidSocialActionError("I couldn't read the image size")
    if width < MIN_WIDTH:
        raise InvalidSocialActionError(f"Instagram needs an image at least {MIN_WIDTH} pixels wide")
    if not MIN_RATIO <= width / height <= MAX_RATIO:
        raise InvalidSocialActionError(
            "Instagram needs an image between portrait 4:5 and landscape 1.91:1"
        )


def validate_photo(payload: dict[str, Any]) -> None:
    if set(payload) - {"design"} != {"account", "caption", "image", "arguments"}:
        raise InvalidSocialActionError("Unexpected post fields")
    check_design(payload)
    account = payload["account"]
    if not isinstance(account, dict) or set(account) != {"id", "username"}:
        raise InvalidSocialActionError("An Instagram account is required")
    if not isinstance(account["id"], str) or not _NUMERIC.match(account["id"]):
        raise InvalidSocialActionError("A valid Instagram account id is required")
    caption = payload["caption"]
    if not isinstance(caption, str) or len(caption) > MAX_CAPTION_CHARS:
        raise InvalidSocialActionError(f"The caption must be under {MAX_CAPTION_CHARS} characters")
    try:
        media.check_image_record(payload["image"])
    except ValueError as exc:
        raise InvalidSocialActionError(str(exc)) from exc
    image = payload["image"]
    if image["content_type"] not in IMAGE_TYPES:
        raise InvalidSocialActionError("Instagram accepts JPEG images only")
    check_image_shape(image["width"], image["height"])
    if payload["arguments"] != _arguments(account["id"], caption, image["url"]):
        raise InvalidSocialActionError("The post must match the approved image, caption and account")


def build_photo_payload(
    *,
    account: dict[str, str],
    caption: str,
    image: dict[str, Any],
    design: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "account": account, "caption": caption, "image": image,
        "arguments": _arguments(account["id"], caption, image["url"]),
    }
    if design is not None:
        payload["design"] = design
    validate_photo(payload)
    return payload


def user_arguments() -> dict[str, Any]:
    return {"ig_user_id": "me", "fields": "user_id,username,account_type"}


def _unwrap(data: Any) -> Any:
    for key in ("response_data", "data"):
        if isinstance(data, dict) and isinstance(data.get(key), dict):
            return data[key]
    return data


def account_from(data: Any) -> dict[str, str]:
    """The professional account to publish as. Personal accounts are refused."""
    body = _unwrap(data)
    if not isinstance(body, dict):
        raise InvalidSocialActionError("Instagram returned account details I couldn't read")
    account_id = str(body.get("user_id") or "")
    if not _NUMERIC.match(account_id):
        raise InvalidSocialActionError("Instagram didn't return the account id needed to post")
    kind = body.get("account_type")
    if kind is not None and kind not in PROFESSIONAL_TYPES:
        raise InvalidSocialActionError(
            "Instagram allows posting only from Business or Creator accounts"
        )
    return {"id": account_id, "username": str(body.get("username") or "")}


def limit_arguments(account_id: str) -> dict[str, Any]:
    return {"ig_user_id": account_id, "fields": "quota_usage,config"}


def quota_left(data: Any) -> int | None:
    """Posts left in the 24-hour window, or None when Instagram's answer can't be read."""
    body = data.get("response_data", data) if isinstance(data, dict) else data
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    usage = rows[0].get("quota_usage")
    config = rows[0].get("config") if isinstance(rows[0].get("config"), dict) else {}
    total = config.get("quota_total")
    if not isinstance(usage, int) or not isinstance(total, int):
        return None
    return max(total - usage, 0)


def container_id(data: Any) -> str | None:
    body = _unwrap(data)
    value = str(body.get("id") or "") if isinstance(body, dict) else ""
    return value if _NUMERIC.match(value) else None


def publish_step(payload: dict[str, Any], data: Any) -> FollowUp | None:
    created = container_id(data)
    if created is None:
        return None
    return FollowUp(
        PUBLISH_TOOL, {"ig_user_id": payload["account"]["id"], "creation_id": created}
    )


def media_id(data: Any) -> str | None:
    return container_id(data)


def no_url(_data: Any, _media_id: str | None) -> str | None:
    return None  # Instagram's post link needs a shortcode the publish step doesn't return


async def check_photo_unchanged(payload: dict[str, Any]) -> None:
    await media.verify_unchanged(payload["image"], IMAGE_TYPES)
