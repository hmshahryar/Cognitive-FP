"""Understand Notion commands with plain code.

Page names, titles and content are taken exactly from the user's words. Anything missing becomes
one question, and the reply continues the same command. Only the instruction part of a message can
select Notion, so a message body that mentions Notion never does.
"""

import re
from dataclasses import dataclass, replace
from typing import Any, Literal

Kind = Literal["find", "read", "create", "append", "help"]
Missing = Literal["query", "page", "title", "parent", "body"]

_ARTICLES = r"(?:(?:a|an|new|the|my|our)\s+)*"
_IN_NOTION = re.compile(
    r"\b(?:in|into|to|on|from)\s+(?:(?:my|the|our)\s+)?notion(?:\s+workspace)?\b(?!\s+pages?\b)",
    re.IGNORECASE,
)
_NAMES_NOTION = re.compile(
    r"\b(?:in|into|to|on|from)\s+(?:(?:my|the|our)\s+)?notion\b"
    r"|\bnotion\s+(?:pages?|docs?|documents?|notes?|workspace)\b"
    r"|^\s*(?:please\s+)?search\s+(?:(?:my|the|our)\s+)?notion\b",
    re.IGNORECASE,
)
_OTHER_APP = re.compile(
    r"\b(?:e-?mail|gmail|mail|send|forward|calendar|meeting\s+at|sheets?|spreadsheet|linkedin"
    r"|facebook|instagram|hub\s?spot|sales\s?force|zoho|crm)\b",
    re.IGNORECASE,
)
_BODY = re.compile(
    r":(?!//)|\s+(?:with\s+(?:the\s+)?(?:content|text|body)|saying)\b\s*:?\s*", re.IGNORECASE
)
_SEARCH_NOTION = re.compile(
    r"^\s*(?:please\s+)?search\s+(?:(?:my|the|our)\s+)?notion\b\s*(?:for\s+)?(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_APPEND = re.compile(
    r"^\s*(?:please\s+)?(?:add|append|write|save)\s+"
    r"(?:(?:this|that|a|an|the|following|my|some|note|text|content|line)\s+)*"
    r"(?:to|into|on)\s+(?P<target>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_CREATE = re.compile(
    rf"^\s*(?:please\s+)?(?:create|make|add|write|start)\s+{_ARTICLES}(?:notion\s+)?"
    r"(?:page|doc|document|note)\b(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_FIND = re.compile(
    r"^\s*(?:please\s+)?(?:find|search(?:\s+for)?|look\s*up|list)\s+(?:me\s+)?(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_READ = re.compile(
    r"^\s*(?:please\s+)?(?:read|open|show|get|view)\s+(?:me\s+)?(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_ALL_PAGES = re.compile(
    r"^(?:(?:all|my|the|our)\s+)*(?:notion\s+)?(?:pages?|docs?|documents?)?$", re.IGNORECASE
)
_LEAD_PARENT = re.compile(
    r"^(?:in|to|under|inside|within)\s+(?P<parent>.+?)(?=\s+(?:titled|called|named)\b|$)",
    re.IGNORECASE,
)
_TAIL_PARENT = re.compile(r"\b(?:under|inside|within)\s+(?P<parent>.+)$", re.IGNORECASE)
_TITLE_LABEL = re.compile(r"\b(?:titled|called|named)\s+(?P<title>.+)$", re.IGNORECASE)
_TARGET_PREFIX = re.compile(
    r"^(?:(?:my|the|our)\s+)*(?:notion\s+)?(?:pages?\s+|docs?\s+|documents?\s+)?", re.IGNORECASE
)
_TARGET_SUFFIX = re.compile(r"\s+(?:page|doc|document)$", re.IGNORECASE)

HELP = (
    "In Notion I can find or read a page, create a page, or add content. For example: "
    "create a Notion page called Launch Plan under Projects: Finalize the checklist."
)
_QUESTIONS: dict[str, str] = {
    "query": "What should I look for in Notion?",
    "page": "Which Notion page? Send its exact title or link.",
    "title": "What should the new Notion page be called?",
    "parent": "Which Notion page should it go under? Send its exact title or link.",
    "body": "What content should I add?",
}


@dataclass(frozen=True)
class NotionCommand:
    kind: Kind
    query: str | None = None
    page: str | None = None
    title: str | None = None
    parent: str | None = None
    body: str | None = None
    error: str | None = None
    missing: Missing | None = None


def notion_from_dict(data: dict[str, Any]) -> NotionCommand:
    return NotionCommand(**data)


def finalize_notion(command: NotionCommand) -> NotionCommand:
    missing: Missing | None = None
    if command.kind == "find" and command.query is None:
        missing = "query"
    elif command.kind == "read" and not command.page:
        missing = "page"
    elif command.kind == "create":
        missing = "title" if not command.title else "parent" if not command.parent else None
    elif command.kind == "append":
        missing = "page" if not command.page else "body" if not command.body else None
    return replace(command, missing=missing, error=_QUESTIONS[missing] if missing else None)


def _clean(value: str | None) -> str | None:
    text = " ".join((value or "").split()).strip(" .,;:'\"“”")
    return text or None


def _target(value: str) -> str | None:
    text = _clean(_IN_NOTION.sub(" ", value)) or ""
    text = _TARGET_SUFFIX.sub("", _TARGET_PREFIX.sub("", text))
    cleaned = _clean(text)
    return None if cleaned is None or cleaned.casefold() == "notion" else cleaned


def _split(text: str) -> tuple[str, str | None]:
    match = _BODY.search(text)
    if match is None:
        return text, None
    return text[: match.start()], text[match.end() :].strip() or None


def _create(rest: str, body: str | None) -> NotionCommand:
    remaining = _IN_NOTION.sub(" ", rest).strip()
    parent: str | None = None
    if lead := _LEAD_PARENT.match(remaining):
        parent, remaining = lead["parent"], remaining[lead.end() :]
    elif tail := _TAIL_PARENT.search(remaining):
        parent, remaining = tail["parent"], remaining[: tail.start()]
    label = _TITLE_LABEL.search(remaining)
    title = _clean(label["title"] if label else remaining)
    return finalize_notion(
        NotionCommand("create", title=title, parent=_target(parent or ""), body=body)
    )


def parse_notion_command(command: str) -> NotionCommand | None:
    """A NotionCommand if the instruction clearly names Notion, otherwise None."""
    text = command.strip()
    head, body = _split(text)
    if not _NAMES_NOTION.search(head):
        return None
    if searched := _SEARCH_NOTION.match(head):
        return finalize_notion(NotionCommand("find", query=_clean(searched["rest"]) or ""))
    if appended := _APPEND.match(head):
        return finalize_notion(
            NotionCommand("append", page=_target(appended["target"]), body=body)
        )
    if created := _CREATE.match(head):
        return _create(created["rest"], body)
    if found := _FIND.match(head):
        rest = _clean(_IN_NOTION.sub(" ", found["rest"])) or ""
        if _ALL_PAGES.match(rest):
            return finalize_notion(NotionCommand("find", query=""))
        return finalize_notion(NotionCommand("find", query=_target(rest)))
    if opened := _READ.match(head):
        rest = _clean(_IN_NOTION.sub(" ", opened["rest"])) or ""
        if _ALL_PAGES.match(rest) and re.search(r"pages|docs|documents", rest, re.IGNORECASE):
            return finalize_notion(NotionCommand("find", query=""))
        return finalize_notion(NotionCommand("read", page=_target(rest)))
    return None if _OTHER_APP.search(head) else NotionCommand("help", error=HELP)


def continue_notion(command: NotionCommand, reply: str) -> NotionCommand:
    text = reply.strip()
    if command.missing == "query":
        return finalize_notion(replace(command, query=_clean(text)))
    if command.missing == "page":
        return finalize_notion(replace(command, page=_target(text)))
    if command.missing == "title":
        return finalize_notion(replace(command, title=_clean(text)))
    if command.missing == "parent":
        return finalize_notion(replace(command, parent=_target(text)))
    if command.missing == "body":
        return finalize_notion(replace(command, body=text or None))
    return command
