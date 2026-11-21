"""Understand email commands with plain code.

Recipients and the target email come only from the user's own words or contacts they
explicitly saved, never from a model, summary or email content. Anything ambiguous returns
a question, and the user's answer continues the same command.
"""

import re
from dataclasses import dataclass, replace
from typing import Any, Literal

from cognitive_mesh.integrations.gmail import ADDRESS

Kind = Literal["send", "write", "forward", "trash"]
Missing = Literal["recipient", "content", "body", "selector"]

_ADDRESS_LIST = rf"{ADDRESS}(?:\s*(?:,|;|&|\band\b)\s*{ADDRESS})*"
_ADDRESS = re.compile(ADDRESS)
_TO = re.compile(rf"\bto\s*:?\s+({_ADDRESS_LIST})", re.IGNORECASE)
_CC = re.compile(rf"\bcc\s*:?\s+({_ADDRESS_LIST})", re.IGNORECASE)
_BCC = re.compile(rf"\bbcc\s*:?\s+({_ADDRESS_LIST})", re.IGNORECASE)
_DIRECT_RECIPIENT = re.compile(rf"\b(?:e-?mail|mail|message)\s+({_ADDRESS_LIST})", re.IGNORECASE)
_FROM = re.compile(rf"\bfrom\s+({ADDRESS})", re.IGNORECASE)
_INTENT = re.compile(
    r"^\s*(?:(?:please|kindly|can you|could you)\s+)*"
    r"(?P<verb>send|write|draft|compose|forward|delete|trash|remove)\s+"
    r"(?:(?P<bulk>all|every)\s+(?:of\s+)?(?:my\s+|the\s+)?|(?:an?|the|this|that|my)\s+)?"
    r"(?:(?:latest|last|newest|most\s+recent|recent)\s+)?(?:e-?mails?|gmail|mails?)\b",
    re.IGNORECASE,
)
_EMAIL_VERB = re.compile(rf"^\s*(?:please\s+)?e-?mail\s+(?={ADDRESS})", re.IGNORECASE)
_SUBJECT_LABEL = re.compile(r"\bsubject\s*:", re.IGNORECASE)
_BODY_LABEL = re.compile(r"\b(?:body|message)\s*:", re.IGNORECASE)
_NOTE = re.compile(r"\bnote\s*:", re.IGNORECASE)
_LATEST = re.compile(r"\b(?:latest|last|newest|most\s+recent)\b", re.IGNORECASE)
_SUBJECT_SELECTOR = re.compile(
    r"\bsubject\s*:?\s*(?:[\"“]([^\"”]+)[\"”]|(.+?)(?=\s+(?:to|from|cc|bcc)\s|$))",
    re.IGNORECASE,
)
_TOPIC_START = re.compile(
    r"\b(?:about|regarding|saying|telling|asking|that|with|for|on)\b", re.IGNORECASE
)
_WORD = re.compile(r"[^\W_]+")
_NOT_CONTENT = {"to", "cc", "bcc", "and"}

NEED_RECIPIENT = (
    "Who should receive this email? Reply with the full address, for example name@example.com."
)
NEED_CONTENT = "What should the email say? Reply with the message or topic."
NEED_BODY = "What should the email body say? Reply with the exact text."
NEED_SELECTOR = (
    "Which email do you mean? Use the sender or subject, for example: "
    "the latest email from name@example.com."
)
NO_BULK = "I can act on one email at a time, so each action gets its own approval."
_QUESTIONS: dict[str, str] = {
    "recipient": NEED_RECIPIENT,
    "content": NEED_CONTENT,
    "body": NEED_BODY,
    "selector": NEED_SELECTOR,
}


@dataclass(frozen=True)
class EmailCommand:
    kind: Kind
    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()
    subject: str | None = None
    body: str | None = None
    topic: str | None = None
    query: str | None = None
    latest: bool = False
    note: str | None = None
    error: str | None = None
    missing: Missing | None = None


def email_from_dict(data: dict[str, Any]) -> EmailCommand:
    return EmailCommand(
        **{**data, "to": tuple(data["to"]), "cc": tuple(data["cc"]), "bcc": tuple(data["bcc"])}
    )


def _with_status(command: EmailCommand) -> EmailCommand:
    """Work out the one detail still needed, in a natural order."""
    missing: Missing | None = None
    if command.kind == "send":
        missing = "recipient" if not command.to else None
        if not missing and not (command.subject and command.body):
            missing = "body"
    elif command.kind == "write":
        missing = "recipient" if not command.to else "content" if not command.topic else None
    elif command.kind == "forward":
        missing = "recipient" if not command.to else "selector" if not command.query else None
    elif not command.query:
        missing = "selector"
    return replace(command, missing=missing, error=_QUESTIONS[missing] if missing else None)


def _addresses(pattern: re.Pattern[str], text: str) -> tuple[str, ...]:
    found = [a for match in pattern.finditer(text) for a in _ADDRESS.findall(match.group(1))]
    return tuple(dict.fromkeys(found))


def _labeled_parts(text: str) -> tuple[str | None, str | None]:
    """(subject, exact body) from `subject: ... body: ...` text."""
    labeled = _SUBJECT_LABEL.search(text)
    if not labeled:
        return None, None
    after = text[labeled.end() :]
    body = _BODY_LABEL.search(after)
    subject = " ".join((after[: body.start()] if body else after).split())
    exact = after[body.end() :].strip() if body else ""
    return subject or None, exact or None


def _content_words(text: str) -> list[str]:
    return [w for w in _WORD.findall(_ADDRESS.sub(" ", text)) if w.lower() not in _NOT_CONTENT]


def _outgoing(text: str, content_start: int) -> EmailCommand:
    labeled = _SUBJECT_LABEL.search(text)
    if labeled:
        head = text[: labeled.start()]
        subject, body = _labeled_parts(text)
        return _with_status(
            EmailCommand(
                "send", _addresses(_TO, head), _addresses(_CC, head), _addresses(_BCC, head),
                subject=subject, body=body,
            )
        )

    # Addresses later in the topic ("ask her to contact a@b.com") are not recipients.
    topic_start = _TOPIC_START.search(text, content_start)
    head = text[: topic_start.start()] if topic_start else text
    to, cc, bcc = _addresses(_TO, head), _addresses(_CC, head), _addresses(_BCC, head)
    words = _content_words(text[content_start:])
    # Without an address, "to ali" names a recipient rather than describing the content.
    has_content = bool(words) and (bool(to) or bool(topic_start))
    return _with_status(
        EmailCommand("write", to, cc, bcc, topic=text.strip() if has_content else None)
    )


def _selector(head: str) -> str | None:
    parts = []
    sender = _FROM.search(head)
    if sender:
        parts.append(f"from:{sender.group(1)}")
    subject = _SUBJECT_SELECTOR.search(head)
    if subject:
        value = (subject.group(1) or subject.group(2) or "").replace('"', "").strip()
        if value:
            parts.append(f'subject:"{value}"')
    return " ".join(parts) or None


def _forward(text: str) -> EmailCommand:
    note_match = _NOTE.search(text)
    head = text[: note_match.start()] if note_match else text
    note = text[note_match.end() :].strip() if note_match else None
    to = _addresses(_TO, head)
    query = _selector(_TO.sub(" ", head))
    return _with_status(
        EmailCommand(
            "forward", to, query=query, latest=bool(_LATEST.search(head)), note=note or None
        )
    )


def _trash(text: str) -> EmailCommand:
    return _with_status(
        EmailCommand("trash", query=_selector(text), latest=bool(_LATEST.search(text)))
    )


def parse_email_command(command: str) -> EmailCommand | None:
    """An EmailCommand if this is an email instruction, otherwise None."""
    direct = _EMAIL_VERB.match(command)
    if direct:
        return _outgoing(command, direct.end())
    intent = _INTENT.match(command)
    if not intent:
        return None
    verb = intent["verb"].lower()
    if intent["bulk"]:
        kind: Kind = (
            "trash" if verb in {"delete", "trash", "remove"}
            else "forward" if verb == "forward" else "send"
        )
        return EmailCommand(kind, error=NO_BULK)
    if verb == "forward":
        return _forward(command)
    if verb in {"delete", "trash", "remove"}:
        return _trash(command)
    return _outgoing(command, intent.end())


def email_from_text(kind: Kind, text: str) -> EmailCommand:
    """Read recipients and selectors with code once the Manager has chosen the action."""
    if kind == "forward":
        return _forward(text)
    if kind == "trash":
        return _trash(text)
    command = _outgoing(text, 0)
    direct = _DIRECT_RECIPIENT.search(text)
    if command.missing == "recipient" and direct:  # "mail ann@example.com to say thanks"
        has_content = bool(_content_words(text[direct.end() :]))
        return _with_status(
            replace(
                command,
                to=tuple(dict.fromkeys(_ADDRESS.findall(direct.group(1)))),
                topic=command.topic or (text.strip() if has_content else None),
            )
        )
    return command


def continue_email(command: EmailCommand, reply: str) -> EmailCommand:
    """Fill the one missing detail from the user's reply. Unusable replies change nothing."""
    text = reply.strip()
    if command.missing == "recipient":
        to = tuple(dict.fromkeys(_ADDRESS.findall(text)))
        if not to:
            return command
        topic = command.topic
        if command.kind == "write" and not topic and _content_words(text):
            topic = _ADDRESS.sub(" ", text).strip()
        return _with_status(replace(command, to=to, topic=topic))
    if command.missing in ("content", "body"):
        subject, body = _labeled_parts(text)
        if subject and body:
            return _with_status(replace(command, kind="send", subject=subject, body=body))
        if command.missing == "content":
            return _with_status(replace(command, topic=text))
        if not command.subject:  # an empty "subject:" label: let the writer create one
            return _with_status(replace(command, kind="write", topic=text))
        return _with_status(replace(command, body=text))
    if command.missing == "selector":
        addresses = _ADDRESS.findall(text)
        query = _selector(text) or (f"from:{addresses[0]}" if addresses else None)
        if not query:
            return command
        return _with_status(
            replace(command, query=query, latest=command.latest or bool(_LATEST.search(text)))
        )
    return command


def apply_contact(command: EmailCommand, text: str, contacts: dict[str, str]) -> EmailCommand:
    """Resolve "to my manager" using a contact the user explicitly saved."""
    if command.missing != "recipient":
        return command
    stripped = text.strip()
    for name in sorted(contacts, key=len, reverse=True):
        match = re.search(
            rf"(?:^|\bto\s+)(?:my\s+|our\s+)?{re.escape(name)}\b", stripped, re.IGNORECASE
        )
        if match:
            topic = command.topic
            if command.kind == "write" and not topic and _content_words(stripped[match.end() :]):
                topic = stripped
            return _with_status(replace(command, to=(contacts[name],), topic=topic))
    return command
