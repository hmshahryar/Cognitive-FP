"""Onboarding answers belong to the caller's own workspace, and only fixed choices are stored."""

import httpx
import pytest

from tests.conftest import auth_headers

ALICE = auth_headers("user_alice")
BOB = auth_headers("user_bob")

ANSWERS = {
    "workspace_name": "  Northwind   Studio ",
    "team_size": "2_10",
    "goals": ["email", "crm", "email"],
}


async def test_new_workspace_is_not_onboarded(client: httpx.AsyncClient) -> None:
    res = await client.get("/api/onboarding", headers=ALICE)
    assert res.status_code == 200
    assert res.json() == {
        "workspace_name": "Personal workspace",
        "team_size": None,
        "goals": [],
        "completed": False,
        "completed_at": None,
    }


async def test_saving_progress_then_finishing(client: httpx.AsyncClient) -> None:
    saved = await client.put("/api/onboarding", headers=ALICE, json=ANSWERS)
    assert saved.status_code == 200
    body = saved.json()
    assert body["workspace_name"] == "Northwind Studio"
    assert body["goals"] == ["email", "crm"]
    assert body["completed"] is False

    done = await client.put("/api/onboarding", headers=ALICE, json={**ANSWERS, "complete": True})
    assert done.json()["completed"] is True
    finished_at = done.json()["completed_at"]

    # Saving again later keeps it finished, with the original time.
    again = await client.put("/api/onboarding", headers=ALICE, json=ANSWERS)
    assert again.json()["completed"] is True
    assert again.json()["completed_at"] == finished_at
    me = await client.get("/api/me", headers=ALICE)
    assert me.json()["workspace"]["name"] == "Northwind Studio"


async def test_each_user_sees_only_their_own_answers(client: httpx.AsyncClient) -> None:
    await client.put("/api/onboarding", headers=ALICE, json={**ANSWERS, "complete": True})
    bob = await client.get("/api/onboarding", headers=BOB)
    assert bob.json()["completed"] is False
    assert bob.json()["workspace_name"] == "Personal workspace"


@pytest.mark.parametrize(
    "change",
    [
        {"team_size": "huge"},
        {"goals": ["hack"]},
        {"workspace_name": "   "},
        {"workspace_name": "x" * 81},
        {"workspace_id": "00000000-0000-0000-0000-000000000000"},
        {"user_id": "user_bob"},
    ],
)
async def test_unknown_values_and_ids_are_refused(
    client: httpx.AsyncClient, change: dict
) -> None:
    res = await client.put("/api/onboarding", headers=ALICE, json={**ANSWERS, **change})
    assert res.status_code == 422


async def test_onboarding_needs_sign_in(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/onboarding")).status_code == 401
