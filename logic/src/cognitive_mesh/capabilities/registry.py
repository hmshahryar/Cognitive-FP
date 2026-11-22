"""The single list of external tools, who may use them, and how risky they are.

Agents depend on business capabilities such as `sheets.values.read`, not on provider tools.
Anything missing from this registry cannot be run by the integration gateway.

To add a tool:
1. Confirm its slug and arguments in the provider's current Composio schema.
2. Add one `Capability` below with the narrowest agent access.
3. For a write, also register its payload rules in `integrations/actions.py`.
"""

from dataclasses import dataclass
from enum import StrEnum


class RiskLevel(StrEnum):
    READ = "read"
    INTERNAL_WRITE = "internal_write"
    CUSTOMER_CONTACT = "customer_contact"
    PUBLIC = "public"
    SPEND = "spend"


class AgentRole(StrEnum):
    INBOX = "inbox"
    SCHEDULING = "scheduling"
    DATA = "data"
    RESEARCH = "research"
    SOCIAL = "social"


class CapabilityDeniedError(Exception):
    """An agent asked for a capability outside its role."""


@dataclass(frozen=True)
class Capability:
    name: str
    toolkit: str
    tool_slug: str
    risk: RiskLevel
    agents: frozenset[AgentRole]
    action_type: str | None = None  # approval action; required for every write

    def __post_init__(self) -> None:
        if self.is_write != (self.action_type is not None):
            raise ValueError(f"{self.name}: writes, and only writes, need an action_type")
        if not self.agents:
            raise ValueError(f"{self.name}: at least one agent role is required")

    @property
    def is_write(self) -> bool:
        return self.risk is not RiskLevel.READ


def _read(name: str, toolkit: str, tool_slug: str, agent: AgentRole) -> Capability:
    return Capability(name, toolkit, tool_slug, RiskLevel.READ, frozenset({agent}))


def _write(
    name: str, toolkit: str, tool_slug: str, risk: RiskLevel, agent: AgentRole, action_type: str
) -> Capability:
    return Capability(name, toolkit, tool_slug, risk, frozenset({agent}), action_type)


CAPABILITIES: tuple[Capability, ...] = (
    _read("social.linkedin.profile.read", "linkedin", "LINKEDIN_GET_MY_INFO", AgentRole.SOCIAL),
    _write(
        "social.linkedin.post.publish", "linkedin", "LINKEDIN_CREATE_LINKED_IN_POST",
        RiskLevel.PUBLIC, AgentRole.SOCIAL, "linkedin.publish_post",
    ),
    _read("research.news.search", "composio_search", "COMPOSIO_SEARCH_NEWS", AgentRole.RESEARCH),
    _read("research.web.search", "composio_search", "COMPOSIO_SEARCH_WEB", AgentRole.RESEARCH),
    _read(
        "research.page.read", "composio_search", "COMPOSIO_SEARCH_FETCH_URL_CONTENT",
        AgentRole.RESEARCH,
    ),
    _read("email.profile.read", "gmail", "GMAIL_GET_PROFILE", AgentRole.INBOX),
    _read("email.messages.search", "gmail", "GMAIL_FETCH_EMAILS", AgentRole.INBOX),
    _write(
        "email.message.send", "gmail", "GMAIL_SEND_EMAIL",
        RiskLevel.CUSTOMER_CONTACT, AgentRole.INBOX, "gmail.send_email",
    ),
    _write(
        "email.message.forward", "gmail", "GMAIL_FORWARD_MESSAGE",
        RiskLevel.CUSTOMER_CONTACT, AgentRole.INBOX, "gmail.forward_email",
    ),
    _write(
        "email.message.trash", "gmail", "GMAIL_MOVE_TO_TRASH",
        RiskLevel.INTERNAL_WRITE, AgentRole.INBOX, "gmail.trash_email",
    ),
    _read(
        "calendar.events.list", "googlecalendar", "GOOGLECALENDAR_EVENTS_LIST",
        AgentRole.SCHEDULING,
    ),
    _write(
        "calendar.event.create", "googlecalendar", "GOOGLECALENDAR_CREATE_EVENT",
        RiskLevel.CUSTOMER_CONTACT, AgentRole.SCHEDULING, "calendar.create_event",
    ),
    _write(
        "calendar.event.reschedule", "googlecalendar", "GOOGLECALENDAR_PATCH_EVENT",
        RiskLevel.CUSTOMER_CONTACT, AgentRole.SCHEDULING, "calendar.reschedule_event",
    ),
    _write(
        "calendar.event.cancel", "googlecalendar", "GOOGLECALENDAR_DELETE_EVENT",
        RiskLevel.CUSTOMER_CONTACT, AgentRole.SCHEDULING, "calendar.cancel_event",
    ),
    _read("sheets.files.search", "googlesheets", "GOOGLESHEETS_SEARCH_SPREADSHEETS", AgentRole.DATA),
    _read("sheets.tabs.read", "googlesheets", "GOOGLESHEETS_GET_SHEET_NAMES", AgentRole.DATA),
    _read("sheets.values.read", "googlesheets", "GOOGLESHEETS_VALUES_GET", AgentRole.DATA),
    _write(
        "sheets.file.create", "googlesheets", "GOOGLESHEETS_CREATE_GOOGLE_SHEET1",
        RiskLevel.INTERNAL_WRITE, AgentRole.DATA, "sheets.create_spreadsheet",
    ),
    _write(
        "sheets.rows.append", "googlesheets", "GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND",
        RiskLevel.INTERNAL_WRITE, AgentRole.DATA, "sheets.append_rows",
    ),
    _write(
        "sheets.cell.update", "googlesheets", "GOOGLESHEETS_VALUES_UPDATE",
        RiskLevel.INTERNAL_WRITE, AgentRole.DATA, "sheets.update_cell",
    ),
)

_BY_NAME = {capability.name: capability for capability in CAPABILITIES}
if len(_BY_NAME) != len(CAPABILITIES):
    raise ValueError("Capability names must be unique")
if len({capability.tool_slug for capability in CAPABILITIES}) != len(CAPABILITIES):
    raise ValueError("A provider tool can be registered only once")


def get_capability(name: str) -> Capability | None:
    return _BY_NAME.get(name)


def capabilities_for(agent: AgentRole) -> tuple[Capability, ...]:
    return tuple(capability for capability in CAPABILITIES if agent in capability.agents)


def authorize(agent: AgentRole, name: str) -> Capability:
    """The capability, if this agent role may use it. Unknown names are denied too."""
    capability = _BY_NAME.get(name)
    if capability is None or agent not in capability.agents:
        raise CapabilityDeniedError(f"{agent} may not use {name}")
    return capability


def read_tool_slugs() -> frozenset[str]:
    return frozenset(c.tool_slug for c in CAPABILITIES if not c.is_write)


def write_tool_slugs() -> frozenset[str]:
    return frozenset(c.tool_slug for c in CAPABILITIES if c.is_write)
