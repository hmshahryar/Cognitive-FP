"""HubSpot connects with only the permissions the app uses, and users never handle keys."""

from types import SimpleNamespace
from typing import Any

import pytest

from cognitive_mesh.integrations.catalog import get_connector
from cognitive_mesh.integrations.gateway import (
    ComposioGateway,
    IntegrationUnavailableError,
    _requested_scopes,
)

SPEC = get_connector("hubspot")
assert SPEC is not None
NAME = SPEC.limited_config_name
LIMITED = list(SPEC.allowed_scopes)
UNUSED_DEFAULTS = "transactional-email automation marketing.campaigns.read"


class Row:
    def __init__(self, id_: str, name: str, scopes: Any, managed: bool = True, status: str = "ENABLED") -> None:
        self.data = {
            "id": id_, "name": name, "status": status, "is_composio_managed": managed,
            "toolkit": {"slug": "hubspot"},
            "credentials": {"scopes": scopes, "optional_scopes": UNUSED_DEFAULTS},
        }

    def model_dump(self) -> dict[str, Any]:
        return self.data


class FakeSdk:
    def __init__(self, rows: list[Row] | None = None) -> None:
        self.rows = rows or []
        self.created: list[dict[str, Any]] = []
        self.sessions: list[dict[str, Any]] = []
        self.auth_configs = SimpleNamespace(create=self._create)
        self.sessions_api = SimpleNamespace(create=self._session)
        self.client = SimpleNamespace(
            auth_configs=SimpleNamespace(
                list=lambda **_kw: SimpleNamespace(items=self.rows),
                retrieve=lambda config_id: next(r for r in self.rows if r.data["id"] == config_id),
            )
        )

    def _create(self, *, toolkit: str, options: dict[str, Any]) -> SimpleNamespace:
        self.created.append({"toolkit": toolkit, **options})
        self.rows.append(Row("ac_created", options["name"], options["credentials"]["scopes"]))
        return SimpleNamespace(id="ac_created")

    def _session(self, **kwargs: Any) -> SimpleNamespace:
        self.sessions.append(kwargs)
        return SimpleNamespace(
            authorize=lambda *_a, **_k: SimpleNamespace(id="ca_1", redirect_url="https://link")
        )


def gateway_with(sdk: FakeSdk) -> ComposioGateway:
    gateway = ComposioGateway("")
    sdk.sessions = []
    gateway._client = SimpleNamespace(  # type: ignore[assignment]
        auth_configs=sdk.auth_configs, client=sdk.client, sessions=sdk.sessions_api
    )
    return gateway


async def test_first_connect_creates_one_limited_managed_config() -> None:
    sdk = FakeSdk([Row("ac_default", "HubSpot default", [*LIMITED, "tickets"])])
    gateway = gateway_with(sdk)
    assert set(await gateway.auth_config_scopes("hubspot") or ()) == set(LIMITED)
    assert await gateway.auth_config_scopes("hubspot") is not None
    assert len(sdk.created) == 1
    created = sdk.created[0]
    assert created["type"] == "use_composio_managed_auth" and created["name"] == NAME
    assert set(created["credentials"]["scopes"].split(",")) == set(LIMITED)
    assert "client_secret" not in created["credentials"]


async def test_existing_limited_config_is_reused_after_restart() -> None:
    sdk = FakeSdk([Row("ac_limited", NAME, LIMITED)])
    gateway = gateway_with(sdk)
    await gateway.start_connection(user_id="u1", toolkit_slug="hubspot", callback_url="http://cb")
    assert sdk.created == []
    assert sdk.sessions[0]["auth_configs"] == {"hubspot": "ac_limited"}


@pytest.mark.parametrize(
    "row",
    [
        Row("ac_broad", NAME, [*LIMITED, "tickets"]),
        Row("ac_partial", NAME, LIMITED[:2]),
        Row("ac_off", NAME, LIMITED, status="DISABLED"),
        Row("ac_other", "Another config", LIMITED),
    ],
)
async def test_unsuitable_configs_are_not_used(row: Row) -> None:
    sdk = FakeSdk([row])
    gateway = gateway_with(sdk)
    await gateway.auth_config_scopes("hubspot")
    assert len(sdk.created) == 1


async def test_other_apps_keep_their_existing_config_path() -> None:
    sdk = FakeSdk()
    gateway = gateway_with(sdk)
    await gateway.start_connection(user_id="u1", toolkit_slug="gmail", callback_url="http://cb")
    assert sdk.created == [] and "auth_configs" not in sdk.sessions[0]


async def test_config_for_another_app_is_refused() -> None:
    row = Row("ac_wrong", "Wrong", LIMITED)
    row.data["toolkit"] = {"slug": "gmail"}
    sdk = FakeSdk([row])
    gateway = ComposioGateway("", auth_configs={"hubspot": "ac_wrong"})
    gateway._client = SimpleNamespace(  # type: ignore[assignment]
        auth_configs=sdk.auth_configs, client=sdk.client, sessions=sdk.sessions_api
    )
    with pytest.raises(IntegrationUnavailableError):
        await gateway.auth_config_scopes("hubspot")


def test_custom_app_optional_scopes_count_as_requested() -> None:
    config = Row("ac_custom", "Custom", LIMITED, managed=False).model_dump()
    assert "automation" in _requested_scopes(config)
