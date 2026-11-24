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
    # Creates something private in the user's own account (a Canva draft) that nobody else sees
    # and the user can delete. It runs without approval; whatever is later published from it
    # still needs approval.
    PRIVATE_DRAFT = "private_draft"
    SPEND = "spend"


class AgentRole(StrEnum):
    INBOX = "inbox"
    SCHEDULING = "scheduling"
    DATA = "data"
    RESEARCH = "research"
    SOCIAL = "social"
    CRM = "crm"
    KNOWLEDGE = "knowledge"
    DESIGN = "design"


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
        """An external change that needs approval."""
        return self.risk not in (RiskLevel.READ, RiskLevel.PRIVATE_DRAFT)


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
    _read(
        "social.facebook.pages.list", "facebook", "FACEBOOK_LIST_MANAGED_PAGES", AgentRole.SOCIAL
    ),
    _write(
        "social.facebook.post.publish", "facebook", "FACEBOOK_CREATE_POST",
        RiskLevel.PUBLIC, AgentRole.SOCIAL, "facebook.publish_post",
    ),
    _write(
        "social.facebook.photo.publish", "facebook", "FACEBOOK_CREATE_PHOTO_POST",
        RiskLevel.PUBLIC, AgentRole.SOCIAL, "facebook.publish_photo",
    ),
    _read("social.instagram.account.read", "instagram", "INSTAGRAM_GET_USER_INFO", AgentRole.SOCIAL),
    _read(
        "social.instagram.quota.read", "instagram",
        "INSTAGRAM_GET_IG_USER_CONTENT_PUBLISHING_LIMIT", AgentRole.SOCIAL,
    ),
    # Second step of the same approved action; it is listed first so the container step
    # below stays the action's registered tool.
    _write(
        "social.instagram.media.publish", "instagram", "INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH",
        RiskLevel.PUBLIC, AgentRole.SOCIAL, "instagram.publish_photo",
    ),
    _write(
        "social.instagram.media.create", "instagram", "INSTAGRAM_POST_IG_USER_MEDIA",
        RiskLevel.PUBLIC, AgentRole.SOCIAL, "instagram.publish_photo",
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
    _read("crm.contacts.search", "hubspot", "HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA", AgentRole.CRM),
    _write(
        "crm.contact.create", "hubspot", "HUBSPOT_CREATE_CONTACT",
        RiskLevel.INTERNAL_WRITE, AgentRole.CRM, "crm.create_contact",
    ),
    _write(
        "crm.contact.update", "hubspot", "HUBSPOT_UPDATE_CONTACT",
        RiskLevel.INTERNAL_WRITE, AgentRole.CRM, "crm.update_contact",
    ),
    _write(
        "crm.note.create", "hubspot", "HUBSPOT_CREATE_NOTE",
        RiskLevel.INTERNAL_WRITE, AgentRole.CRM, "crm.create_note",
    ),
    _read(
        "crm.salesforce.contacts.search", "salesforce", "SALESFORCE_SEARCH_CONTACTS", AgentRole.CRM
    ),
    _read(
        "crm.salesforce.accounts.search", "salesforce", "SALESFORCE_SEARCH_ACCOUNTS", AgentRole.CRM
    ),
    _write(
        "crm.salesforce.contact.create", "salesforce", "SALESFORCE_CREATE_CONTACT",
        RiskLevel.INTERNAL_WRITE, AgentRole.CRM, "crm.salesforce.create_contact",
    ),
    _write(
        "crm.salesforce.contact.update", "salesforce", "SALESFORCE_UPDATE_CONTACT",
        RiskLevel.INTERNAL_WRITE, AgentRole.CRM, "crm.salesforce.update_contact",
    ),
    _write(
        "crm.salesforce.note.create", "salesforce", "SALESFORCE_CREATE_NOTE",
        RiskLevel.INTERNAL_WRITE, AgentRole.CRM, "crm.salesforce.create_note",
    ),
    _read("crm.zoho.contacts.search", "zoho", "ZOHO_SEARCH_CONTACTS", AgentRole.CRM),
    _write(
        "crm.zoho.contact.create", "zoho", "ZOHO_CREATE_CONTACT",
        RiskLevel.INTERNAL_WRITE, AgentRole.CRM, "crm.zoho.create_contact",
    ),
    _write(
        "crm.zoho.contact.update", "zoho", "ZOHO_UPDATE_ZOHO_RECORD",
        RiskLevel.INTERNAL_WRITE, AgentRole.CRM, "crm.zoho.update_contact",
    ),
    _write(
        "crm.zoho.note.create", "zoho", "ZOHO_CREATE_NOTE",
        RiskLevel.INTERNAL_WRITE, AgentRole.CRM, "crm.zoho.create_note",
    ),
    _read("design.canva.capabilities.read", "canva", "CANVA_GET_USERS_ME_CAPABILITIES", AgentRole.DESIGN),
    _read("design.canva.upload.status", "canva", "CANVA_GET_URL_ASSET_UPLOADS_JOBID", AgentRole.DESIGN),
    _read("design.canva.export.status", "canva", "CANVA_GET_DESIGN_EXPORT_JOB_RESULT", AgentRole.DESIGN),
    Capability(
        "design.canva.asset.upload", "canva", "CANVA_CREATE_URL_ASSET_UPLOAD_JOB",
        RiskLevel.PRIVATE_DRAFT, frozenset({AgentRole.DESIGN}),
    ),
    Capability(
        "design.canva.design.create", "canva", "CANVA_POST_DESIGNS",
        RiskLevel.PRIVATE_DRAFT, frozenset({AgentRole.DESIGN}),
    ),
    Capability(
        "design.canva.design.export", "canva", "CANVA_POST_EXPORTS",
        RiskLevel.PRIVATE_DRAFT, frozenset({AgentRole.DESIGN}),
    ),
    _read("notion.pages.search", "notion", "NOTION_SEARCH_NOTION_PAGE", AgentRole.KNOWLEDGE),
    _read("notion.page.metadata", "notion", "NOTION_RETRIEVE_PAGE", AgentRole.KNOWLEDGE),
    _read("notion.page.read", "notion", "NOTION_GET_PAGE_MARKDOWN", AgentRole.KNOWLEDGE),
    _write(
        "notion.page.create", "notion", "NOTION_CREATE_NOTION_PAGE",
        RiskLevel.INTERNAL_WRITE, AgentRole.KNOWLEDGE, "notion.create_page",
    ),
    _write(
        "notion.page.append", "notion", "NOTION_ADD_MULTIPLE_PAGE_CONTENT",
        RiskLevel.INTERNAL_WRITE, AgentRole.KNOWLEDGE, "notion.append_content",
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
    return frozenset(c.tool_slug for c in CAPABILITIES if c.risk is RiskLevel.READ)


def draft_tool_slugs() -> frozenset[str]:
    return frozenset(c.tool_slug for c in CAPABILITIES if c.risk is RiskLevel.PRIVATE_DRAFT)


def write_tool_slugs() -> frozenset[str]:
    return frozenset(c.tool_slug for c in CAPABILITIES if c.is_write)
