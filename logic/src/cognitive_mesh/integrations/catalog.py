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

CONNECTORS: tuple[ConnectorSpec, ...] = (
    ConnectorSpec("linkedin", "Social media", available=True, scope_labels=_LINKEDIN_SCOPES),
    ConnectorSpec("facebook", "Social media"),
    ConnectorSpec("instagram", "Social media"),
    ConnectorSpec("gmail", "Email", available=True, scope_labels=_GMAIL_SCOPES),
    ConnectorSpec("googlecalendar", "Calendar", available=True, scope_labels=_CALENDAR_SCOPES),
    ConnectorSpec("hubspot", "CRM"),
    ConnectorSpec("salesforce", "CRM"),
    ConnectorSpec("zoho", "CRM"),
    ConnectorSpec("notion", "Docs & notes"),
    ConnectorSpec("googlesheets", "Docs & notes", available=True, scope_labels=_SHEETS_SCOPES),
    ConnectorSpec("slack", "Team chat"),
    ConnectorSpec("canva", "Design"),
)

_BY_SLUG = {c.slug: c for c in CONNECTORS}


def get_connector(slug: str) -> ConnectorSpec | None:
    return _BY_SLUG.get(slug)
