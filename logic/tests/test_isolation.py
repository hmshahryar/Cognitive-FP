import uuid

import httpx

from tests.conftest import auth_headers


async def _me(client: httpx.AsyncClient, clerk_user_id: str) -> dict:
    res = await client.get("/api/me", headers=auth_headers(clerk_user_id))
    assert res.status_code == 200
    return res.json()


async def test_each_user_gets_their_own_workspace(client: httpx.AsyncClient) -> None:
    alice = await _me(client, "user_alice")
    bob = await _me(client, "user_bob")

    assert alice["user"]["id"] != bob["user"]["id"]
    assert alice["workspace"]["id"] != bob["workspace"]["id"]

    res = await client.get("/api/workspace", headers=auth_headers("user_bob"))
    assert res.json()["id"] == bob["workspace"]["id"]


async def test_user_can_read_own_workspace_by_id(client: httpx.AsyncClient) -> None:
    alice = await _me(client, "user_alice")
    ws_id = alice["workspace"]["id"]

    res = await client.get(f"/api/workspaces/{ws_id}", headers=auth_headers("user_alice"))
    assert res.status_code == 200
    assert res.json()["id"] == ws_id


async def test_user_cannot_read_another_users_workspace(client: httpx.AsyncClient) -> None:
    alice = await _me(client, "user_alice")
    await _me(client, "user_bob")

    res = await client.get(
        f"/api/workspaces/{alice['workspace']['id']}", headers=auth_headers("user_bob")
    )
    # 404, not 403: don't reveal that the workspace exists.
    assert res.status_code == 404


async def test_unknown_workspace_id_is_404(client: httpx.AsyncClient) -> None:
    res = await client.get(f"/api/workspaces/{uuid.uuid4()}", headers=auth_headers("user_alice"))
    assert res.status_code == 404


async def test_frontend_supplied_user_ids_are_ignored(client: httpx.AsyncClient) -> None:
    alice = await _me(client, "user_alice")

    # Bob tries to pass Alice's identity via query, headers and body.
    res = await client.request(
        "GET",
        "/api/me",
        params={"user_id": alice["user"]["id"], "clerk_user_id": "user_alice"},
        headers={
            **auth_headers("user_bob"),
            "X-User-Id": alice["user"]["id"],
            "X-Clerk-User-Id": "user_alice",
        },
        json={"user_id": alice["user"]["id"], "clerk_user_id": "user_alice"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["user"]["clerk_user_id"] == "user_bob"
    assert body["workspace"]["id"] != alice["workspace"]["id"]
