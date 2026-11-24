"""Canva banners (Free plan): drawn from the user's words, placed in their Canva, posted only
after approval of the exact exported image."""

import hashlib
import io

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from cognitive_mesh.capabilities import AgentRole, RiskLevel, capabilities_for
from cognitive_mesh.integrations import canva, facebook, instagram, media
from cognitive_mesh.integrations.facebook import InvalidSocialActionError
from cognitive_mesh.integrations.gateway import (
    APPROVED_ACTION_TOOLS,
    PRIVATE_DRAFT_TOOLS,
    READ_ONLY_TOOLS,
)
from cognitive_mesh.workflows.social_commands import continue_social, parse_social_command
from tests.conftest import FakeGateway
from tests.fakes_ai import FakeTeam
from tests.test_graph_approval import approvals_for, approve
from tests.test_runner import status
from tests.test_social_posts import FB_SCOPES, IG_SCOPES, PAGE, connect, say

CANVA_SCOPES = [
    "asset:read", "asset:write", "brandtemplate:content:read", "brandtemplate:meta:read",
    "design:content:read", "design:content:write", "design:meta:read", "profile:read",
]
COMMAND = (
    'Make a banner for Instagram: headline "Launching Soon", text "AI employees for SMEs", '
    "caption: Big news coming this week"
)
EXPORT = "https://export-download.canva.com/banner.jpg"


@pytest.fixture
def exported(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """Serve Canva's export link from memory, through the real checks."""
    served = {EXPORT: canva.render_banner(headline="Launching Soon")}

    async def fake_fetch(url: str, *, allowed_types: frozenset[str], max_bytes: int):  # type: ignore[no-untyped-def]
        data = served[url]
        width, height = media._dimensions(data, "image/jpeg")
        return media.FetchedImage(
            url, hashlib.sha256(data).hexdigest(), "image/jpeg", len(data), width, height
        )

    monkeypatch.setattr(media, "fetch_image", fake_fetch)
    monkeypatch.setattr(canva, "POLL_SECONDS", 0)
    return served


# ---------- understanding commands ----------
def test_banner_command_is_understood() -> None:
    command = parse_social_command(COMMAND)
    assert command is not None and command.kind == "banner" and command.platform == "instagram"
    assert command.headline == "Launching Soon" and command.subtext == "AI employees for SMEs"
    assert command.text == "Big news coming this week" and command.missing is None


def test_banner_options_and_missing_headline() -> None:
    command = parse_social_command(
        "Create a banner for Facebook with background https://x.example/bg.jpg and colour #1E3A8A"
    )
    assert command is not None and command.kind == "banner" and command.platform == "facebook"
    assert command.background_url == "https://x.example/bg.jpg" and command.color == "#1E3A8A"
    assert command.missing == "headline"
    assert continue_social(command, '"Grand Opening"').headline == "Grand Opening"


def test_banner_for_two_platforms_is_not_guessed() -> None:
    assert parse_social_command('Make a banner for Facebook and Instagram: headline "Hi"') is None


# ---------- drawing ----------
def test_banner_is_a_square_jpeg_with_the_users_words_only() -> None:
    data = canva.render_banner(headline="Launching Soon", subtext="AI employees for SMEs")
    image = Image.open(io.BytesIO(data))
    assert image.format == "JPEG" and image.size == (1080, 1080)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"headline": ""}, "headline"),
        ({"headline": "x" * 81}, "headline"),
        ({"headline": "Hi", "color": "blue"}, "colour"),
        ({"headline": "Hi", "background": b"not an image"}, "background"),
    ],
)
def test_bad_banner_input_is_refused(kwargs: dict, message: str) -> None:
    with pytest.raises(canva.BannerError, match=message):
        canva.render_banner(**kwargs)


# ---------- the full flow ----------
async def test_instagram_banner_is_made_in_canva_then_waits_for_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam,
    exported: dict[str, bytes],
) -> None:
    await connect(client, app, "instagram", IG_SCOPES)
    await connect(client, app, "canva", CANVA_SCOPES)
    gateway.publish_responses = [{"id": "178900001"}, {"id": "178900002"}]
    task, reply = await say(client, app, COMMAND)
    assert "in your Canva" in reply and team.manager_calls == []
    assert [tool for tool, _args in gateway.draft_calls] == [
        canva.UPLOAD_TOOL, canva.UPLOAD_STATUS_TOOL, canva.DESIGN_TOOL,
        canva.EXPORT_TOOL, canva.EXPORT_STATUS_TOOL,
    ]
    design_args = dict(gateway.draft_calls)[canva.DESIGN_TOOL]
    assert design_args["asset_id"] == "Msd123"
    assert design_args["design_type"] == {"type": "custom", "width": 1080, "height": 1080}
    a = (await approvals_for(client, task))[0]
    assert a["action_type"] == instagram.PHOTO_ACTION and gateway.publish_calls == []
    assert a["payload"]["image"]["url"] == EXPORT
    assert a["payload"]["design"] == {
        "id": "DAF123", "edit_url": "https://www.canva.com/design/DAF123/edit",
    }
    assert a["payload"]["caption"] == "Big news coming this week"
    await approve(client, a)
    await app.state.runner.run_once()
    assert await status(client, task) == "COMPLETED"
    assert gateway.publish_calls[0]["arguments"]["image_url"] == EXPORT


async def test_facebook_banner_posts_the_exported_image(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, exported: dict[str, bytes]
) -> None:
    await connect(client, app, "facebook", FB_SCOPES)
    await connect(client, app, "canva", CANVA_SCOPES)
    gateway.facebook_pages = [PAGE]
    task, _reply = await say(
        client, app, 'Make a banner for Facebook: headline "Open Today", caption: Come visit'
    )
    a = (await approvals_for(client, task))[0]
    assert a["action_type"] == facebook.PHOTO_ACTION
    assert a["payload"]["arguments"]["url"] == EXPORT


async def test_banner_changed_after_approval_is_not_posted(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, exported: dict[str, bytes]
) -> None:
    await connect(client, app, "instagram", IG_SCOPES)
    await connect(client, app, "canva", CANVA_SCOPES)
    task, _reply = await say(client, app, COMMAND)
    exported[EXPORT] = canva.render_banner(headline="Something else")
    await approve(client, (await approvals_for(client, task))[0])
    await app.state.runner.run_once()
    assert gateway.publish_calls == [] and await status(client, task) == "FAILED"


async def test_canva_not_connected_makes_nothing(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app, "instagram", IG_SCOPES)
    task, _reply = await say(client, app, COMMAND)
    assert await status(client, task) == "FAILED"
    assert gateway.draft_calls == [] and gateway.publish_calls == []


async def test_failed_canva_job_stops_before_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, exported: dict[str, bytes]
) -> None:
    await connect(client, app, "instagram", IG_SCOPES)
    await connect(client, app, "canva", CANVA_SCOPES)
    gateway.canva_responses[canva.EXPORT_STATUS_TOOL] = {
        "job": {"id": "ex1", "status": "failed", "error": {"message": "internal_failure"}}
    }
    task, reply = await say(client, app, COMMAND)
    assert "couldn't make the banner" in reply and await approvals_for(client, task) == []


async def test_exhausted_instagram_quota_makes_no_canva_design(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app, "instagram", IG_SCOPES)
    await connect(client, app, "canva", CANVA_SCOPES)
    gateway.instagram_quota = {"data": [{"quota_usage": 100, "config": {"quota_total": 100}}]}
    _task, reply = await say(client, app, COMMAND)
    assert "limit" in reply and gateway.draft_calls == []


# ---------- safety rules ----------
def test_canva_drafts_are_private_and_never_publishing_tools() -> None:
    drafts = {c.tool_slug for c in capabilities_for(AgentRole.DESIGN) if c.risk is RiskLevel.PRIVATE_DRAFT}
    assert drafts == PRIVATE_DRAFT_TOOLS == {
        canva.UPLOAD_TOOL, canva.DESIGN_TOOL, canva.EXPORT_TOOL,
    }
    assert not PRIVATE_DRAFT_TOOLS & (APPROVED_ACTION_TOOLS | READ_ONLY_TOOLS)


def test_tampered_design_record_is_rejected() -> None:
    image = media.FetchedImage(EXPORT, "a" * 64, "image/jpeg", 10, 1080, 1080).as_payload()
    payload = instagram.build_photo_payload(
        account={"id": "1", "username": "u"}, caption="", image=image,
        design={"id": "DAF123", "edit_url": "https://www.canva.com/design/DAF123/edit"},
    )
    with pytest.raises(InvalidSocialActionError):
        instagram.validate_photo({**payload, "design": {"id": "DAF123", "edit_url": "https://evil.example/"}})
