"""Which Composio toolkits the Connector Hub offers.

Slugs were verified against the live Composio catalog (all use Composio-managed
OAuth2). Names and logos are NOT hardcoded: they come from Composio at runtime.
Flip `available` to True when an app's AI-employee workflow is implemented.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ConnectorSpec:
    slug: str
    category: str
    available: bool = False
    # Plain-language meaning of each OAuth scope, shown to the user as permissions.
    scope_labels: tuple[tuple[str, str], ...] = ()
    # When set, users can connect only through an auth config limited to these scopes.
    allowed_scopes: tuple[str, ...] = ()
    # The app creates this Composio-managed config itself, so users never handle keys.
    limited_config_name: str = ""

    def describe_scopes(self, scopes: tuple[str, ...]) -> list[str]:
        labels = dict(self.scope_labels)
        return [labels.get(s, s) for s in scopes]


# LinkedIn scope meanings per LinkedIn's OAuth documentation.
_LINKEDIN_SCOPES = (
    ("w_member_social", "Create, edit and delete posts, comments and reactions as you"),
    ("profile", "Read your name, headline and photo"),
    ("email", "Read your primary email address"),
    ("openid", "Sign in with your LinkedIn identity"),
)

# Gmail scope meanings per Google's OAuth scope documentation.
_GMAIL_SCOPES = (
    ("https://mail.google.com/", "Full access to read, send, delete and manage your email"),
    ("https://www.googleapis.com/auth/gmail.modify", "Read, compose, send and organize email"),
    ("https://www.googleapis.com/auth/gmail.compose", "Create drafts and send email"),
    ("https://www.googleapis.com/auth/gmail.send", "Send email as you"),
    ("https://www.googleapis.com/auth/gmail.readonly", "Read your email and settings"),
    ("https://www.googleapis.com/auth/userinfo.email", "Read your email address"),
    ("https://www.googleapis.com/auth/userinfo.profile", "Read your basic profile"),
)

# Google Calendar scope meanings per Google's OAuth scope documentation.
_CALENDAR_SCOPES = (
    ("https://www.googleapis.com/auth/calendar", "See, edit, share and delete your calendars"),
    ("https://www.googleapis.com/auth/calendar.events", "View and edit events on your calendars"),
    ("https://www.googleapis.com/auth/calendar.events.owned", "Manage events you own"),
    ("https://www.googleapis.com/auth/calendar.readonly", "View your calendars"),
    ("https://www.googleapis.com/auth/calendar.events.readonly", "View your calendar events"),
    ("https://www.googleapis.com/auth/calendar.settings.readonly", "View your calendar settings"),
    ("https://www.googleapis.com/auth/userinfo.email", "Read your email address"),
    ("https://www.googleapis.com/auth/userinfo.profile", "Read your basic profile"),
)

# Google Sheets scope meanings per Google's OAuth scope documentation.
_SHEETS_SCOPES = (
    ("https://www.googleapis.com/auth/spreadsheets", "See, edit, create and delete spreadsheets"),
    ("https://www.googleapis.com/auth/drive", "See, edit, create and delete your Drive files"),
    ("https://www.googleapis.com/auth/drive.file", "Manage Drive files used with this app"),
    ("https://www.googleapis.com/auth/userinfo.email", "Read your email address"),
)

# HubSpot scope meanings per HubSpot's scopes reference.
_HUBSPOT_SCOPES = (
    ("oauth", "Connect to your HubSpot account"),
    ("crm.objects.contacts.read", "View your HubSpot contacts"),
    ("crm.objects.contacts.write", "Create and update contacts and contact notes"),
)

# Salesforce scope meanings per Salesforce's OAuth scope documentation. `api` cannot be limited to
# one object, so the app's capability allowlist restricts it to contact actions.
_SALESFORCE_SCOPES = (
    ("api", "Read and change Salesforce data your user can access (this app uses contacts only)"),
    ("refresh_token", "Stay connected until you disconnect"),
)

# Zoho scope meanings per Zoho CRM API v8 docs (search, insert, update and create-note pages).
_ZOHO_SCOPES = (
    ("ZohoCRM.modules.contacts.READ", "View your Zoho CRM contacts"),
    ("ZohoCRM.modules.contacts.CREATE", "Add contacts"),
    ("ZohoCRM.modules.contacts.UPDATE", "Update contacts"),
    ("ZohoCRM.modules.notes.CREATE", "Add notes to contacts"),
    ("ZohoSearch.securesearch.READ", "Search the contacts you can access"),
)

# Facebook Page scope meanings per Meta's permissions reference. Posting needs all three.
_FACEBOOK_SCOPES = (
    ("pages_show_list", "See the list of Pages you manage"),
    ("pages_read_engagement", "Read your Pages' details and posts"),
    ("pages_manage_posts", "Create posts on Pages you manage"),
)

# Instagram API with Instagram Login scope meanings per Meta's content publishing docs.
_INSTAGRAM_SCOPES = (
    ("instagram_business_basic", "Read your professional account's name and ID"),
    ("instagram_business_content_publish", "Publish posts to your account"),
)

# Canva scope meanings per Canva's Connect API scopes. No comment or folder access is requested.
_CANVA_SCOPES = (
    ("profile:read", "Read your Canva profile and which features your plan includes"),
    ("design:meta:read", "See your designs' names and links"),
    ("design:content:read", "Export your designs as images"),
    ("design:content:write", "Create designs"),
    ("asset:read", "See images you uploaded"),
    ("asset:write", "Upload images to your Canva uploads"),
    ("brandtemplate:meta:read", "See your brand templates"),
    ("brandtemplate:content:read", "Read the fields of your brand templates"),
)

CONNECTORS: tuple[ConnectorSpec, ...] = (
    ConnectorSpec("linkedin", "Social media", available=True, scope_labels=_LINKEDIN_SCOPES),
    ConnectorSpec(
        "facebook", "Social media", available=True, scope_labels=_FACEBOOK_SCOPES,
        allowed_scopes=tuple(scope for scope, _label in _FACEBOOK_SCOPES),
        limited_config_name="Cognitive Mesh Facebook Pages (limited)",
    ),
    ConnectorSpec(
        "instagram", "Social media", available=True, scope_labels=_INSTAGRAM_SCOPES,
        allowed_scopes=tuple(scope for scope, _label in _INSTAGRAM_SCOPES),
        limited_config_name="Cognitive Mesh Instagram (limited)",
    ),
    ConnectorSpec("gmail", "Email", available=True, scope_labels=_GMAIL_SCOPES),
    ConnectorSpec("googlecalendar", "Calendar", available=True, scope_labels=_CALENDAR_SCOPES),
    ConnectorSpec(
        "hubspot", "CRM", available=True, scope_labels=_HUBSPOT_SCOPES,
        allowed_scopes=tuple(scope for scope, _label in _HUBSPOT_SCOPES),
        limited_config_name="Cognitive Mesh HubSpot (limited)",
    ),
    ConnectorSpec(
        "salesforce", "CRM", available=True, scope_labels=_SALESFORCE_SCOPES,
        allowed_scopes=tuple(scope for scope, _label in _SALESFORCE_SCOPES),
        limited_config_name="Cognitive Mesh Salesforce (limited)",
    ),
    ConnectorSpec(
        "zoho", "CRM", available=True, scope_labels=_ZOHO_SCOPES,
        allowed_scopes=tuple(scope for scope, _label in _ZOHO_SCOPES),
        limited_config_name="Cognitive Mesh Zoho CRM (limited)",
    ),
    ConnectorSpec("notion", "Docs & notes", available=True),  # no OAuth scopes; user picks pages
    ConnectorSpec("googlesheets", "Docs & notes", available=True, scope_labels=_SHEETS_SCOPES),
    ConnectorSpec("slack", "Team chat"),
    ConnectorSpec(
        "canva", "Design", available=True, scope_labels=_CANVA_SCOPES,
        allowed_scopes=tuple(scope for scope, _label in _CANVA_SCOPES),
        limited_config_name="Cognitive Mesh Canva (limited)",
    ),
)

_BY_SLUG = {c.slug: c for c in CONNECTORS}


def get_connector(slug: str) -> ConnectorSpec | None:
    return _BY_SLUG.get(slug)
