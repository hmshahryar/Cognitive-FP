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

CONNECTORS: tuple[ConnectorSpec, ...] = (
    ConnectorSpec("linkedin", "Social media", available=True, scope_labels=_LINKEDIN_SCOPES),
    ConnectorSpec("facebook", "Social media"),
    ConnectorSpec("instagram", "Social media"),
    ConnectorSpec("gmail", "Email"),
    ConnectorSpec("googlecalendar", "Calendar"),
    ConnectorSpec("hubspot", "CRM"),
    ConnectorSpec("salesforce", "CRM"),
    ConnectorSpec("zoho", "CRM"),
    ConnectorSpec("notion", "Docs & notes"),
    ConnectorSpec("googlesheets", "Docs & notes"),
    ConnectorSpec("slack", "Team chat"),
    ConnectorSpec("canva", "Design"),
)

_BY_SLUG = {c.slug: c for c in CONNECTORS}


def get_connector(slug: str) -> ConnectorSpec | None:
    return _BY_SLUG.get(slug)
