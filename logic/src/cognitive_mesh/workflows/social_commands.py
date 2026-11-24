"""Understand Facebook Page and Instagram post commands with plain code.

The post text is taken exactly from the user's words and never passes through a model. Only the
instruction part (before the first colon) chooses the platform, the Page and the image link.
"""

import re
from dataclasses import dataclass, replace
from typing import Any, Literal

Kind = Literal["post", "banner", "help"]
Missing = Literal["text", "image", "headline"]

_NAMES = {"facebook": "facebook", "fb": "facebook", "instagram": "instagram",
          "insta": "instagram", "ig": "instagram"}
_PLATFORM = r"(?:facebook|fb|instagram|insta|ig)"
_DESTINATION = re.compile(
    rf"\b(?:on|to|in|at|via)\s+(?:(?:my|our|the)\s+)?(?P<name>{_PLATFORM})\b", re.IGNORECASE
)
_ADJECTIVE = re.compile(
    rf"\b(?P<name>{_PLATFORM})\s+(?:post|page|feed|caption|photo)\b", re.IGNORECASE
)
_ANY_PLATFORM = re.compile(rf"\b{_PLATFORM}\b", re.IGNORECASE)
_LINKEDIN = re.compile(r"\blinked\s?in\b", re.IGNORECASE)
_POST_VERB = re.compile(r"\b(?:post|publish|share|upload)\b", re.IGNORECASE)
_WRITE_VERB = re.compile(
    r"\b(?:write|rewrite|draft|create|generate|compose|make|research|improve)\b", re.IGNORECASE
)
_AS_IS = re.compile(r"\b(?:as[-\s]+is|verbatim|exactly)\b", re.IGNORECASE)
_SPLIT = re.compile(r":(?!//)(?!\s*https?://)|\n")
_IMAGE = re.compile(
    r"\b(?:image|photo|picture|pic|banner)\b(?:\s+(?:link|url))?\s*(?:is|=|:)?\s*"
    r"(?P<url>https?://\S+)",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://\S+")
# "**Image URL:** https://..." on its own line, as a structured message often has.
_IMAGE_LINE = re.compile(
    r"^[\s*_#>-]*(?:image|photo|picture|pic|banner)(?:\s+(?:link|url))?[\s*_:=-]*"
    r"(?P<url>https?://\S+?)[\s*_]*$",
    re.IGNORECASE | re.MULTILINE,
)
_CAPTION_LABEL = re.compile(
    r"^[\s*_#>-]*(?:caption|text|post\s+text)[\s*_]*:[\s*_]*", re.IGNORECASE | re.MULTILINE
)
_JOINER = re.compile(r"^\s*(?:and|with|then|,|-|–|—)\s+", re.IGNORECASE)
# Banners: "Make a banner for Instagram: headline "X", text "Y", caption: Z"
_BANNER = re.compile(r"\b(?:banner|poster|graphic|flyer)s?\b", re.IGNORECASE)
_BANNER_VERB = re.compile(r"\b(?:make|create|design|generate|build)\b", re.IGNORECASE)
_FOR_PLATFORM = re.compile(
    rf"\b(?:for|on|to|in)\s+(?:(?:my|our|the)\s+)?(?P<name>{_PLATFORM})\b", re.IGNORECASE
)
_QUOTED = r"[\"“](?P<v>[^\"”]{1,200})[\"”]"
_HEADLINE = re.compile(rf"\b(?:headline|title|heading)\s*[:=]?\s*{_QUOTED}", re.IGNORECASE)
_SUBTEXT = re.compile(
    rf"\b(?:text|subtext|subtitle|tagline|subheading)\s*[:=]?\s*{_QUOTED}", re.IGNORECASE
)
_BANNER_CAPTION = re.compile(r"\bcaption\s*[:=]\s*(?P<v>.+)$", re.IGNORECASE | re.DOTALL)
_COLOR = re.compile(r"(?<![\w/])#[0-9a-fA-F]{6}\b")
_BACKGROUND = re.compile(
    r"\bbackground(?:\s+(?:image|photo|picture))?\s*[:=]?\s*(?P<url>https?://\S+)", re.IGNORECASE
)
_PAGE = re.compile(
    r"\bpage\s+(?:called\s+|named\s+)?(?P<page>.+?)(?=\s+(?:with|using|and)\b|$)", re.IGNORECASE
)

_HELP = (
    "For Facebook and Instagram I post your exact text for now. For example: "
    "Post on Facebook: We're open on Saturday. "
    "Instagram also needs an image: Post on Instagram with image https://example.com/photo.jpg: "
    "your caption."
)
_QUESTIONS: dict[str, str] = {
    "text": "What should the Facebook post say?",
    "image": "Instagram posts need an image. Send a public https link to a JPEG image.",
    "headline": 'What should the banner headline say? For example: headline "Launching Soon".',
}


@dataclass(frozen=True)
class SocialPost:
    platform: str  # facebook | instagram
    kind: Kind = "post"
    text: str | None = None
    image_url: str | None = None
    page: str | None = None
    error: str | None = None
    missing: Missing | None = None
    # Banner fields (kind == "banner"): drawn by the app and placed in the user's Canva.
    headline: str | None = None
    subtext: str | None = None
    color: str | None = None
    background_url: str | None = None


def social_from_dict(data: dict[str, Any]) -> SocialPost:
    return SocialPost(**data)


def finalize_social(command: SocialPost) -> SocialPost:
    missing: Missing | None = None
    if command.kind == "banner":
        missing = None if command.headline else "headline"
    elif command.platform == "instagram" and not command.image_url:
        missing = "image"
    elif command.platform == "facebook" and not command.text and not command.image_url:
        missing = "text"
    return replace(command, missing=missing, error=_QUESTIONS[missing] if missing else None)


def _clean_url(value: str) -> str:
    return value.rstrip(".,;)'\"”")


def _unwrap(body: str) -> str:
    body = body.strip()
    if len(body) >= 2 and body[0] in "\"“" and body[-1] in "\"”":
        return body[1:-1].strip()
    return body


def _image_and_text(head: str, body: str, *, tail: str) -> tuple[str | None, str]:
    """Find the image link in the instruction, wherever the user put it. The text is unchanged
    apart from removing the link and its label, and the approval card shows the exact result.

    `tail` is everything after the instruction, starting with the separator.
    """
    labelled = _IMAGE.search(head)
    if labelled:
        return _clean_url(labelled["url"]), body
    bare = _URL.search(head)
    if bare:  # "post in instagram https://x.jpg and <text>", possibly running onto more lines
        after = head[bare.end() :]
        if tail.startswith("\n") and after.strip():
            text = _JOINER.sub("", after).strip() + tail
        else:
            text = body or _JOINER.sub("", after).strip()
        return _clean_url(bare.group(0)), _unwrap(text)
    line = _IMAGE_LINE.search(body)
    if line is None:
        return None, body
    remaining = (body[: line.start()] + body[line.end() :]).strip()
    caption = _CAPTION_LABEL.search(remaining)
    if caption:
        remaining = remaining[caption.end() :]
    return _clean_url(line["url"]), _unwrap(remaining.strip())


def _parse_banner(text: str, head: str) -> SocialPost | None:
    """A banner request: the app draws it, puts it in Canva, then asks to post it."""
    if not (_BANNER.search(head) and _BANNER_VERB.search(head)) or _LINKEDIN.search(head):
        return None
    if not _FOR_PLATFORM.search(head):
        return None
    names = {_NAMES[m.group(0).lower()] for m in _ANY_PLATFORM.finditer(_URL.sub(" ", head))}
    if len(names) != 1:
        return None
    rest = text  # labelled fields can come before or after the first colon
    caption = _BANNER_CAPTION.search(rest)
    fields = rest[: caption.start()] if caption else rest
    headline, subtext = _HEADLINE.search(fields), _SUBTEXT.search(fields)
    color, background = _COLOR.search(fields), _BACKGROUND.search(fields)
    return finalize_social(
        SocialPost(
            names.pop(),
            "banner",
            text=_unwrap(caption["v"]) if caption else None,
            headline=headline["v"].strip() if headline else None,
            subtext=subtext["v"].strip() if subtext else None,
            color=color.group(0) if color else None,
            background_url=_clean_url(background["url"]) if background else None,
        )
    )


def parse_social_command(command: str) -> SocialPost | None:
    """A Facebook or Instagram post if the instruction clearly asks for one, otherwise None."""
    text = command.strip()
    split = _SPLIT.search(text)
    head = text[: split.start()] if split else text
    banner = _parse_banner(text, head)
    if banner is not None:
        return banner
    body = _unwrap(text[split.end() :]) if split else ""
    if not (_DESTINATION.search(head) or _ADJECTIVE.search(head)):
        return None
    # Every platform named in the instruction counts, so "on Facebook and Instagram" is never
    # reduced to one of them.
    names = {_NAMES[m.group(0).lower()] for m in _ANY_PLATFORM.finditer(_URL.sub(" ", head))}
    if len(names) != 1 or _LINKEDIN.search(head):
        return None  # none, or several platforms: the general router explains
    platform = names.pop()
    if not (_POST_VERB.search(head) or _WRITE_VERB.search(head)):
        return None
    if _WRITE_VERB.search(head) and not _AS_IS.search(head):
        return SocialPost(platform, "help", error=_HELP)
    image_url, body = _image_and_text(head, body, tail=text[split.start() :] if split else "")
    page = None
    if platform == "facebook":
        rest = _URL.sub(" ", head)
        found = _PAGE.search(rest)
        if found:
            page = " ".join(found["page"].split()).strip(" .,'\"") or None
    return finalize_social(
        SocialPost(platform, text=body or None, image_url=image_url, page=page)
    )


def continue_social(command: SocialPost, reply: str) -> SocialPost:
    text = reply.strip()
    if command.missing == "image":
        found = _URL.search(text)
        return finalize_social(replace(command, image_url=_clean_url(found.group(0)) if found else None))
    if command.missing == "text":
        return finalize_social(replace(command, text=_unwrap(text) or None))
    if command.missing == "headline":
        quoted = re.search(_QUOTED, text)
        value = quoted["v"] if quoted else _unwrap(text)
        return finalize_social(replace(command, headline=value.strip()[:200] or None))
    return command
