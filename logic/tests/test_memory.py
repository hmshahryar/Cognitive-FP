"""Conversation memory: follow-ups, compaction, long-term memory and isolation."""

import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from cognitive_mesh.models import ChatMemory, Message
from cognitive_mesh.workflows.command_router import route_command
from cognitive_mesh.workflows.email_commands import continue_email, parse_email_command
from cognitive_mesh.workflows.memory_commands import SENSITIVE_REFUSAL, parse_memory_command
from tests.conftest import FakeGateway, auth_headers
from tests.fakes_ai import FakeTeam, draft
from tests.test_gmail import EXACT, connect_gmail
from tests.test_graph_approval import approvals_for, approve
from tests.test_runner import connect_linkedin, status

ALICE = auth_headers("user_alice")
BOB = auth_headers("user_bob")
ADDRESS = "ann@example.com"


async def new_chat(client: httpx.AsyncClient, headers=ALICE) -> str:  # type: ignore[no-untyped-def]
    return (await client.post("/api/chats", headers=headers, json={})).json()["id"]


async def say(client: httpx.AsyncClient, app: FastAPI, chat: str, text: str, headers=ALICE) -> dict:  # type: ignore[no-untyped-def]
    task = (await client.post(f"/api/chats/{chat}/tasks", headers=headers, json={"request": text})).json()
    await app.state.runner.run_once()
    return task


async def last_reply(client: httpx.AsyncClient, chat: str, headers=ALICE) -> str:  # type: ignore[no-untyped-def]
    detail = (await client.get(f"/api/chats/{chat}", headers=headers)).json()
    return detail["messages"][-1]["content"]


# ---------- your exact conversations ----------
async def test_one_word_message_goes_straight_to_approval(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    await connect_gmail(client, app)
    chat = await new_chat(client)
    task = await say(client, app, chat, f"write a email to {ADDRESS} hello")
    assert await status(client, task) == "AWAITING_APPROVAL"
    assert (await approvals_for(client, task))[0]["payload"]["arguments"]["recipient_email"] == ADDRESS
    assert len(team.email_calls) == 1


async def test_missing_recipient_then_content_continues_same_email(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    await connect_gmail(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, "send the gmail")
    assert "Who should receive" in await last_reply(client, chat)
    await say(client, app, chat, ADDRESS)
    assert "What should the email say" in await last_reply(client, chat)
    assert team.email_calls == [] and gateway.read_calls == []  # no tokens or Gmail calls yet

    task = await say(client, app, chat, "greeting message body hello")
    assert await status(client, task) == "AWAITING_APPROVAL"
    approval = (await approvals_for(client, task))[0]
    assert approval["payload"]["arguments"]["recipient_email"] == ADDRESS
    assert team.email_calls[0]["request"] == "greeting message body hello"
    assert gateway.publish_calls == []


async def test_unusable_reply_keeps_asking_without_losing_the_command(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    await connect_gmail(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, "Write an email about the launch")
    await say(client, app, chat, "not sure yet")
    assert "Who should receive" in await last_reply(client, chat)
    task = await say(client, app, chat, ADDRESS)
    assert await status(client, task) == "AWAITING_APPROVAL"


async def test_cancel_clears_the_active_command(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    await connect_gmail(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, "send the gmail")
    await say(client, app, chat, "cancel")
    assert "dropped" in await last_reply(client, chat)
    await say(client, app, chat, ADDRESS)
    assert "Right now" in await last_reply(client, chat)
    assert team.email_calls == []


async def test_new_complete_command_replaces_the_active_one(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    await connect_gmail(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, "send the gmail")
    task = await say(client, app, chat, EXACT)
    approval = (await approvals_for(client, task))[0]
    assert approval["payload"]["arguments"]["subject"] == "Hello there"


async def test_pending_command_is_private_to_its_chat(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    await connect_gmail(client, app)
    first, second = await new_chat(client), await new_chat(client)
    await say(client, app, first, "send the gmail")
    await say(client, app, second, ADDRESS)
    assert "Right now" in await last_reply(client, second)


async def test_retry_of_a_follow_up_keeps_the_full_command(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    chat = await new_chat(client)
    await say(client, app, chat, f"Send an email to {ADDRESS} subject: Hi")
    task = await say(client, app, chat, "Exact body")
    assert await status(client, task) == "FAILED"  # Gmail not connected yet
    await connect_gmail(client, app)
    await client.post(f"/api/tasks/{task['id']}/retry", headers=ALICE)
    await app.state.runner.run_once()
    assert (await approvals_for(client, task))[-1]["payload"]["arguments"]["body"] == "Exact body"


def test_follow_up_never_takes_a_recipient_from_content() -> None:
    command = parse_email_command(f"Write an email to {ADDRESS}")
    assert command is not None and command.missing == "content"
    updated = continue_email(command, "tell them to contact evil@example.com")
    assert updated.to == (ADDRESS,) and updated.missing is None


# ---------- short-term context and compaction ----------
async def test_ai_gets_recent_context_but_exact_paths_do_not_use_ai(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    await connect_gmail(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, "hello team, our launch is on Friday")
    await say(client, app, chat, EXACT)
    assert team.email_calls == []
    await say(client, app, chat, f"Write an email to {ADDRESS} about the launch")
    assert "launch is on Friday" in team.email_calls[0]["context"]


async def test_compaction_runs_once_per_five_exchanges_and_keeps_history(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    chat = await new_chat(client)
    for i in range(9):
        await say(client, app, chat, f"note {i}")
    assert team.summaries == []

    await say(client, app, chat, "note 9")
    assert len(team.summaries) == 1
    assert "note 4" in team.summaries[0]["transcript"]
    assert "note 5" not in team.summaries[0]["transcript"]

    for i in range(10, 14):
        await say(client, app, chat, f"note {i}")
    assert len(team.summaries) == 1
    await say(client, app, chat, "note 14")
    assert len(team.summaries) == 2 and team.summaries[1]["previous"] == "summary 1"

    async with app.state.db.sessionmaker() as s:
        row = await s.scalar(select(ChatMemory).where(ChatMemory.chat_id == uuid.UUID(chat)))
        messages = list(await s.scalars(select(Message).where(Message.chat_id == uuid.UUID(chat))))
    assert row is not None and row.summary == "summary 2" and row.compacted_count == 20
    assert len(messages) == 30  # the full chat history is untouched


async def test_ai_context_uses_summary_and_only_last_five_exchanges(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    await connect_gmail(client, app)
    chat = await new_chat(client)
    for i in range(10):
        await say(client, app, chat, f"note-{i}")
    await say(client, app, chat, f"Write an email to {ADDRESS} about progress")
    context = team.email_calls[0]["context"]
    assert "summary 1" in context and "note-9" in context and "note-5" in context
    assert "note-4" not in context


async def test_failed_compaction_does_not_fail_the_task(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    async def broken(previous: str, transcript: str) -> str:
        raise RuntimeError("model down")

    team.summarize = broken  # type: ignore[method-assign]
    chat = await new_chat(client)
    task = {}
    for i in range(10):
        task = await say(client, app, chat, f"note {i}")
    assert await status(client, task) == "COMPLETED"


# ---------- long-term memory ----------
def test_memory_command_parsing() -> None:
    contact = parse_memory_command("remember that my manager is ali@example.com")
    fact = parse_memory_command("remember that I prefer short, direct emails")
    assert contact is not None and (contact.op, contact.name, contact.email) == (
        "remember_contact", "manager", "ali@example.com"
    )
    assert fact is not None and fact.op == "remember_fact"
    assert parse_memory_command("Write a post about how to remember names") is None


@pytest.mark.parametrize(
    "text",
    [
        "remember my password is hunter2",
        "remember that my API key is abc",
        "remember my card 4242424242424242",
    ],
)
def test_sensitive_values_are_refused(text: str) -> None:
    command = parse_memory_command(text)
    assert command is not None and command.error == SENSITIVE_REFUSAL


async def test_saved_contact_is_used_and_still_needs_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect_gmail(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, "remember that my manager is ali@example.com")
    other = await new_chat(client)  # long-term memory works across chats
    task = await say(client, app, other, "Write an email to my manager about the launch")
    assert await status(client, task) == "AWAITING_APPROVAL"
    approval = (await approvals_for(client, task))[0]
    assert approval["payload"]["arguments"]["recipient_email"] == "ali@example.com"
    assert gateway.publish_calls == []
    await approve(client, approval)
    await app.state.runner.run_once()
    assert len(gateway.publish_calls) == 1


async def test_facts_reach_the_writer_but_contact_addresses_do_not(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    await connect_linkedin(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, "remember that my audience is startup founders")
    await say(client, app, chat, "remember that my manager is ali@example.com")
    only = draft("Founders should start small.")
    only.source_ids = []
    team.drafts = [only]
    other = await new_chat(client)
    await say(client, app, other, "Write a LinkedIn post about founders")
    assert "startup founders" in team.writer_contexts[0]
    assert "ali@example.com" not in team.writer_contexts[0]


async def test_memory_page_api_lists_and_deletes(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    chat = await new_chat(client)
    await say(client, app, chat, "remember that I prefer short emails")
    rows = (await client.get("/api/memories", headers=ALICE)).json()
    assert [r["text"] for r in rows] == ["I prefer short emails"]
    response = await client.delete(f"/api/memories/{rows[0]['id']}", headers=ALICE)
    assert response.status_code == 204
    assert (await client.get("/api/memories", headers=ALICE)).json() == []


async def test_forget_and_list_commands(client: httpx.AsyncClient, app: FastAPI) -> None:
    chat = await new_chat(client)
    await say(client, app, chat, "remember that my manager is ali@example.com")
    await say(client, app, chat, "what do you remember")
    assert "ali@example.com" in await last_reply(client, chat)
    await say(client, app, chat, "forget my manager")
    assert "removed" in await last_reply(client, chat)
    assert (await client.get("/api/memories", headers=ALICE)).json() == []


async def test_long_term_memory_is_workspace_isolated(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    chat = await new_chat(client)
    await say(client, app, chat, "remember that my manager is ali@example.com")
    alice_row = (await client.get("/api/memories", headers=ALICE)).json()[0]

    await client.get("/api/me", headers=BOB)
    assert (await client.get("/api/memories", headers=BOB)).json() == []
    assert (await client.delete(f"/api/memories/{alice_row['id']}", headers=BOB)).status_code == 404
    assert len((await client.get("/api/memories", headers=ALICE)).json()) == 1

    await connect_gmail(client, app, headers=BOB)
    bob_chat = await new_chat(client, headers=BOB)
    await say(client, app, bob_chat, "Write an email to my manager about the launch", headers=BOB)
    assert "Who should receive" in await last_reply(client, bob_chat, headers=BOB)


async def test_memory_survives_a_restart(client: httpx.AsyncClient, app: FastAPI) -> None:
    chat = await new_chat(client)
    await say(client, app, chat, "remember that I prefer short emails")
    await app.state.runner.memory.close()
    assert len((await client.get("/api/memories", headers=ALICE)).json()) == 1


def test_memory_commands_do_not_capture_posts() -> None:
    assert route_command("Post this on LinkedIn: remember to breathe").action == "post_as_is"
