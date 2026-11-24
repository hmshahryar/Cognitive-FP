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
    "HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA",
    "SALESFORCE_SEARCH_CONTACTS",
    "SALESFORCE_SEARCH_ACCOUNTS",
    "ZOHO_SEARCH_CONTACTS",
    "NOTION_SEARCH_NOTION_PAGE",
    "NOTION_RETRIEVE_PAGE",
    "NOTION_GET_PAGE_MARKDOWN",
    "FACEBOOK_LIST_MANAGED_PAGES",
    "INSTAGRAM_GET_USER_INFO",
    "INSTAGRAM_GET_IG_USER_CONTENT_PUBLISHING_LIMIT",
    "CANVA_GET_USERS_ME_CAPABILITIES",
    "CANVA_GET_URL_ASSET_UPLOADS_JOBID",
    "CANVA_GET_DESIGN_EXPORT_JOB_RESULT",
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
    "HUBSPOT_CREATE_CONTACT",
    "HUBSPOT_UPDATE_CONTACT",
    "HUBSPOT_CREATE_NOTE",
    "SALESFORCE_CREATE_CONTACT",
    "SALESFORCE_UPDATE_CONTACT",
    "SALESFORCE_CREATE_NOTE",
    "ZOHO_CREATE_CONTACT",
    "ZOHO_UPDATE_ZOHO_RECORD",
    "ZOHO_CREATE_NOTE",
    "NOTION_CREATE_NOTION_PAGE",
    "NOTION_ADD_MULTIPLE_PAGE_CONTENT",
    "FACEBOOK_CREATE_POST",
    "FACEBOOK_CREATE_PHOTO_POST",
    "INSTAGRAM_POST_IG_USER_MEDIA",
    "INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH",
}
EXPECTED_NODES = {
    "__start__", "__end__", "prepare", "use_provided_text", "gmail_action", "calendar_action",
    "sheets_action", "crm_action", "notion_action", "social_action", "research", "write",
    "request_approval", "decide", "publish",
}


def _graph():
    return build_graph(MagicMock()).compile().get_graph()


def test_gateway_allowlists_are_unchanged_and_come_from_the_registry() -> None:
    assert READ_ONLY_TOOLS == read_tool_slugs() == EXPECTED_READS
    assert APPROVED_ACTION_TOOLS == write_tool_slugs() == EXPECTED_WRITES
    assert not READ_ONLY_TOOLS & APPROVED_ACTION_TOOLS


def test_every_approved_action_matches_exactly_one_write_capability() -> None:
    writes: dict[str, list] = {}
    for c in CAPABILITIES:
        if c.is_write:
            writes.setdefault(c.action_type, []).append(c)
    assert set(writes) == set(ACTIONS)
    for action_type, action in ACTIONS.items():
        main = [c for c in writes[action_type] if c.tool_slug == action.tool_slug]
        assert len(main) == 1 and main[0].toolkit == action.toolkit
        extra = [c for c in writes[action_type] if c.tool_slug != action.tool_slug]
        assert all(c.toolkit == action.toolkit for c in extra)
        assert len(extra) == (1 if action.follow_up_result_id else 0)  # only a completing step


def test_agent_roles_only_see_their_own_tools() -> None:
    prefixes = {
        AgentRole.INBOX: "GMAIL_",
        AgentRole.SCHEDULING: "GOOGLECALENDAR_",
        AgentRole.DATA: "GOOGLESHEETS_",
        AgentRole.RESEARCH: "COMPOSIO_SEARCH_",
        AgentRole.SOCIAL: ("LINKEDIN_", "FACEBOOK_", "INSTAGRAM_"),
        AgentRole.CRM: ("HUBSPOT_", "SALESFORCE_", "ZOHO_"),
        AgentRole.KNOWLEDGE: "NOTION_",
        AgentRole.DESIGN: "CANVA_",
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
    assert len(graph.edges) == 42


def test_publish_is_reachable_only_after_the_decision_node() -> None:
    graph = _graph()
    assert {e.source for e in graph.edges if e.target == "publish"} == {"decide"}
    assert {e.source for e in graph.edges if e.target == "decide"} == {"request_approval"}
