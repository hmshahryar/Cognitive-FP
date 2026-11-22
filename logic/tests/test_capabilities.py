"""The tool registry and workflow shape are safety boundaries, so they are pinned here."""

from unittest.mock import MagicMock

import pytest

from cognitive_mesh.capabilities import (
    CAPABILITIES,
    AgentRole,
    Capability,
    CapabilityDeniedError,
    RiskLevel,
    authorize,
    capabilities_for,
    read_tool_slugs,
    write_tool_slugs,
)
from cognitive_mesh.integrations.actions import ACTIONS
from cognitive_mesh.integrations.gateway import APPROVED_ACTION_TOOLS, READ_ONLY_TOOLS
from cognitive_mesh.workflows.command_graph import build_graph

EXPECTED_READS = {
    "LINKEDIN_GET_MY_INFO",
    "COMPOSIO_SEARCH_NEWS",
    "COMPOSIO_SEARCH_WEB",
    "COMPOSIO_SEARCH_FETCH_URL_CONTENT",
    "GMAIL_GET_PROFILE",
    "GMAIL_FETCH_EMAILS",
    "GOOGLECALENDAR_EVENTS_LIST",
    "GOOGLESHEETS_SEARCH_SPREADSHEETS",
    "GOOGLESHEETS_GET_SHEET_NAMES",
    "GOOGLESHEETS_VALUES_GET",
}
EXPECTED_WRITES = {
    "LINKEDIN_CREATE_LINKED_IN_POST",
    "GMAIL_SEND_EMAIL",
    "GMAIL_FORWARD_MESSAGE",
    "GMAIL_MOVE_TO_TRASH",
    "GOOGLECALENDAR_CREATE_EVENT",
    "GOOGLECALENDAR_PATCH_EVENT",
    "GOOGLECALENDAR_DELETE_EVENT",
    "GOOGLESHEETS_CREATE_GOOGLE_SHEET1",
    "GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND",
    "GOOGLESHEETS_VALUES_UPDATE",
}
EXPECTED_NODES = {
    "__start__", "__end__", "prepare", "use_provided_text", "gmail_action", "calendar_action",
    "sheets_action", "research", "write", "request_approval", "decide", "publish",
}


def _graph():
    return build_graph(MagicMock()).compile().get_graph()


def test_gateway_allowlists_are_unchanged_and_come_from_the_registry() -> None:
    assert READ_ONLY_TOOLS == read_tool_slugs() == EXPECTED_READS
    assert APPROVED_ACTION_TOOLS == write_tool_slugs() == EXPECTED_WRITES
    assert not READ_ONLY_TOOLS & APPROVED_ACTION_TOOLS


def test_every_approved_action_matches_exactly_one_write_capability() -> None:
    writes = {c.action_type: c for c in CAPABILITIES if c.is_write}
    assert set(writes) == set(ACTIONS)
    for action_type, action in ACTIONS.items():
        assert writes[action_type].tool_slug == action.tool_slug
        assert writes[action_type].toolkit == action.toolkit


def test_agent_roles_only_see_their_own_tools() -> None:
    prefixes = {
        AgentRole.INBOX: "GMAIL_",
        AgentRole.SCHEDULING: "GOOGLECALENDAR_",
        AgentRole.DATA: "GOOGLESHEETS_",
        AgentRole.RESEARCH: "COMPOSIO_SEARCH_",
        AgentRole.SOCIAL: "LINKEDIN_",
    }
    for role, prefix in prefixes.items():
        tools = capabilities_for(role)
        assert tools and all(c.tool_slug.startswith(prefix) for c in tools)


def test_research_agent_has_no_write_tools() -> None:
    assert not any(c.is_write for c in capabilities_for(AgentRole.RESEARCH))


@pytest.mark.parametrize(
    ("role", "name"),
    [
        (AgentRole.RESEARCH, "email.message.send"),
        (AgentRole.DATA, "calendar.event.cancel"),
        (AgentRole.INBOX, "not.a.capability"),
    ],
)
def test_authorize_denies_other_roles_and_unknown_names(role: AgentRole, name: str) -> None:
    with pytest.raises(CapabilityDeniedError):
        authorize(role, name)


def test_authorize_returns_an_allowed_capability() -> None:
    assert authorize(AgentRole.DATA, "sheets.values.read").tool_slug == "GOOGLESHEETS_VALUES_GET"


def test_a_write_without_an_approval_action_is_rejected() -> None:
    with pytest.raises(ValueError, match="action_type"):
        Capability(
            "x.write", "x", "X_WRITE", RiskLevel.INTERNAL_WRITE, frozenset({AgentRole.DATA})
        )


def test_a_capability_needs_at_least_one_agent() -> None:
    with pytest.raises(ValueError, match="agent"):
        Capability("x.read", "x", "X_READ", RiskLevel.READ, frozenset())


def test_workflow_node_names_stay_compatible_with_saved_checkpoints() -> None:
    graph = _graph()
    assert set(graph.nodes) == EXPECTED_NODES
    assert len(graph.edges) == 30


def test_publish_is_reachable_only_after_the_decision_node() -> None:
    graph = _graph()
    assert {e.source for e in graph.edges if e.target == "publish"} == {"decide"}
    assert {e.source for e in graph.edges if e.target == "decide"} == {"request_approval"}
