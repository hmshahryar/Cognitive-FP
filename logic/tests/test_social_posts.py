"""Facebook Page and Instagram posts: exact text, one exact image, one approval, at most once."""

import uuid

import httpx
import pytest
from fastapi import FastAPI

from cognitive_mesh.integrations import facebook, instagram, media
from cognitive_mesh.integrations.facebook import InvalidSocialActionError
from cognitive_mesh.integrations.gateway import ActionRejectedError
from cognitive_mesh.models import ConnectionStatus, IntegrationConnection
from cognitive_mesh.workflows.command_router import route_command
from cognitive_mesh.workflows.social_commands import parse_social_command
from tests.conftest import FakeGateway, auth_headers
from tests.fakes_ai import FakeTeam
from tests.test_graph_approval import approvals_for, approve
from tests.test_runner import status

ALICE = auth_headers("user_alice")
PAGE = {"id": "1234567890", "name": "Cognitive Mesh Services", "tasks": ["CREATE_CONTENT", "MANAGE"]}
IMAGE_URL = "https://cdn.example.com/banner.jpg"
FB_SCOPES = ["pages_manage_posts", "pages_read_engagement", "pages_show_list"]
IG_SCOPES = ["instagram_business_basic", "instagram_business_content_publish"]


def jpeg(width: int, height: int, extra: bytes = b"") -> bytes:
    sof = b"\xff\xc0\x00\x11\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big")
    return b"\xff\xd8" + sof + b"\x03" + b"\x00" * 9 + extra + b"\xff\xd9"


@pytest.fixture
def images(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """Serve image links from memory, through the real type, size and fingerprint checks."""
    served: dict[str, bytes] = {IMAGE_URL: jpeg(1080, 1080)}

    async def fake_fetch(url: str, *, allowed_types: frozenset[str], max_bytes: int):  # type: ignore[no-untyped-def]
        if url not in served:
            raise media.ImageCheckError("I couldn't download the image from that link")
        data = served[url]
        content_type = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
        if content_type not in allowed_types:
            raise media.ImageCheckError("The link must be a direct JPEG image file")
        width, height = media._dimensions(data, content_type)
        import hashlib

        return media.FetchedImage(
            url, hashlib.sha256(data).hexdigest(), content_type, len(data), width, height
        )

    monkeypatch.setattr(media, "fetch_image", fake_fetch)
    return served


async def connect(client, app, toolkit: str, scopes: list[str]) -> None:  # type: ignore[no-untyped-def]
    me = (await client.get("/api/me", headers=ALICE)).json()
    async with app.state.db.sessionmaker() as s:
        s.add(
            IntegrationConnection(
                workspace_id=uuid.UUID(me["workspace"]["id"]),
                user_id=uuid.UUID(me["user"]["id"]),
                toolkit_slug=toolkit,
                composio_account_id=f"ca_{toolkit}_{uuid.uuid4().hex[:8]}",
                status=ConnectionStatus.ACTIVE,
                granted_scopes=scopes,
            )
        )
        await s.commit()


async def say(client: httpx.AsyncClient, app: FastAPI, text: str) -> tuple[dict, str]:
    chat = (await client.post("/api/chats", headers=ALICE, json={})).json()["id"]
    task = (
        await client.post(f"/api/chats/{chat}/tasks", headers=ALICE, json={"request": text})
    ).json()
    await app.state.runner.run_once()
    messages = (await client.get(f"/api/chats/{chat}", headers=ALICE)).json()["messages"]
    return task, messages[-1]["content"]


# ---------- understanding commands ----------
@pytest.mark.parametrize(
    ("text", "platform", "body", "image"),
    [
        ("Post on Facebook: We're open on Saturday", "facebook", "We're open on Saturday", None),
        (f"Post on Facebook with image {IMAGE_URL}: Sale today", "facebook", "Sale today", IMAGE_URL),
        (f"Post on Instagram with image: {IMAGE_URL}: New look", "instagram", "New look", IMAGE_URL),
        (f"share this on insta, photo {IMAGE_URL}", "instagram", None, IMAGE_URL),
    ],
)
def test_post_commands_are_understood(text: str, platform: str, body: str | None, image: str | None) -> None:
    command = parse_social_command(text)
    assert command is not None and command.platform == platform
    assert command.text == body and command.image_url == image


@pytest.mark.parametrize(
    ("text", "caption"),
    [
        (  # structured message with labelled lines
            (
                "Post the following content on Instagram using the provided image.\n\n"
                f"**Image URL:** {IMAGE_URL}\n\n**Caption:**\nTesting Cognitive Mesh 🚀\n\nSecond line"
            ),
            "Testing Cognitive Mesh 🚀\n\nSecond line",
        ),
        (  # link then text, no colon, running onto a second line
            f"post the post in instagram {IMAGE_URL} and We are about to launch.\n\nGet ready",
            "We are about to launch.\n\nGet ready",
        ),
        (f"post the post in instagram {IMAGE_URL}:  We are about to launch", "We are about to launch"),
    ],
)
def test_natural_image_phrasings_are_understood(text: str, caption: str) -> None:
    command = parse_social_command(text)
    assert command is not None and command.platform == "instagram"
    assert command.image_url == IMAGE_URL and command.text == caption and command.missing is None


def test_a_link_inside_facebook_text_stays_text() -> None:
    command = parse_social_command("Post on Facebook: Read more at https://example.com/blog")
    assert command is not None and command.image_url is None
    assert command.text == "Read more at https://example.com/blog"


def test_page_name_is_read_from_the_instruction() -> None:
    command = parse_social_command("Post on my Facebook page Cognitive Mesh Services: Hello")
    assert command is not None and command.page == "Cognitive Mesh Services"


def test_writing_requests_get_guidance_without_ai() -> None:
    command = parse_social_command("Write a Facebook post about our launch")
    assert command is not None and command.kind == "help" and command.error


@pytest.mark.parametrize(
    "text",
    [
        "Post on LinkedIn: we are now on Facebook and Instagram",
        "Post on Facebook and Instagram: hello",
        "send an email to bob@example.com saying see you on facebook",
    ],
)
def test_other_commands_never_become_one_social_post(text: str) -> None:
    assert route_command(text).action != "social"


def test_instagram_needs_an_image() -> None:
    command = parse_social_command("Post on Instagram: Hello")
    assert command is not None and command.missing == "image"


# ---------- Facebook ----------
async def test_facebook_text_post_needs_approval_and_posts_once(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    await connect(client, app, "facebook", FB_SCOPES)
    gateway.facebook_pages = [PAGE]
    gateway.publish_response = {"id": "1234567890_555"}
    task, _reply = await say(client, app, "Post on Facebook: We're open on Saturday")
    a = (await approvals_for(client, task))[0]
    assert a["action_type"] == facebook.POST_ACTION and gateway.publish_calls == []
    assert team.manager_calls == []
    assert a["payload"]["arguments"] == {
        "page_id": "1234567890", "message": "We're open on Saturday", "published": True,
    }
    await approve(client, a)
    await app.state.runner.run_once()
    assert await status(client, task) == "COMPLETED"
    assert [c["tool"] for c in gateway.publish_calls] == [facebook.POST_TOOL]


async def test_facebook_photo_is_bound_to_the_image_fingerprint(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, images: dict[str, bytes]
) -> None:
    await connect(client, app, "facebook", FB_SCOPES)
    gateway.facebook_pages = [PAGE]
    task, _reply = await say(client, app, f"Post on Facebook with image {IMAGE_URL}: Sale today")
    a = (await approvals_for(client, task))[0]
    assert a["action_type"] == facebook.PHOTO_ACTION
    assert a["payload"]["arguments"]["url"] == IMAGE_URL
    images[IMAGE_URL] = jpeg(1080, 1080, b"changed")  # swapped after approval
    await approve(client, a)
    await app.state.runner.run_once()
    assert gateway.publish_calls == []  # nothing sent
    assert await status(client, task) == "FAILED"


async def test_several_pages_are_never_guessed(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app, "facebook", FB_SCOPES)
    gateway.facebook_pages = [PAGE, {**PAGE, "id": "999", "name": "Other Page"}]
    task, reply = await say(client, app, "Post on Facebook: Hello")
    assert "Which Page" in reply and await approvals_for(client, task) == []
    task, _reply = await say(client, app, "Post on my Facebook page Other Page: Hello")
    assert (await approvals_for(client, task))[0]["payload"]["page"]["id"] == "999"


async def test_pages_without_posting_rights_are_not_offered(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app, "facebook", FB_SCOPES)
    gateway.facebook_pages = [{**PAGE, "tasks": ["ANALYZE"]}]
    task, reply = await say(client, app, "Post on Facebook: Hello")
    assert "can't see a Facebook Page" in reply and await approvals_for(client, task) == []


async def test_repeat_post_is_not_prepared_twice(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app, "facebook", FB_SCOPES)
    gateway.facebook_pages = [PAGE]
    gateway.publish_response = {"id": "1234567890_555"}
    task, _reply = await say(client, app, "Post on Facebook: Hello")
    await approve(client, (await approvals_for(client, task))[0])
    await app.state.runner.run_once()
    second, reply = await say(client, app, "Post on Facebook: Hello")
    assert "didn't repeat" in reply and await approvals_for(client, second) == []


async def test_facebook_not_connected_fails_clearly(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    task, _reply = await say(client, app, "Post on Facebook: Hello")
    assert await status(client, task) == "FAILED" and gateway.publish_calls == []


# ---------- Instagram ----------
async def test_instagram_publishes_in_two_steps_under_one_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, images: dict[str, bytes]
) -> None:
    await connect(client, app, "instagram", IG_SCOPES)
    gateway.publish_responses = [{"id": "178900001"}, {"id": "178900002"}]
    task, _reply = await say(client, app, f"Post on Instagram with image {IMAGE_URL}: New look")
    a = (await approvals_for(client, task))[0]
    assert a["action_type"] == instagram.PHOTO_ACTION and gateway.publish_calls == []
    assert a["payload"]["arguments"] == {
        "ig_user_id": "17841400000000001", "image_url": IMAGE_URL, "caption": "New look",
    }
    await approve(client, a)
    await app.state.runner.run_once()
    assert await status(client, task) == "COMPLETED"
    calls = gateway.publish_calls
    assert [c["tool"] for c in calls] == [instagram.CONTAINER_TOOL, instagram.PUBLISH_TOOL]
    assert calls[1]["arguments"] == {"ig_user_id": "17841400000000001", "creation_id": "178900001"}
    pubs = (await client.get(f"/api/tasks/{task['id']}/publications", headers=ALICE)).json()
    assert pubs[0]["status"] == "PUBLISHED" and pubs[0]["external_id"] == "178900002"


async def test_failed_publish_step_is_never_reported_as_posted(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, images: dict[str, bytes]
) -> None:
    await connect(client, app, "instagram", IG_SCOPES)
    gateway.publish_responses = [{"id": "178900001"}, ActionRejectedError("400 media not ready")]
    task, _reply = await say(client, app, f"Post on Instagram with image {IMAGE_URL}: New look")
    await approve(client, (await approvals_for(client, task))[0])
    await app.state.runner.run_once()
    pubs = (await client.get(f"/api/tasks/{task['id']}/publications", headers=ALICE)).json()
    assert pubs[0]["status"] == "FAILED" and pubs[0]["external_id"] is None


async def test_personal_instagram_accounts_are_refused(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, images: dict[str, bytes]
) -> None:
    await connect(client, app, "instagram", IG_SCOPES)
    gateway.instagram_account = {**gateway.instagram_account, "account_type": "PERSONAL"}
    task, reply = await say(client, app, f"Post on Instagram with image {IMAGE_URL}: Hi")
    assert "Business or Creator" in reply and await approvals_for(client, task) == []


async def test_png_and_bad_shapes_are_refused_for_instagram(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, images: dict[str, bytes]
) -> None:
    await connect(client, app, "instagram", IG_SCOPES)
    images[IMAGE_URL] = jpeg(1080, 3000)  # taller than 4:5
    task, reply = await say(client, app, f"Post on Instagram with image {IMAGE_URL}: Hi")
    assert "4:5" in reply and await approvals_for(client, task) == []


async def test_exhausted_quota_stops_before_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, images: dict[str, bytes]
) -> None:
    await connect(client, app, "instagram", IG_SCOPES)
    gateway.instagram_quota = {"data": [{"quota_usage": 100, "config": {"quota_total": 100}}]}
    task, reply = await say(client, app, f"Post on Instagram with image {IMAGE_URL}: Hi")
    assert "limit" in reply and await approvals_for(client, task) == []


async def test_missing_image_is_asked_for_and_the_reply_continues(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, images: dict[str, bytes]
) -> None:
    await connect(client, app, "instagram", IG_SCOPES)
    chat = (await client.post("/api/chats", headers=ALICE, json={})).json()["id"]
    await client.post(f"/api/chats/{chat}/tasks", headers=ALICE, json={"request": "Post on Instagram: Hi"})
    await app.state.runner.run_once()
    task = (
        await client.post(f"/api/chats/{chat}/tasks", headers=ALICE, json={"request": IMAGE_URL})
    ).json()
    await app.state.runner.run_once()
    a = (await approvals_for(client, task))[0]
    assert a["payload"]["caption"] == "Hi" and a["payload"]["image"]["url"] == IMAGE_URL


# ---------- payload rules ----------
def test_tampered_payloads_are_rejected() -> None:
    page = {"id": "1234567890", "name": "P"}
    post = facebook.build_post_payload(page=page, message="Hi")
    with pytest.raises(InvalidSocialActionError):
        facebook.validate_post({**post, "arguments": {**post["arguments"], "page_id": "999"}})
    image = media.FetchedImage(IMAGE_URL, "a" * 64, "image/jpeg", 10, 1080, 1080).as_payload()
    ig = instagram.build_photo_payload(account={"id": "1", "username": "u"}, caption="Hi", image=image)
    with pytest.raises(InvalidSocialActionError):
        instagram.validate_photo({**ig, "arguments": {**ig["arguments"], "image_url": "https://x/y.jpg"}})


def test_jpeg_header_and_dimensions_are_read() -> None:
    assert media._dimensions(jpeg(1200, 628), "image/jpeg") == (1200, 628)
    with pytest.raises(media.ImageCheckError):
        media._dimensions(b"<html>", "image/jpeg")


@pytest.mark.parametrize("url", ["http://example.com/a.jpg", "https://127.0.0.1/a.jpg", "https://10.0.0.5/a.jpg"])
async def test_private_or_plain_http_links_are_never_fetched(url: str) -> None:
    with pytest.raises(media.ImageCheckError):
        await media._check_public(url)
