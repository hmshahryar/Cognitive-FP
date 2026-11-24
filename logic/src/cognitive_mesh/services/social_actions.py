"""Prepare one exact Facebook Page or Instagram post. Uses no AI and publishes nothing."""

from dataclasses import dataclass
from typing import Any

from cognitive_mesh.integrations import canva, facebook, instagram, media
from cognitive_mesh.integrations.facebook import InvalidSocialActionError
from cognitive_mesh.integrations.gateway import (
    IntegrationGateway,
    IntegrationUnavailableError,
    ToolExecutionError,
)
from cognitive_mesh.workflows.social_commands import SocialPost


class SocialStop(Exception):
    """Stop safely and tell the user why."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PreparedPost:
    action_type: str
    payload: dict[str, Any]
    text: str


async def _read(
    gateway: IntegrationGateway, user_id: str, toolkit: str, tool: str, args: dict[str, Any]
) -> Any:
    try:
        return await gateway.execute_read_only(
            user_id=user_id, toolkit_slug=toolkit, tool_slug=tool, arguments=args
        )
    except (IntegrationUnavailableError, ToolExecutionError) as exc:
        name = "Facebook" if toolkit == facebook.TOOLKIT else "Instagram"
        raise SocialStop(f"I couldn't reach {name}, so nothing was posted. Please try again.") from exc


async def _image(url: str, allowed: frozenset[str], max_bytes: int) -> dict[str, Any]:
    try:
        fetched = await media.fetch_image(url, allowed_types=allowed, max_bytes=max_bytes)
    except media.ImageCheckError as exc:
        raise SocialStop(f"I can't use that image: {exc}. Nothing was posted.") from exc
    return fetched.as_payload()


async def _facebook_page(
    gateway: IntegrationGateway, user_id: str, wanted: str | None
) -> dict[str, str]:
    data = await _read(
        gateway, user_id, facebook.TOOLKIT, facebook.PAGES_TOOL, facebook.pages_arguments()
    )
    try:
        pages, more = facebook.pages_from(data)
    except InvalidSocialActionError as exc:
        raise SocialStop(f"{exc}, so I stopped.") from exc
    if wanted:
        exact = [p for p in pages if p["name"].casefold() == wanted.casefold()]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise SocialStop(f"More than one of your Pages is called {wanted}, so I stopped.")
        names = ", ".join(p["name"] for p in pages) or "none"
        raise SocialStop(f"I can't post to a Page called {wanted}. Pages I can post to: {names}.")
    if not pages:
        raise SocialStop(
            "I can't see a Facebook Page you can post to. Reconnect Facebook and select your "
            "Page, or create a Page first. Facebook doesn't allow posting to personal profiles."
        )
    if len(pages) > 1 or more:
        names = ", ".join(p["name"] for p in pages)
        raise SocialStop(
            f"Which Page should I post to: {names}? Send the command again with "
            "'on my Facebook page <name>'."
        )
    return pages[0]


BACKGROUND_TYPES = frozenset({"image/jpeg", "image/png"})
MAX_BACKGROUND_BYTES = 10 * 1024 * 1024


async def _make_banner(
    gateway: IntegrationGateway, user_id: str, command: SocialPost
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Draw the banner, put it in the user's Canva as a private design, and fingerprint the
    exported JPEG that will be posted. Returns (image record, design)."""
    background = None
    if command.background_url:
        try:
            background = await media.fetch_bytes(
                command.background_url, allowed_types=BACKGROUND_TYPES,
                max_bytes=MAX_BACKGROUND_BYTES,
            )
        except media.ImageCheckError as exc:
            raise SocialStop(f"I can't use that background image: {exc}.") from exc
    try:
        jpeg = canva.render_banner(
            headline=command.headline or "",
            subtext=command.subtext or "",
            color=command.color or canva.DEFAULT_COLOR,
            background=background,
        )
        made: canva.CanvaBanner = await gateway.run_private_draft(
            user_id=user_id,
            toolkit_slug=canva.TOOLKIT,
            work=lambda session: canva.make_in_canva(
                session, jpeg, title=f"Cognitive Mesh banner: {command.headline}"
            ),
        )
    except canva.BannerError as exc:
        raise SocialStop(f"I couldn't make the banner: {exc}. Nothing was posted.") from exc
    except (IntegrationUnavailableError, ToolExecutionError) as exc:
        raise SocialStop("I couldn't reach Canva, so no banner was made. Please try again.") from exc
    image = await _image(made.export_url, frozenset({"image/jpeg"}), instagram.MAX_IMAGE_BYTES)
    return image, {"id": made.design_id, "edit_url": made.edit_url}


async def prepare_post(
    gateway: IntegrationGateway, user_id: str, command: SocialPost
) -> PreparedPost:
    text = command.text or ""
    try:
        if command.platform == facebook.TOOLKIT:
            page = await _facebook_page(gateway, user_id, command.page)
            if command.kind == "banner":
                image, design = await _make_banner(gateway, user_id, command)
                payload = facebook.build_photo_payload(
                    page=page, message=text, image=image, design=design
                )
                return PreparedPost(facebook.PHOTO_ACTION, payload, text)
            if command.image_url:
                image = await _image(
                    command.image_url, facebook.IMAGE_TYPES, facebook.MAX_IMAGE_BYTES
                )
                payload = facebook.build_photo_payload(page=page, message=text, image=image)
                return PreparedPost(facebook.PHOTO_ACTION, payload, text)
            payload = facebook.build_post_payload(page=page, message=text)
            return PreparedPost(facebook.POST_ACTION, payload, text)

        data = await _read(
            gateway, user_id, instagram.TOOLKIT, instagram.USER_TOOL, instagram.user_arguments()
        )
        account = instagram.account_from(data)
        left = instagram.quota_left(
            await _read(
                gateway, user_id, instagram.TOOLKIT, instagram.LIMIT_TOOL,
                instagram.limit_arguments(account["id"]),
            )
        )
        if left == 0:
            raise SocialStop(
                "Instagram's limit of API posts for the last 24 hours is reached. "
                "Try again later. Nothing was posted."
            )
        design = None
        if command.kind == "banner":  # checked after the quota, so no wasted Canva design
            image, design = await _make_banner(gateway, user_id, command)
        else:
            assert command.image_url is not None
            image = await _image(
                command.image_url, instagram.IMAGE_TYPES, instagram.MAX_IMAGE_BYTES
            )
        instagram.check_image_shape(image["width"], image["height"])
        payload = instagram.build_photo_payload(
            account=account, caption=text, image=image, design=design
        )
        return PreparedPost(instagram.PHOTO_ACTION, payload, text)
    except InvalidSocialActionError as exc:
        raise SocialStop(f"I can't prepare that post: {exc}. Nothing was posted.") from exc
