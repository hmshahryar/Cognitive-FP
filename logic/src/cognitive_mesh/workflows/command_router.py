"""Decide what a command needs before spending any AI or search resources.

    post_as_is  the user supplied the exact text: no AI, no search
    write       write and review a post: no search
    research    only when explicitly requested: research, then write and review
    model       wording is unclear: one model call classifies it
    unsupported not a posting command

Plain code, so routing is free and repeatable. The user's post text is extracted here and
never passes through a model. The platform must come from the user and is never guessed.
"""

import re
from dataclasses import dataclass, replace
from typing import Literal

from cognitive_mesh.agents.schemas import LINKEDIN_MAX_CHARS
from cognitive_mesh.integrations.catalog import get_connector
from cognitive_mesh.workflows.calendar_commands import CalendarCommand, parse_calendar_command
from cognitive_mesh.workflows.crm_commands import CrmCommand, parse_crm_command
from cognitive_mesh.workflows.email_commands import EmailCommand, parse_email_command
from cognitive_mesh.workflows.memory_commands import MemoryCommand, parse_memory_command
from cognitive_mesh.workflows.notion_commands import NotionCommand, parse_notion_command
from cognitive_mesh.workflows.sheet_commands import SheetCommand, parse_sheet_command
from cognitive_mesh.workflows.social_commands import SocialPost, parse_social_command

Action = Literal[
    "post_as_is", "write", "research", "model", "unsupported", "gmail", "memory", "calendar",
    "sheets", "crm", "notion", "social",
]
POST_PLATFORMS = frozenset({"linkedin"})  # apps that accept social posts today

PLATFORM_NAMES = {
    "linkedin": "LinkedIn",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "twitter": "X (Twitter)",
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "slack": "Slack",
    "notion": "Notion",
    "gmail": "Gmail",
}

_ALIASES = {
    "linkedin": "linkedin",
    "linked in": "linkedin",
    "linkdin": "linkedin",
    "linkedn": "linkedin",
    "likedin": "linkedin",
    "likned in": "linkedin",
    "instagram": "instagram",
    "insta": "instagram",
    "facebook": "facebook",
    "fb": "facebook",
    "twitter": "twitter",
    "tiktok": "tiktok",
    "youtube": "youtube",
    "slack": "slack",
    "notion": "notion",
    "gmail": "gmail",
}
_P = "|".join(sorted(map(re.escape, _ALIASES), key=len, reverse=True))
_NAME = re.compile(rf"\b(?:{_P})\b", re.IGNORECASE)
_AS_ADJECTIVE = re.compile(
    rf"\b({_P})\s+(?:post|update|article|caption|story|reel|tweet)\b", re.IGNORECASE
)
_NOT_A_DESTINATION = r"(?!\s+(?:marketing|ads?|strategy|algorithm|trends?|growth|influencers?))"
_DESTINATION = re.compile(
    rf"\b(?:on|to|in|for|at|via)\s+(?:my\s+|our\s+)?((?:{_P}){_NOT_A_DESTINATION}"
    rf"(?:\s*(?:,|and|&)\s*(?:my\s+|our\s+)?(?:{_P}){_NOT_A_DESTINATION})*)",
    re.IGNORECASE,
)
_BARE_PLATFORM = re.compile(
    rf"^\s*(?:(?:on|to|in|at)\s+)?(?:my\s+)?({_P})\s*[.!]?\s*$", re.IGNORECASE
)

_POST_VERB = re.compile(r"\b(?:post|publish|share|upload)\b", re.IGNORECASE)
_WRITE_VERB = re.compile(
    r"\b(?:write|rewrite|draft|create|generate|compose|prepare|improve|edit|summarize"
    r"|research|investigate)\b",
    re.IGNORECASE,
)
_RESEARCH_WORD = re.compile(r"\b(?:research|investigate)\b", re.IGNORECASE)
_AS_IS = re.compile(
    r"\b(?:as[-\s]+is|as\s+it\s+is|verbatim|word\s+for\s+word|unchanged"
    r"|without\s+(?:any\s+)?(?:changes?|editing))\b",
    re.IGNORECASE,
)
_HERE_IS = re.compile(
    r"\bhere(?:'s|’s|\s+is)\s+(?:the\s+|my\s+)?(?:post|text|content)\b\s*[:\-–—]?\s*",
    re.IGNORECASE,
)
_TOPIC = re.compile(r"\b(?:about|regarding|topic)\b", re.IGNORECASE)
_CONTENT_NOUN = re.compile(r"\b(?:post|caption|article|content|update|tweet)\b", re.IGNORECASE)
_NO_BODY = re.compile(
    r"^(?:please\s+)?(?:post|publish|share|upload)\s+(?:this|it|the following|my post|my text)\b",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"^([^\"“\n:]*)[\"“](.+)[\"”]([^\"”]*)$", re.DOTALL)
_RESEARCH = re.compile(
    r"(?<!\w)@research\b"
    r"|^\W*(?:please\s+)?(?:do\s+)?(?:some\s+)?(?:research|investigate|deep[- ]dive)\b"
    r"|\b(?:research|investigate)\s+(?:it|this|that|first|then|the topic)\b"
    r"|\bfirst\s+research\b"
    r"|\b(?:do|with|after|deep|in-depth)\s+(?:some\s+|proper\s+)?research\b"
    r"|\bdeep[- ]dive\b",
    re.IGNORECASE,
)
_MAX_HEAD_CHARS = 160


@dataclass(frozen=True)
class Route:
    action: Action
    platforms: tuple[str, ...] = ()
    text: str | None = None  # the user's exact post, for post_as_is
    email: EmailCommand | None = None  # parsed email action, for gmail
    memory: MemoryCommand | None = None  # explicit remember/forget/list
    calendar: CalendarCommand | None = None  # parsed calendar action
    sheet: SheetCommand | None = None  # parsed Google Sheets action
    crm: CrmCommand | None = None  # parsed CRM action
    notion: NotionCommand | None = None  # parsed Notion action
    social: SocialPost | None = None  # parsed Facebook Page or Instagram post

    @property
    def platform(self) -> str | None:
        return self.platforms[0] if len(self.platforms) == 1 else None


def find_platforms(instruction: str) -> tuple[str, ...]:
    """Where to post, from the instruction only (never from the post's own text)."""
    direct = [_ALIASES[m.lower()] for m in _AS_ADJECTIVE.findall(instruction)]
    if direct:
        return tuple(dict.fromkeys(direct))
    found = [
        _ALIASES[name.lower()]
        for match in _DESTINATION.finditer(instruction)
        for name in _NAME.findall(match.group(1))
    ]
    return tuple(dict.fromkeys(found))


def bare_platform(command: str) -> str | None:
    """A reply that is only a platform name, such as "LinkedIn"."""
    match = _BARE_PLATFORM.match(command)
    return _ALIASES[match.group(1).lower()] if match else None


def _is_posting_head(head: str) -> bool:
    if (
        len(head) > _MAX_HEAD_CHARS
        or not _POST_VERB.search(head)
        or _TOPIC.search(head)
        or _RESEARCH_WORD.search(head)
    ):
        return False
    # "write the post as is" means keep it: an explicit as-is overrides the writing verb.
    return bool(_AS_IS.search(head)) or not _WRITE_VERB.search(head)


def _unwrap(body: str) -> str:
    """Trim only the outer whitespace and one pair of wrapping quotes."""
    body = body.strip()
    if len(body) >= 2 and body[0] in "\"“" and body[-1] in "\"”":
        return body[1:-1].strip()
    return body


def _verbatim(command: str) -> tuple[str, str] | None:
    """(instruction, exact post text) when the user supplied the post."""
    text = command.strip()
    here = _HERE_IS.search(text)
    if here and here.start() > 0 and _is_posting_head(text[: here.start()]):
        return text[: here.start()], _unwrap(text[here.end() :])
    quoted = _QUOTED.match(text)
    if quoted and _is_posting_head(f"{quoted[1]} {quoted[3]}"):
        return f"{quoted[1]} {quoted[3]}", quoted[2].strip()
    delimiter = re.search(r":|\n", text)
    if delimiter and _is_posting_head(text[: delimiter.start()]):
        return text[: delimiter.start()], _unwrap(text[delimiter.end() :])
    if _NO_BODY.match(text) and not _TOPIC.search(text) and not _WRITE_VERB.search(text):
        return text, ""
    return None


def route_command(command: str) -> Route:
    memory = parse_memory_command(command)
    if memory is not None:
        return Route("memory", memory=memory)
    notion = parse_notion_command(command)
    if notion is not None:
        return Route("notion", ("notion",), notion=notion)
    social = parse_social_command(command)
    if social is not None:
        return Route("social", (social.platform,), social=social)
    crm = parse_crm_command(command)
    if crm is not None:
        return Route("crm", (crm.provider,) if crm.provider else (), crm=crm)
    calendar = parse_calendar_command(command)
    if calendar is not None:
        return Route("calendar", ("googlecalendar",), calendar=calendar)
    sheet = parse_sheet_command(command)
    if sheet is not None:
        return Route("sheets", ("googlesheets",), sheet=sheet)
    email = parse_email_command(command)
    if email is not None:
        return Route("gmail", ("gmail",), email=email)
    supplied = _verbatim(command)
    if supplied:
        head, body = supplied
        return Route("post_as_is", find_platforms(head), body or None)

    platforms = find_platforms(command)
    if _RESEARCH.search(command):
        return Route("research", platforms)
    has_noun = bool(_CONTENT_NOUN.search(command))
    if _WRITE_VERB.search(command) and (has_noun or platforms):
        return Route("write", platforms)
    if _POST_VERB.search(command) and _TOPIC.search(command):
        return Route("write", platforms)
    if has_noun or _POST_VERB.search(command):
        return Route("model", platforms)
    return Route("unsupported", platforms)


def needs_platform(route: Route) -> bool:
    return (
        route.action
        not in (
            "unsupported", "memory", "gmail", "calendar", "sheets", "crm", "notion", "social",
        )
        and not route.platforms
    )


def with_platform(route: Route, platform: str) -> Route:
    return replace(route, platforms=(platform,))


def is_available(platform: str) -> bool:
    connector = get_connector(platform)
    return connector is not None and connector.available


def blocking_reply(route: Route) -> str | None:
    """A message for the user when the command can't proceed. Uses no AI or search."""
    if route.action == "gmail":
        return route.email.error if route.email else "Tell me what to do with the email."
    if route.action == "calendar":
        return route.calendar.error if route.calendar else "Tell me what to do on your calendar."
    if route.action == "sheets":
        return route.sheet.error if route.sheet else "Tell me what to do in Google Sheets."
    if route.action == "crm":
        return route.crm.error if route.crm else "Tell me what to do in your CRM."
    if route.action == "notion":
        return route.notion.error if route.notion else "Tell me what to do in Notion."
    if route.action == "social":
        return route.social.error if route.social else "Tell me what to post."
    if route.action == "unsupported":
        return (
            "Right now I can post to LinkedIn, a Facebook Page or Instagram, handle Gmail, read "
            "and schedule on Google Calendar, read and write Google Sheets, manage HubSpot, "
            "Salesforce and Zoho CRM contacts, or find, read and write Notion pages. Tell me "
            "what to do and where."
        )
    if route.action == "post_as_is" and not route.text:
        return "Paste the exact text you want posted, and tell me where to post it."
    if not route.platforms:
        return "Where should I post this? Reply with the platform, for example LinkedIn."
    if len(route.platforms) > 1:
        names = " and ".join(PLATFORM_NAMES[p] for p in route.platforms)
        return f"I can handle one platform per command. Choose one of {names}."
    if route.platforms[0] in ("facebook", "instagram"):
        return (
            f"For {PLATFORM_NAMES[route.platforms[0]]} I post your exact text. Send it like this: "
            f"Post on {PLATFORM_NAMES[route.platforms[0]]}: your text."
        )
    if route.platforms[0] not in POST_PLATFORMS or not is_available(route.platforms[0]):
        return (
            f"{PLATFORM_NAMES[route.platforms[0]]} isn't available for posts yet. "
            "I can post to LinkedIn."
        )
    if route.action == "post_as_is" and len(route.text or "") > LINKEDIN_MAX_CHARS:
        return (
            f"Your post is {len(route.text or '')} characters and LinkedIn allows "
            f"{LINKEDIN_MAX_CHARS}. I won't trim your text, so please shorten it and send it again."
        )
    return None
