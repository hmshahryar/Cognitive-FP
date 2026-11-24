"""Each command uses only the resources it needs, on the platform the user named."""

import httpx
import pytest
from fastapi import FastAPI

from cognitive_mesh.workflows.command_router import blocking_reply, route_command
from tests.conftest import FakeGateway, auth_headers
from tests.fakes_ai import FakeTeam, draft
from tests.test_runner import command, connect_linkedin, status

ALICE = auth_headers("user_alice")


@pytest.mark.parametrize(
    ("text", "action", "platforms"),
    [
        ("Write a LinkedIn post about AI in small businesses", "write", ("linkedin",)),
        ("Write a post about trends in LinkedIn marketing", "write", ()),
        ("Publish a post about AI on LinkedIn", "write", ("linkedin",)),
        ("Write a LinkedIn post about growing on Instagram", "write", ("linkedin",)),
        ("write post on likned in about agents", "write", ("linkedin",)),
        ("Write a post about research and development for LinkedIn", "write", ("linkedin",)),
        ("Research AI agents and write a LinkedIn post", "research", ("linkedin",)),
        ("Write a LinkedIn post, research it first", "research", ("linkedin",)),
        ("@research agents then write a LinkedIn post", "research", ("linkedin",)),
        ("Write an Instagram caption about coffee for Instagram", "social", ("instagram",)),
        ("Post on LinkedIn and Instagram: hello there", "post_as_is", ("linkedin", "instagram")),
        ("Book me a flight", "unsupported", ()),
    ],
)
def test_routing(text: str, action: str, platforms: tuple[str, ...]) -> None:
    route = route_command(text)
    assert (route.action, route.platforms) == (action, platforms)


@pytest.mark.parametrize(
    ("text", "exact"),
    [
        ("Post this on LinkedIn: Shipping beats planning.", "Shipping beats planning."),
        ("I am giving you a post, post it as it is on LinkedIn:\nLine one\n\n  Line two", "Line one\n\n  Line two"),
        ('Post "Hello, LinkedIn world" on LinkedIn', "Hello, LinkedIn world"),
        ('Publish this to LinkedIn: "Keep my: colon"', "Keep my: colon"),
        ("Post this on LinkedIn: I wrote this about Instagram, research and 73% growth!", "I wrote this about Instagram, research and 73% growth!"),
    ],
)
def test_user_text_is_extracted_exactly(text: str, exact: str) -> None:
    route = route_command(text)
    assert route.action == "post_as_is" and route.text == exact and route.platforms == ("linkedin",)


def test_real_user_wording_for_as_is_keeps_the_text_exactly() -> None:
    body = "Genie 4 is a world model: you can steer it in real time. Research labs noticed."
    route = route_command(f"write the post as is and post in linked in here is the post {body}")
    assert route.action == "post_as_is" and route.text == body and route.platforms == ("linkedin",)


def test_extra_spaces_in_as_is_wording_still_keep_the_text() -> None:
    route = route_command("write the post as it  is and post in linked in here is the post Exact words.")
    assert route.action == "post_as_is" and route.text == "Exact words."


def test_length_instruction_is_not_mistaken_for_as_is() -> None:
    route = route_command("Write a LinkedIn post exactly 100 words: remote work")
    assert route.action == "write"


def test_platform_named_only_inside_the_post_is_not_a_destination() -> None:
    route = route_command("Post this: I love sharing on Instagram")
    assert route.action == "post_as_is" and route.platforms == ()
    assert "Where should I post" in (blocking_reply(route) or "")


def test_blocking_replies() -> None:
    assert "need an image" in (blocking_reply(route_command("Post this on Instagram: hi")) or "")
    assert "exact text" in (blocking_reply(route_command("Write a Facebook post about AI")) or "")
    assert "one platform" in (blocking_reply(route_command("Post on LinkedIn and Instagram: hi")) or "")
    assert "exact text" in (blocking_reply(route_command("Post this on LinkedIn")) or "")
    too_long = "Post this on LinkedIn: " + "x" * 3001
    assert "won't trim" in (blocking_reply(route_command(too_long)) or "")


async def pending(client: httpx.AsyncClient, task: dict) -> dict:
    rows = (await client.get("/api/approvals", headers=ALICE, params={"task_id": task["id"]})).json()
    assert len(rows) == 1
    return rows[0]


async def test_post_as_is_uses_no_ai_or_search_and_still_needs_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    await connect_linkedin(client, app)
    exact = "Line one.\n\n  Line two, up 73%!"
    task = await command(client, f"Post this on LinkedIn: {exact}")
    await app.state.runner.run_once()

    assert await status(client, task) == "AWAITING_APPROVAL"
    assert (await pending(client, task))["payload"]["commentary"] == exact
    assert team.writer_feedback == [] and team.review_calls == 0 and team.profiles == []
    assert [slug for _user, slug in gateway.read_calls] == ["LINKEDIN_GET_MY_INFO"]
    row = (await client.get(f"/api/tasks/{task['id']}/drafts", headers=ALICE)).json()[0]
    assert (row["provider"], row["model"], row["research"]["mode"]) == ("user", "none", "post_as_is")


async def test_write_skips_search_but_writes_and_reviews(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    team.drafts = [draft("Most pilots stall at the handoff.", sources=[])]
    team.drafts[0].source_ids = []
    await connect_linkedin(client, app)
    task = await command(client, "Write a LinkedIn post about AI pilots")
    await app.state.runner.run_once()

    assert await status(client, task) == "AWAITING_APPROVAL"
    assert team.profiles == [] and team.review_calls == 1  # no research ran
    assert not any(slug.startswith("COMPOSIO_SEARCH") for _u, slug in gateway.read_calls)
    row = (await client.get(f"/api/tasks/{task['id']}/drafts", headers=ALICE)).json()[0]
    assert row["research"]["mode"] == "write" and row["sources"] == []


async def test_write_without_sources_cannot_state_invented_figures(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    bad = draft("Adoption rose 73%.")
    bad.source_ids = []
    team.drafts = [bad]
    await connect_linkedin(client, app)
    task = await command(client, "Write a LinkedIn post about AI adoption")
    await app.state.runner.run_once()
    assert await status(client, task) == "FAILED"
    assert (await client.get("/api/approvals", headers=ALICE)).json() == []


async def test_research_runs_only_when_requested(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    await connect_linkedin(client, app)
    await command(client)
    await app.state.runner.run_once()
    assert len(team.profiles) == 1


async def test_missing_platform_asks_first_then_reply_continues_same_command(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    await connect_linkedin(client, app)
    asked = await command(client, "Post this: Exactly my words.")
    await app.state.runner.run_once()
    assert await status(client, asked) == "COMPLETED"
    chat = (await client.get(f"/api/chats/{asked['chat_id']}", headers=ALICE)).json()
    assert "Where should I post" in chat["messages"][-1]["content"]
    assert gateway.read_calls == []  # asked before using anything

    reply = (
        await client.post(f"/api/chats/{asked['chat_id']}/tasks", headers=ALICE, json={"request": "LinkedIn"})
    ).json()
    await app.state.runner.run_once()
    assert await status(client, reply) == "AWAITING_APPROVAL"
    assert (await pending(client, reply))["payload"]["commentary"] == "Exactly my words."


async def test_unavailable_platform_stops_without_ai(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    task = await command(client, "Write an Instagram post about coffee")
    await app.state.runner.run_once()
    assert await status(client, task) == "COMPLETED"
    assert team.writer_feedback == [] and gateway.read_calls == []


async def test_unclear_command_uses_the_model_once_to_classify(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    calls = {"n": 0}
    original = team.analyze

    async def counted(text):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return await original(text)

    team.analyze = counted  # type: ignore[method-assign]
    only = draft("Consistency compounds over a career.")
    only.source_ids = []
    team.drafts = [only]
    await connect_linkedin(client, app)
    task = await command(client, "Need a nice LinkedIn update for Friday")
    await app.state.runner.run_once()
    assert calls["n"] == 1 and await status(client, task) == "AWAITING_APPROVAL"


async def test_cancel_during_writing_stops_before_the_review_call(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    only = draft("Most pilots stall at the handoff.")
    only.source_ids = []
    team.drafts = [only]
    await connect_linkedin(client, app)
    task = await command(client, "Write a LinkedIn post about AI pilots")
    original = team.write

    async def write_then_cancel(*args, **kwargs):  # type: ignore[no-untyped-def]
        out = await original(*args, **kwargs)
        await client.post(f"/api/tasks/{task['id']}/cancel", headers=ALICE)
        return out

    team.write = write_then_cancel  # type: ignore[method-assign]
    await app.state.runner.run_once()
    assert await status(client, task) == "CANCELLED" and team.review_calls == 0
    assert (await client.get(f"/api/tasks/{task['id']}/drafts", headers=ALICE)).json() == []


async def test_request_changes_on_user_text_revises_that_text(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    await connect_linkedin(client, app)
    task = await command(client, "Post this on LinkedIn: My original words.")
    await app.state.runner.run_once()
    approval = await pending(client, task)
    revised = draft("My sharper words.")
    revised.source_ids = []
    team.drafts = [revised]
    await client.post(
        f"/api/approvals/{approval['id']}/request-changes", headers=ALICE, json={"note": "Sharper"}
    )
    await app.state.runner.run_once()
    assert team.previous_texts == ["My original words."]
    rows = (await client.get("/api/approvals", headers=ALICE, params={"task_id": task["id"], "status": "PENDING"})).json()
    assert rows[0]["content_version"] == 2 and rows[0]["payload"]["commentary"].startswith("My sharper")
