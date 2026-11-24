"""Notion reads and exact change preparation. Uses no AI and changes nothing.

Page content is untrusted data. It is shown to the user and never followed as instructions.
"""

from dataclasses import dataclass
from typing import Any

from cognitive_mesh.integrations import notion
from cognitive_mesh.integrations.gateway import (
    IntegrationGateway,
    IntegrationUnavailableError,
    ToolExecutionError,
)
from cognitive_mesh.workflows.notion_commands import NotionCommand

MAX_LISTED_PAGES = 10
MAX_READ_CHARS = 4000
_UNREACHABLE = "I couldn't reach Notion, so nothing was read or changed. Please try again."
_NOT_SHARED = (
    "I couldn't find a Notion page titled {name}. Check the exact title, and make sure the page "
    "is shared with this app in Notion."
)


class NotionStop(Exception):
    """Stop safely and tell the user why."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PreparedNotionChange:
    action_type: str
    payload: dict[str, Any]
    text: str


async def _read(gateway: IntegrationGateway, user_id: str, tool: str, args: dict[str, Any]) -> Any:
    try:
        return await gateway.execute_read_only(
            user_id=user_id, toolkit_slug=notion.TOOLKIT, tool_slug=tool, arguments=args
        )
    except (IntegrationUnavailableError, ToolExecutionError) as exc:
        raise NotionStop(_UNREACHABLE) from exc


async def _search(gateway: IntegrationGateway, user_id: str, query: str) -> list[dict[str, Any]]:
    data = await _read(gateway, user_id, notion.SEARCH_TOOL, notion.search_arguments(query))
    try:
        return notion.pages_from(data)
    except notion.InvalidNotionActionError as exc:
        raise NotionStop(
            f"{exc}, so I stopped. Send the page's Notion link to choose it exactly."
        ) from exc


def _label(page: dict[str, Any]) -> str:
    title = page["title"] or "Untitled"
    return f"{title} ({page['url']})" if page["url"] else title


async def _resolve(
    gateway: IntegrationGateway, user_id: str, name: str, *, allow_only_result: bool = False
) -> dict[str, Any]:
    """One exact page. A link is exact. A title with several matches is never guessed."""
    page_id = notion.page_id_in(name)
    if page_id:
        data = await _read(gateway, user_id, notion.PAGE_TOOL, {"page_id": page_id})
        try:
            page = notion.page_from(data)
        except notion.InvalidNotionActionError as exc:
            raise NotionStop("I couldn't read that Notion page, so I stopped.") from exc
        if page is None:
            raise NotionStop("That Notion page is in the trash, so I stopped.")
        return page
    pages = await _search(gateway, user_id, name)
    exact = [p for p in pages if p["title"].casefold() == name.casefold()]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        listed = "\n".join(f"- {_label(p)}" for p in exact[:MAX_LISTED_PAGES])
        raise NotionStop(f"Several Notion pages are titled {name}. Send the link to one:\n{listed}")
    if allow_only_result and len(pages) == 1:
        return pages[0]
    if pages:
        listed = "\n".join(f"- {_label(p)}" for p in pages[:MAX_LISTED_PAGES])
        raise NotionStop(f"No page is titled exactly {name}. Did you mean one of these?\n{listed}")
    raise NotionStop(_NOT_SHARED.format(name=name))


async def answer_find(gateway: IntegrationGateway, user_id: str, command: NotionCommand) -> str:
    query = command.query or ""
    pages = await _search(gateway, user_id, query)
    if not pages:
        if query:
            return _NOT_SHARED.format(name=query)
        return "I can't see any Notion pages yet. Share a page with this app in Notion first."
    lines = "\n".join(f"- {_label(p)}" for p in pages[:MAX_LISTED_PAGES])
    more = f"\n…and {len(pages) - MAX_LISTED_PAGES} more." if len(pages) > MAX_LISTED_PAGES else ""
    return f"Notion pages I found:\n{lines}{more}"


async def answer_read(gateway: IntegrationGateway, user_id: str, command: NotionCommand) -> str:
    assert command.page is not None
    page = await _resolve(gateway, user_id, command.page, allow_only_result=True)
    data = await _read(gateway, user_id, notion.READ_TOOL, {"page_id": page["id"]})
    try:
        text, truncated = notion.markdown_from(data)
    except notion.InvalidNotionActionError as exc:
        raise NotionStop("I couldn't read that Notion page's content.") from exc
    shortened = truncated or len(text) > MAX_READ_CHARS
    title = page["title"] or "Untitled"
    if not text.strip():
        return f"The Notion page {title} is empty."
    ending = "\n\n(Showing the first part of this page.)" if shortened else ""
    return f"{title}\n\n{text[:MAX_READ_CHARS].strip()}{ending}"


async def prepare_change(
    gateway: IntegrationGateway, user_id: str, command: NotionCommand
) -> PreparedNotionChange:
    if command.kind == "create":
        assert command.parent is not None and command.title is not None
        parent = await _resolve(gateway, user_id, command.parent)
        same_title = await _search(gateway, user_id, command.title)
        if any(
            p["parent_id"] == parent["id"] and p["title"].casefold() == command.title.casefold()
            for p in same_title
        ):
            raise NotionStop(
                f"{parent['title'] or 'That page'} already has a page titled {command.title}, "
                "so I didn't create a duplicate. To add to it, say: add to Notion page "
                f"{command.title}: your text."
            )
        payload = notion.build_create_payload(
            parent={"id": parent["id"], "title": parent["title"]},
            title=command.title,
            body=command.body or "",
        )
        return PreparedNotionChange(
            notion.CREATE_ACTION, payload, f"{command.title}\n\n{command.body or ''}".strip()
        )
    assert command.page is not None and command.body is not None
    page = await _resolve(gateway, user_id, command.page)
    payload = notion.build_append_payload(
        page={"id": page["id"], "title": page["title"]}, body=command.body
    )
    return PreparedNotionChange(notion.APPEND_ACTION, payload, command.body)
