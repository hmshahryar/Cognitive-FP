"""The checkpointed state shared by every node.

Field names are part of the saved checkpoint format, so paused approvals depend on them.
"""

from typing import Any, Literal, TypedDict

Outcome = Literal[
    "", "unsupported", "awaiting", "approved", "published", "rejected", "changes", "cancelled",
    "failed",
]
TERMINAL_OUTCOMES = {"unsupported", "approved", "published", "rejected", "cancelled", "failed"}
NO_RESEARCH: dict[str, Any] = {"summary": "", "insights": []}


class GraphState(TypedDict, total=False):
    task_id: str
    execution_id: str
    user_id: str
    command: str
    mode: str  # post_as_is | write | research | model | gmail
    provided_text: str
    email: dict[str, Any]
    calendar: dict[str, Any]
    sheet: dict[str, Any]
    action_type: str
    action_payload: dict[str, Any]
    profile: str
    author_urn: str
    intent: dict[str, Any]
    findings: dict[str, Any]
    sources: list[dict[str, Any]]
    feedback: list[str]
    next_version: int
    draft_text: str
    draft_id: str
    approval_id: str
    outcome: Outcome
