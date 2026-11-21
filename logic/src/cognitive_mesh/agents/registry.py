"""Registry of AI employees: who exists, what they do, which tools they may use.

New employees (Email, Calendar, CRM, ...) are added here and wired into a workflow;
the read-only tool allowlist itself lives in integrations/gateway.py.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EmployeeSpec:
    key: str
    title: str
    responsibility: str
    tools: tuple[str, ...] = ()
    can_write_externally: bool = False  # always False: writes go through approval


EMPLOYEES: dict[str, EmployeeSpec] = {
    spec.key: spec
    for spec in (
        EmployeeSpec("manager", "Manager", "Understands the command and runs the workflow."),
        EmployeeSpec(
            "research",
            "Research employee",
            "Plans and runs web/news research; returns cited findings.",
            tools=("COMPOSIO_SEARCH_NEWS", "COMPOSIO_SEARCH_WEB"),
        ),
        EmployeeSpec(
            "social_media",
            "Social Media employee",
            "Reads the author's LinkedIn profile to match their niche.",
            tools=("LINKEDIN_GET_MY_INFO",),
        ),
        EmployeeSpec("content", "Content employee", "Writes the post from research only."),
        EmployeeSpec(
            "review",
            "Review employee",
            "Fact-checks cited sources and reviews writing quality.",
            tools=("COMPOSIO_SEARCH_FETCH_URL_CONTENT",),
        ),
    )
}
