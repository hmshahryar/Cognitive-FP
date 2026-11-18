import asyncio
import uuid

import httpx
import pytest

from tests.conftest import auth_headers

ALICE = auth_headers("user_alice")
BOB = auth_headers("user_bob")


async def _chat(client: httpx.AsyncClient, headers: dict, title: str = "Work") -> dict:
    res = await client.post("/api/chats", headers=headers, json={"title": title})
    assert res.status_code == 201, res.text
    return res.json()


async def _task(client: httpx.AsyncClient, headers: dict, chat_id: str, text="Post on LinkedIn"):  # type: ignore[no-untyped-def]
    res = await client.post(f"/api/chats/{chat_id}/tasks", headers=headers, json={"request": text})
    assert res.status_code == 201, res.text
    return res.json()


# ---------- chats & messages ----------
async def test_create_and_list_chats(client: httpx.AsyncClient) -> None:
    await _chat(client, ALICE, "First")
    await _chat(client, ALICE, "Second")
    res = await client.get("/api/chats", headers=ALICE)
    assert [c["title"] for c in res.json()] == ["Second", "First"]  # most recent first


async def test_blank_title_gets_default(client: httpx.AsyncClient) -> None:
    res = await client.post("/api/chats", headers=ALICE, json={})
    assert res.json()["title"] == "New chat"


async def test_conversation_history_is_ordered(client: httpx.AsyncClient) -> None:
    chat = await _chat(client, ALICE)
    for text in ("one", "two", "three"):
        res = await client.post(
            f"/api/chats/{chat['id']}/messages", headers=ALICE, json={"content": text}
        )
        assert res.status_code == 201 and res.json()["role"] == "user"
    history = (await client.get(f"/api/chats/{chat['id']}", headers=ALICE)).json()
    assert [m["content"] for m in history["messages"]] == ["one", "two", "three"]


async def test_users_cannot_post_assistant_messages(client: httpx.AsyncClient) -> None:
    chat = await _chat(client, ALICE)
    res = await client.post(
        f"/api/chats/{chat['id']}/messages",
        headers=ALICE,
        json={"content": "I am the AI", "role": "assistant"},
    )
    assert res.json()["role"] == "user"  # role from the body is ignored


async def test_empty_message_rejected(client: httpx.AsyncClient) -> None:
    chat = await _chat(client, ALICE)
    res = await client.post(f"/api/chats/{chat['id']}/messages", headers=ALICE, json={"content": ""})
    assert res.status_code == 422


# ---------- tasks & executions ----------
async def test_create_task_stores_message_task_and_execution(client: httpx.AsyncClient) -> None:
    chat = await _chat(client, ALICE)
    task = await _task(client, ALICE, chat["id"])
    assert task["status"] == "QUEUED"

    detail = (await client.get(f"/api/chats/{chat['id']}", headers=ALICE)).json()
    assert detail["messages"][-1]["task_id"] == task["id"]
    assert [t["id"] for t in detail["tasks"]] == [task["id"]]

    execs = (await client.get(f"/api/tasks/{task['id']}/executions", headers=ALICE)).json()
    assert len(execs) == 1 and execs[0]["attempt"] == 1
    assert execs[0]["thread_id"].startswith("exec-")
    assert execs[0]["id"] == task["current_execution_id"]

    events = (await client.get(f"/api/tasks/{task['id']}/events", headers=ALICE)).json()
    assert [e["event"] for e in events] == ["task.created"]


async def test_chat_can_hold_many_tasks(client: httpx.AsyncClient) -> None:
    chat = await _chat(client, ALICE)
    for i in range(3):
        await _task(client, ALICE, chat["id"], f"task {i}")
    res = await client.get("/api/tasks", headers=ALICE, params={"chat_id": chat["id"]})
    assert len(res.json()) == 3


async def test_cancel_then_retry_creates_new_execution(client: httpx.AsyncClient) -> None:
    chat = await _chat(client, ALICE)
    task = await _task(client, ALICE, chat["id"])

    cancelled = await client.post(f"/api/tasks/{task['id']}/cancel", headers=ALICE)
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["completed_at"] is not None

    retried = await client.post(f"/api/tasks/{task['id']}/retry", headers=ALICE)
    assert retried.status_code == 200 and retried.json()["status"] == "QUEUED"

    execs = (await client.get(f"/api/tasks/{task['id']}/executions", headers=ALICE)).json()
    assert [e["attempt"] for e in execs] == [1, 2]
    assert [e["status"] for e in execs] == ["CANCELLED", "QUEUED"]
    assert execs[0]["thread_id"] != execs[1]["thread_id"]  # new LangGraph thread
    assert retried.json()["current_execution_id"] == execs[1]["id"]

    events = (await client.get(f"/api/tasks/{task['id']}/events", headers=ALICE)).json()
    assert [e["event"] for e in events] == ["task.created", "task.cancelled", "task.retried"]


@pytest.mark.parametrize("action", ["cancel", "retry"])
async def test_invalid_transitions_conflict(client: httpx.AsyncClient, action: str) -> None:
    chat = await _chat(client, ALICE)
    task = await _task(client, ALICE, chat["id"])
    if action == "cancel":
        await client.post(f"/api/tasks/{task['id']}/cancel", headers=ALICE)
    # cancelling a cancelled task / retrying a queued task are both invalid
    res = await client.post(f"/api/tasks/{task['id']}/{action}", headers=ALICE)
    if action == "retry":
        assert res.status_code == 409
    else:
        assert (await client.post(f"/api/tasks/{task['id']}/cancel", headers=ALICE)).status_code == 409


async def test_concurrent_retries_create_one_execution(client: httpx.AsyncClient) -> None:
    chat = await _chat(client, ALICE)
    task = await _task(client, ALICE, chat["id"])
    await client.post(f"/api/tasks/{task['id']}/cancel", headers=ALICE)

    results = await asyncio.gather(
        *(client.post(f"/api/tasks/{task['id']}/retry", headers=ALICE) for _ in range(5))
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409, 409, 409, 409]
    execs = (await client.get(f"/api/tasks/{task['id']}/executions", headers=ALICE)).json()
    assert len(execs) == 2


# ---------- isolation & auth ----------
async def test_other_users_cannot_touch_my_chats_or_tasks(client: httpx.AsyncClient) -> None:
    chat = await _chat(client, ALICE)
    task = await _task(client, ALICE, chat["id"])
    cid, tid = chat["id"], task["id"]

    attempts = [
        client.get(f"/api/chats/{cid}", headers=BOB),
        client.get(f"/api/chats/{cid}/messages", headers=BOB),
        client.post(f"/api/chats/{cid}/messages", headers=BOB, json={"content": "hi"}),
        client.post(f"/api/chats/{cid}/tasks", headers=BOB, json={"request": "x"}),
        client.get(f"/api/tasks/{tid}", headers=BOB),
        client.get(f"/api/tasks/{tid}/executions", headers=BOB),
        client.get(f"/api/tasks/{tid}/events", headers=BOB),
        client.post(f"/api/tasks/{tid}/cancel", headers=BOB),
        client.post(f"/api/tasks/{tid}/retry", headers=BOB),
        client.get("/api/tasks", headers=BOB, params={"chat_id": cid}),
    ]
    for res in await asyncio.gather(*attempts):
        assert res.status_code == 404, (res.request.method, res.request.url, res.status_code)

    assert (await client.get("/api/chats", headers=BOB)).json() == []
    assert (await client.get("/api/tasks", headers=BOB)).json() == []
    # Alice's task is untouched.
    assert (await client.get(f"/api/tasks/{tid}", headers=ALICE)).json()["status"] == "QUEUED"


async def test_unknown_ids_are_404(client: httpx.AsyncClient) -> None:
    missing = uuid.uuid4()
    assert (await client.get(f"/api/chats/{missing}", headers=ALICE)).status_code == 404
    assert (await client.get(f"/api/tasks/{missing}", headers=ALICE)).status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/chats"),
        ("POST", "/api/chats"),
        ("GET", "/api/tasks"),
        ("GET", "/api/approvals"),
        ("POST", f"/api/tasks/{uuid.uuid4()}/cancel"),
        ("POST", f"/api/approvals/{uuid.uuid4()}/approve"),
    ],
)
async def test_every_endpoint_requires_auth(client: httpx.AsyncClient, method: str, path: str) -> None:
    res = await client.request(method, path, json={})
    assert res.status_code == 401
