"""Phase 3: signed multi-step plans run in order, once, and stop safely."""

import uuid
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from cognitive_mesh.capabilities import RiskLevel
from cognitive_mesh.db.base import utcnow
from cognitive_mesh.integrations import google_sheets as gs
from cognitive_mesh.integrations.gateway import ActionOutcomeUnknownError, ActionRejectedError
from cognitive_mesh.models import ConnectionStatus, IntegrationConnection, WorkflowRun, WorkflowStep
from cognitive_mesh.workflows.engine import executor, ledger
from cognitive_mesh.workflows.engine import plan as plan_module
from cognitive_mesh.workflows.engine.plan import (
    PlanInvalidError,
    PlanReferenceError,
    references_in,
    resolve_references,
)
from tests.conftest import FakeGateway, auth_headers
from tests.fakes_ai import FakeTeam
from tests.test_graph_approval import fresh_runner
from tests.test_sheets import CREATED, SHEET_ID, connect_sheets

ALICE = auth_headers("user_alice")
BOB = auth_headers("user_bob")
SECOND_ID = "2BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"


def create_payload(name: str = "Leads") -> dict:
    return gs.build_create_payload(name=name, rows=[])


def append_payload(rows: list[list[str]] | None = None) -> dict:
    """An append into the sheet created by the `create` step."""
    ref = {"$ref": "steps.create.external_id"}
    payload = gs.build_append_payload(
        spreadsheet_id=SHEET_ID, name="Leads", tab="Sheet1", rows=rows or [["Ali"]]
    )
    payload["spreadsheet"]["id"] = ref
    payload["arguments"]["spreadsheetId"] = ref
    return payload


def fixed_append(rows: list[list[str]]) -> dict:
    return gs.build_append_payload(spreadsheet_id=SECOND_ID, name="Log", tab="Sheet1", rows=rows)


def two_step_plan(**second: object) -> ledger.WorkflowPlan:
    return ledger.build_plan(
        playbook="test_flow",
        version=1,
        title="Create and fill a sheet",
        steps=[
            {"key": "create", "action_type": gs.CREATE_ACTION, "payload": create_payload()},
            {
                "key": "fill",
                "action_type": gs.APPEND_ACTION,
                "payload": append_payload(),
                "depends_on": ["create"],
                **second,
            },
        ],
    )


async def start(client: httpx.AsyncClient, app: FastAPI, plan: ledger.WorkflowPlan) -> dict:
    await connect_sheets(client, app)
    me = (await client.get("/api/me", headers=ALICE)).json()
    async with app.state.db.sessionmaker() as session:
        run = await ledger.create_run(
            session,
            app.state.approval_signer,
            workspace_id=uuid.UUID(me["workspace"]["id"]),
            created_by=uuid.UUID(me["user"]["id"]),
            plan=plan,
        )
        run_id = run.id
    return (await client.get(f"/api/workflow-runs/{run_id}", headers=ALICE)).json()


async def approve(client: httpx.AsyncClient, run: dict, gate: int = 0) -> httpx.Response:
    body = {"plan_hash": run["plan_hash"], "gate": gate}
    return await client.post(f"/api/workflow-runs/{run['id']}/approve", headers=ALICE, json=body)


async def current(client: httpx.AsyncClient, run: dict) -> dict:
    return (await client.get(f"/api/workflow-runs/{run['id']}", headers=ALICE)).json()


def statuses(run: dict) -> dict[str, str]:
    return {step["key"]: step["status"] for step in run["steps"]}


# ---------- plan validation ----------
@pytest.mark.parametrize(
    "steps",
    [
        [{"key": "a", "action_type": "not.real", "payload": {}}],
        [{"key": "a"}],
        [
            {"key": "a", "action_type": gs.CREATE_ACTION, "payload": create_payload()},
            {"key": "a", "action_type": gs.CREATE_ACTION, "payload": create_payload()},
        ],
        [
            {"key": "a", "action_type": gs.CREATE_ACTION, "payload": create_payload(),
             "depends_on": ["b"]},
            {"key": "b", "action_type": gs.CREATE_ACTION, "payload": create_payload()},
        ],
        [{"key": "a", "action_type": gs.CREATE_ACTION, "payload": create_payload(), "gate": 1}],
        [{"key": "a", "action_type": gs.CREATE_ACTION, "payload": {"bad": True}}],
        [{"key": "a", "action_type": gs.CREATE_ACTION, "payload": create_payload(), "extra": 1}],
    ],
)
def test_unsafe_plans_are_rejected(steps: list[dict]) -> None:
    with pytest.raises(PlanInvalidError):
        ledger.build_plan(playbook="bad", version=1, title="Bad", steps=steps)


def test_a_step_can_only_reference_its_dependencies() -> None:
    with pytest.raises(PlanInvalidError):
        ledger.build_plan(
            playbook="bad", version=1, title="Bad",
            steps=[
                {"key": "create", "action_type": gs.CREATE_ACTION, "payload": create_payload()},
                {"key": "fill", "action_type": gs.APPEND_ACTION, "payload": append_payload()},
            ],
        )


@pytest.mark.parametrize(
    "value",
    [{"$ref": "steps.a.email"}, {"$ref": "secrets.key"}, {"$ref": "steps.a.external_id", "x": 1}],
)
def test_malformed_references_are_rejected(value: dict) -> None:
    with pytest.raises(PlanInvalidError):
        references_in(value)


@pytest.mark.parametrize("output", ["", "a b", "x\ny", "=IMPORTXML(1)", "<script>"])
def test_unsafe_returned_ids_cannot_fill_a_reference(output: str) -> None:
    with pytest.raises(PlanReferenceError):
        resolve_references({"id": {"$ref": "steps.a.external_id"}}, {"a": output})


def test_spending_step_needs_its_own_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    risks = {gs.UPDATE_ACTION: RiskLevel.SPEND}
    monkeypatch.setattr(plan_module, "_risk", lambda action: risks.get(action))
    spend = gs.build_update_payload(
        spreadsheet_id=SHEET_ID, name="X", tab="Sheet1", cell="A1", value="1", previous=""
    )
    with pytest.raises(PlanInvalidError, match="own approval gate"):
        ledger.build_plan(
            playbook="ads", version=1, title="Ads",
            steps=[
                {"key": "build", "action_type": gs.CREATE_ACTION, "payload": create_payload()},
                {"key": "launch", "action_type": gs.UPDATE_ACTION, "payload": spend},
            ],
        )
    assert ledger.build_plan(
        playbook="ads", version=1, title="Ads",
        steps=[
            {"key": "build", "action_type": gs.CREATE_ACTION, "payload": create_payload()},
            {"key": "launch", "action_type": gs.UPDATE_ACTION, "payload": spend, "gate": 1,
             "depends_on": ["build"]},
        ],
    ).last_gate == 1


# ---------- approval ----------
async def test_nothing_runs_before_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    run = await start(client, app, two_step_plan())
    assert run["status"] == "AWAITING_APPROVAL"
    assert await app.state.runner.run_once() is False and gateway.publish_calls == []


async def test_approved_plan_runs_in_order_and_fills_the_reference(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.publish_response = CREATED
    run = await start(client, app, two_step_plan())
    assert (await approve(client, run)).status_code == 200
    await app.state.runner.run_once()
    final = await current(client, run)
    assert final["status"] == "COMPLETED"
    assert [c["tool"] for c in gateway.publish_calls] == [gs.CREATE_TOOL, gs.APPEND_TOOL]
    assert gateway.publish_calls[1]["arguments"]["spreadsheetId"] == SHEET_ID


async def test_double_approval_cannot_run_twice(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.publish_response = CREATED
    run = await start(client, app, two_step_plan())
    assert (await approve(client, run)).status_code == 200
    assert (await approve(client, run)).status_code == 409
    await app.state.runner.run_once()
    await app.state.runner.run_once()
    assert len(gateway.publish_calls) == 2


async def test_wrong_hash_or_gate_cannot_approve(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    run = await start(client, app, two_step_plan())
    assert (await approve(client, {**run, "plan_hash": "0" * 64})).status_code == 409
    assert (await approve(client, run, gate=1)).status_code == 409
    assert gateway.publish_calls == []


async def test_rejected_plan_never_runs(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    run = await start(client, app, two_step_plan())
    response = await client.post(f"/api/workflow-runs/{run['id']}/reject", headers=ALICE, json={})
    assert response.json()["status"] == "REJECTED"
    await app.state.runner.run_once()
    assert gateway.publish_calls == [] and set(statuses(response.json()).values()) == {"SKIPPED"}


async def test_other_workspaces_cannot_see_or_decide(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    run = await start(client, app, two_step_plan())
    await client.get("/api/me", headers=BOB)
    url = f"/api/workflow-runs/{run['id']}"
    assert (await client.get(url, headers=BOB)).status_code == 404
    body = {"plan_hash": run["plan_hash"], "gate": 0}
    assert (await client.post(f"{url}/approve", headers=BOB, json=body)).status_code == 404
    assert (await client.post(f"{url}/cancel", headers=BOB)).status_code == 404
    assert (await client.get("/api/workflow-runs", headers=BOB)).json() == []


def test_no_api_accepts_a_plan_from_the_browser(app: FastAPI) -> None:
    routes = {
        (route.path, method)
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/workflow-runs")
        for method in getattr(route, "methods", set())
    }
    assert ("/api/workflow-runs", "POST") not in routes


# ---------- tampering ----------
async def test_changed_payload_after_approval_is_blocked(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    run = await start(client, app, two_step_plan())
    await approve(client, run)
    changed = create_payload("Attacker")
    async with app.state.db.sessionmaker() as session:
        await session.execute(
            update(WorkflowStep)
            .where(WorkflowStep.run_id == uuid.UUID(run["id"]), WorkflowStep.key == "create")
            .values(payload=changed)
        )
        await session.commit()
    await app.state.runner.run_once()
    assert (await current(client, run))["status"] == "INVALIDATED" and gateway.publish_calls == []


async def test_recomputed_hash_still_fails_the_signature(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    run = await start(client, app, two_step_plan())
    await approve(client, run)
    async with app.state.db.sessionmaker() as session:
        step = await session.scalar(
            select(WorkflowStep).where(
                WorkflowStep.run_id == uuid.UUID(run["id"]), WorkflowStep.key == "create"
            )
        )
        assert step is not None
        step.payload = create_payload("Attacker")
        step.payload_hash = ledger._step_hash(step.payload, step)
        await session.commit()
    await app.state.runner.run_once()
    assert (await current(client, run))["status"] == "INVALIDATED" and gateway.publish_calls == []


async def test_disconnected_account_stops_remaining_steps(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    run = await start(client, app, two_step_plan())
    await approve(client, run)
    async with app.state.db.sessionmaker() as session:
        await session.execute(
            update(IntegrationConnection).values(status=ConnectionStatus.DISCONNECTED)
        )
        await session.commit()
    await app.state.runner.run_once()
    assert (await current(client, run))["status"] == "INVALIDATED" and gateway.publish_calls == []


# ---------- failure handling ----------
async def test_definite_failure_skips_dependents_but_runs_the_logger(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    plan = ledger.build_plan(
        playbook="test_flow", version=1, title="With log",
        steps=[
            {"key": "create", "action_type": gs.CREATE_ACTION, "payload": create_payload()},
            {"key": "fill", "action_type": gs.APPEND_ACTION, "payload": append_payload(),
             "depends_on": ["create"]},
            {"key": "log", "action_type": gs.APPEND_ACTION,
             "payload": fixed_append([["failed"]]), "depends_on": ["create"],
             "run_after_failure": True},
        ],
    )
    run = await start(client, app, plan)
    await approve(client, run)
    original = gateway.execute_approved_action

    async def first_fails(**kwargs):  # type: ignore[no-untyped-def]
        if kwargs["tool_slug"] == gs.CREATE_TOOL:
            gateway.publish_calls.append({"tool": gs.CREATE_TOOL})
            raise ActionRejectedError("denied")
        return await original(**kwargs)

    gateway.execute_approved_action = first_fails  # type: ignore[method-assign]
    gateway.publish_response = {"spreadsheetId": SECOND_ID}
    await app.state.runner.run_once()
    final = await current(client, run)
    assert statuses(final) == {"create": "FAILED", "fill": "SKIPPED", "log": "SUCCEEDED"}
    assert final["status"] == "PARTIALLY_COMPLETED"


async def test_unknown_result_is_never_repeated_and_stops_dependents(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.publish_error = ActionOutcomeUnknownError("timeout")
    run = await start(client, app, two_step_plan())
    await approve(client, run)
    await app.state.runner.run_once()
    await app.state.runner.run_once()
    final = await current(client, run)
    assert statuses(final) == {"create": "NEEDS_CHECK", "fill": "SKIPPED"}
    assert final["status"] == "NEEDS_CHECK" and len(gateway.publish_calls) == 1


class Crash(BaseException):
    pass


@pytest.mark.parametrize("crash_on", [1, 2])
async def test_crash_while_sending_never_repeats_any_step(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam, crash_on: int
) -> None:
    gateway.publish_response = CREATED
    run = await start(client, app, two_step_plan())
    await approve(client, run)
    original = gateway.execute_approved_action
    calls = {"n": 0}

    async def crashes(**kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        result = await original(**kwargs)
        if calls["n"] == crash_on:
            raise Crash
        return result

    gateway.execute_approved_action = crashes  # type: ignore[method-assign]
    with pytest.raises(Crash):
        await app.state.runner.run_once()
    await app.state.runner.close()
    gateway.execute_approved_action = original  # type: ignore[method-assign]
    sent = len(gateway.publish_calls)

    restarted = fresh_runner(app, gateway, team)
    try:
        await restarted.recover()
        await restarted.run_once()
        await restarted.run_once()
    finally:
        await restarted.close()
    assert len(gateway.publish_calls) == sent == crash_on
    assert (await current(client, run))["status"] == "NEEDS_CHECK"


async def test_crash_before_a_step_starts_continues_it_exactly_once(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    gateway.publish_response = CREATED
    run = await start(client, app, two_step_plan())
    await approve(client, run)
    await app.state.runner.close()
    restarted = fresh_runner(app, gateway, team)
    try:
        await restarted.recover()
        await restarted.run_once()
    finally:
        await restarted.close()
    assert (await current(client, run))["status"] == "COMPLETED"
    assert len(gateway.publish_calls) == 2


# ---------- gates and waits ----------
async def test_second_gate_needs_its_own_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.publish_response = CREATED
    run = await start(client, app, two_step_plan(gate=1))
    await approve(client, run)
    await app.state.runner.run_once()
    paused = await current(client, run)
    assert paused["status"] == "AWAITING_APPROVAL" and paused["approved_gate"] == 0
    assert statuses(paused) == {"create": "SUCCEEDED", "fill": "PENDING"}
    assert len(gateway.publish_calls) == 1

    assert (await approve(client, paused, gate=1)).status_code == 200
    await app.state.runner.run_once()
    assert (await current(client, run))["status"] == "COMPLETED"
    assert len(gateway.publish_calls) == 2


async def test_failed_gate_never_asks_for_the_next_one(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.publish_error = ActionRejectedError("denied")
    run = await start(client, app, two_step_plan(gate=1))
    await approve(client, run)
    await app.state.runner.run_once()
    final = await current(client, run)
    assert final["status"] == "FAILED" and statuses(final)["fill"] == "SKIPPED"


def wait_plan(**wait: object) -> ledger.WorkflowPlan:
    return ledger.build_plan(
        playbook="test_flow", version=1, title="Wait then write",
        steps=[
            {"key": "pause", **wait},
            {"key": "write", "action_type": gs.CREATE_ACTION, "payload": create_payload(),
             "depends_on": ["pause"]},
        ],
    )


async def _make_due(app: FastAPI, run: dict) -> None:
    async with app.state.db.sessionmaker() as session:
        await session.execute(
            update(WorkflowStep)
            .where(WorkflowStep.run_id == uuid.UUID(run["id"]), WorkflowStep.key == "pause")
            .values(not_before=utcnow() - timedelta(seconds=1))
        )
        await session.commit()


async def test_timer_wait_survives_a_restart(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    gateway.publish_response = CREATED
    run = await start(client, app, wait_plan(wait_seconds=86400))
    await approve(client, run)
    await app.state.runner.run_once()
    assert (await current(client, run))["status"] == "WAITING" and gateway.publish_calls == []
    assert await app.state.runner.run_once() is False

    await app.state.runner.close()
    await _make_due(app, run)
    restarted = fresh_runner(app, gateway, team)
    try:
        await restarted.recover()
        await restarted.run_once()
    finally:
        await restarted.close()
    assert (await current(client, run))["status"] == "COMPLETED"
    assert len(gateway.publish_calls) == 1


async def test_event_wait_resumes_only_on_its_event(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.publish_response = CREATED
    run = await start(client, app, wait_plan(wait_event="customer_replied"))
    await approve(client, run)
    await app.state.runner.run_once()
    me = (await client.get("/api/me", headers=ALICE)).json()
    workspace = uuid.UUID(me["workspace"]["id"])
    run_id = uuid.UUID(run["id"])
    async with app.state.db.sessionmaker() as session:
        assert await ledger.signal_event(session, workspace, run_id, "other_event") is False
        assert await ledger.signal_event(session, uuid.uuid4(), run_id, "customer_replied") is False
        assert await ledger.signal_event(session, workspace, run_id, "customer_replied") is True
        assert await ledger.signal_event(session, workspace, run_id, "customer_replied") is False
    await app.state.runner.run_once()
    assert (await current(client, run))["status"] == "COMPLETED"
    assert len(gateway.publish_calls) == 1


async def test_event_wait_times_out_without_writing(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    run = await start(client, app, wait_plan(wait_event="customer_replied", wait_seconds=3600))
    await approve(client, run)
    await app.state.runner.run_once()
    await _make_due(app, run)
    await app.state.runner.run_once()
    final = await current(client, run)
    assert statuses(final) == {"pause": "FAILED", "write": "SKIPPED"}
    assert final["status"] == "FAILED" and gateway.publish_calls == []


async def test_cancel_stops_a_waiting_run(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    run = await start(client, app, wait_plan(wait_seconds=3600))
    await approve(client, run)
    await app.state.runner.run_once()
    response = await client.post(f"/api/workflow-runs/{run['id']}/cancel", headers=ALICE)
    assert response.json()["status"] == "CANCELLED"
    await _make_due(app, run)
    await app.state.runner.run_once()
    assert gateway.publish_calls == []


async def test_missing_scope_fails_without_sending(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    run = await start(client, app, two_step_plan())
    await approve(client, run)
    async with app.state.db.sessionmaker() as session:
        await session.execute(update(IntegrationConnection).values(granted_scopes=["read.only"]))
        await session.commit()
    await executor.advance_due(app.state.db, gateway, app.state.approval_signer)
    assert statuses(await current(client, run)) == {"create": "FAILED", "fill": "SKIPPED"}
    assert gateway.publish_calls == []


async def test_changed_title_after_approval_is_blocked(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    run = await start(client, app, two_step_plan())
    await approve(client, run)
    async with app.state.db.sessionmaker() as session:
        await session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == uuid.UUID(run["id"]))
            .values(title="Something else")
        )
        await session.commit()
    await app.state.runner.run_once()
    assert (await current(client, run))["status"] == "INVALIDATED" and gateway.publish_calls == []


async def test_one_broken_run_does_not_block_another(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway.publish_response = CREATED
    broken = await start(client, app, two_step_plan())
    await approve(client, broken)
    me = (await client.get("/api/me", headers=ALICE)).json()
    async with app.state.db.sessionmaker() as session:
        healthy = await ledger.create_run(
            session, app.state.approval_signer,
            workspace_id=uuid.UUID(me["workspace"]["id"]),
            created_by=uuid.UUID(me["user"]["id"]),
            plan=two_step_plan(),
        )
        healthy_id = healthy.id
    other = (await client.get(f"/api/workflow-runs/{healthy_id}", headers=ALICE)).json()
    await approve(client, other)
    original = executor.advance_run

    async def fails_first(db, gw, signer, run_id):  # type: ignore[no-untyped-def]
        if str(run_id) == broken["id"]:
            raise RuntimeError("broken run")
        return await original(db, gw, signer, run_id)

    monkeypatch.setattr(executor, "advance_run", fails_first)
    assert await executor.advance_due(app.state.db, gateway, app.state.approval_signer) is True
    assert (await current(client, other))["status"] == "COMPLETED"
