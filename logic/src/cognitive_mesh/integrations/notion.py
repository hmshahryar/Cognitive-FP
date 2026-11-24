"""The exact Notion actions that can be requested, and their payload rules.

Tool slugs and inputs come from Composio's Notion toolkit docs (version 20261116), and response
shapes from Notion's API reference. Notion has capabilities instead of OAuth scopes, and each user
chooses which pages the app can reach when connecting. Targets are always page UUIDs, because the
create tool would otherwise accept a title and could pick the wrong page. Deleting is not offered.
"""

import re
import uuid
from typing import Any

TOOLKIT = "notion"
CREATE_ACTION = "notion.create_page"
APPEND_ACTION = "notion.append_content"
SEARCH_TOOL = "NOTION_SEARCH_NOTION_PAGE"
PAGE_TOOL = "NOTION_RETRIEVE_PAGE"
READ_TOOL = "NOTION_GET_PAGE_MARKDOWN"
CREATE_TOOL = "NOTION_CREATE_NOTION_PAGE"
APPEND_TOOL = "NOTION_ADD_MULTIPLE_PAGE_CONTENT"
SEARCH_LIMIT = 100
MAX_TITLE_CHARS = 200
MAX_BODY_CHARS = 8000
MAX_BLOCK_CHARS = 2000  # Notion's limit for one text object
MAX_BLOCKS = 100  # the append tool's limit

_PAGE_URL = re.compile(
    r"notion\.(?:so|site)/\S*?([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})",
    re.IGNORECASE,
)


class InvalidNotionActionError(ValueError):
    """The requested Notion action does not meet the approved rules."""


def page_id_in(text: str) -> str | None:
    """The page id in a Notion link, in canonical form."""
    match = _PAGE_URL.search(text)
    return str(uuid.UUID(match.group(1))) if match else None


def _canonical(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


def _check_id(value: Any) -> None:
    if not isinstance(value, str) or _canonical(value) != value:
        raise InvalidNotionActionError("A valid Notion page id is required")


def _check_target(target: Any) -> None:
    if not isinstance(target, dict) or set(target) != {"id", "title"}:
        raise InvalidNotionActionError("A Notion page is required")
    _check_id(target["id"])
    if not isinstance(target["title"], str):
        raise InvalidNotionActionError("The Notion page title is not valid")


def _check_body(body: Any, *, required: bool) -> None:
    if not isinstance(body, str) or len(body) > MAX_BODY_CHARS:
        raise InvalidNotionActionError(f"The content must be under {MAX_BODY_CHARS} characters")
    if required and not body.strip():
        raise InvalidNotionActionError("The content is empty")


def _blocks(body: str) -> list[dict[str, str]]:
    """One paragraph block per line, split to fit Notion's text limit without losing text."""
    blocks = [
        {"content": line[start : start + MAX_BLOCK_CHARS], "block_property": "paragraph"}
        for line in (raw.strip() for raw in body.splitlines())
        if line
        for start in range(0, len(line), MAX_BLOCK_CHARS)
    ]
    if len(blocks) > MAX_BLOCKS:
        raise InvalidNotionActionError(f"The content can have at most {MAX_BLOCKS} lines")
    return blocks


def _create_arguments(parent_id: str, title: str, body: str) -> dict[str, Any]:
    arguments: dict[str, Any] = {"parent_id": parent_id, "title": title}
    if body:
        arguments["markdown"] = body
    return arguments


def validate_create(payload: dict[str, Any]) -> None:
    if set(payload) != {"parent", "title", "body", "arguments"}:
        raise InvalidNotionActionError("Unexpected page fields")
    _check_target(payload["parent"])
    title = payload["title"]
    if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE_CHARS:
        raise InvalidNotionActionError(f"The title must be under {MAX_TITLE_CHARS} characters")
    _check_body(payload["body"], required=False)
    expected = _create_arguments(payload["parent"]["id"], title, payload["body"])
    if payload["arguments"] != expected:
        raise InvalidNotionActionError("The write must match the approved page")


def validate_append(payload: dict[str, Any]) -> None:
    if set(payload) != {"page", "body", "arguments"}:
        raise InvalidNotionActionError("Unexpected content fields")
    _check_target(payload["page"])
    _check_body(payload["body"], required=True)
    expected = {"parent_block_id": payload["page"]["id"], "content_blocks": _blocks(payload["body"])}
    if payload["arguments"] != expected:
        raise InvalidNotionActionError("The write must match the approved content")


def build_create_payload(*, parent: dict[str, str], title: str, body: str) -> dict[str, Any]:
    payload = {
        "parent": parent, "title": title, "body": body,
        "arguments": _create_arguments(parent["id"], title, body),
    }
    validate_create(payload)
    return payload


def build_append_payload(*, page: dict[str, str], body: str) -> dict[str, Any]:
    payload = {
        "page": page, "body": body,
        "arguments": {"parent_block_id": page["id"], "content_blocks": _blocks(body)},
    }
    validate_append(payload)
    return payload


def search_arguments(query: str) -> dict[str, Any]:
    return {"query": query, "filter_value": "page", "page_size": SEARCH_LIMIT}


def _unwrap(data: Any) -> Any:
    for key in ("response_data", "data"):
        if isinstance(data, dict) and isinstance(data.get(key), dict):
            return data[key]
    return data


def _title(row: dict[str, Any]) -> str:
    properties = row.get("properties")
    if not isinstance(properties, dict):
        return ""
    for value in properties.values():
        if isinstance(value, dict) and value.get("type") == "title":
            parts = value.get("title")
            if isinstance(parts, list):
                return "".join(
                    str(part.get("plain_text") or "") for part in parts if isinstance(part, dict)
                ).strip()
    return ""


def _page(row: Any) -> dict[str, Any] | None:
    """A live page, or None for a page in the trash. Unreadable rows raise."""
    page_id = _canonical(row.get("id")) if isinstance(row, dict) else None
    if page_id is None or row.get("object") != "page":
        raise InvalidNotionActionError("Notion returned a page I couldn't read")
    if row.get("in_trash") or row.get("is_archived") or row.get("archived"):
        return None
    parent = row.get("parent") if isinstance(row.get("parent"), dict) else {}
    url = row.get("url")
    return {
        "id": page_id,
        "title": _title(row),
        "parent_id": _canonical(parent.get("page_id")) if parent.get("page_id") else None,
        "url": url if isinstance(url, str) and url.startswith("https://www.notion.so/") else None,
    }


def page_from(data: Any) -> dict[str, Any] | None:
    return _page(_unwrap(data))


def pages_from(data: Any) -> list[dict[str, Any]]:
    """Fails closed: an unreadable or partial result must never look like "no match"."""
    body = _unwrap(data)
    rows = body.get("results") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise InvalidNotionActionError("Notion returned a search result I couldn't read")
    if body.get("has_more") is True:
        raise InvalidNotionActionError("Notion returned too many pages to check safely")
    return [page for row in rows if (page := _page(row)) is not None]


def markdown_from(data: Any) -> tuple[str, bool]:
    """(page text, whether Notion cut it short)."""
    body = _unwrap(data)
    text = body.get("markdown") if isinstance(body, dict) else None
    if not isinstance(text, str):
        raise InvalidNotionActionError("Notion returned page content I couldn't read")
    return text, body.get("truncated") is True


def create_result_id(data: Any) -> str | None:
    body = _unwrap(data)
    if not isinstance(body, dict) or body.get("object", "page") != "page":
        return None
    return _canonical(body.get("id")) if body.get("id") else None


def create_result_url(data: Any, _page_id: str | None) -> str | None:
    body = _unwrap(data)
    url = body.get("url") if isinstance(body, dict) else None
    return url if isinstance(url, str) and url.startswith("https://www.notion.so/") else None


def append_result_id(data: Any) -> str | None:
    """The first new block's id, only when Notion lists the blocks it added."""
    body = _unwrap(data)
    rows = body.get("results") if isinstance(body, dict) else None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    return _canonical(rows[0].get("id")) if rows[0].get("id") else None


def no_url(_data: Any, _block_id: str | None) -> str | None:
    return None
