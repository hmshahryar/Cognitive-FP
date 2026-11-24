"""Understand CRM commands with plain code.

Contact details are taken exactly from the user's words. Anything missing becomes one question,
and the reply continues the same command.
"""

import re
from dataclasses import dataclass, replace
from typing import Any, Literal

from cognitive_mesh.integrations.gmail import ADDRESS
from cognitive_mesh.integrations.hubspot import MAX_FIELD_CHARS, MAX_NOTE_CHARS

Kind = Literal["find", "create", "update", "note"]
Missing = Literal["email", "fields", "note", "query"]

_ADDRESS = re.compile(ADDRESS)
MULTIPLE_CRMS = "multiple"
_CRM_NAMES = r"hub\s?spot|sales\s?force|zoho"
_CRM = re.compile(rf"\b(?:{_CRM_NAMES}|crm)\b", re.IGNORECASE)
_DESTINATION = re.compile(
    rf"\b(?:to|in|into|on|from)\s+(?:(?:my|the|our)\s+)?(?P<name>{_CRM_NAMES})\b", re.IGNORECASE
)
_OTHER_APP = re.compile(r"\b(?:sheets?|spreadsheet|calendar|linkedin)\b", re.IGNORECASE)
_NOUN = r"(?P<noun>contacts?|leads?|customers?)"
_ARTICLES = r"(?:(?:a|an|new|the|this|my)\s+)*"
_NOTE = re.compile(
    rf"^\s*(?:please\s+)?(?:add|write|log|save)\s+{_ARTICLES}note\s+(?:to|on|for)\s+"
    rf"{_ARTICLES}(?:{_NOUN}\s+)?(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_CREATE = re.compile(
    rf"^\s*(?:please\s+)?(?:add|create|save|make)\s+{_ARTICLES}{_NOUN}\b(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_UPDATE = re.compile(
    rf"^\s*(?:please\s+)?(?:update|change|edit|set)\s+{_ARTICLES}{_NOUN}\b(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_FIND = re.compile(
    rf"^\s*(?:please\s+)?(?:find|search(?:\s+for)?|look\s*up|show|get)\s+(?:me\s+)?{_ARTICLES}"
    rf"{_NOUN}\b(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_IN_CRM = re.compile(
    rf"\b(?:to|in|into|on|from)\s+(?:(?:my|the|our)\s+)?(?:{_CRM_NAMES}|crm)\b", re.IGNORECASE
)
_VALUE = r"\s*(?:is|=|:)?\s*(?P<value>[^,;\n]+)"
_FIELDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("phone", re.compile(r"\b(?:phone|mobile)\s*(?:number)?\s*(?:is|=|:)?\s*(?P<value>\+?[\d][\d ()-]{5,24})", re.IGNORECASE)),
    ("company", re.compile(rf"\bcompany{_VALUE}", re.IGNORECASE)),
    ("jobtitle", re.compile(rf"\b(?:job\s?title|title|role){_VALUE}", re.IGNORECASE)),
    ("website", re.compile(rf"\bwebsite{_VALUE}", re.IGNORECASE)),
    ("city", re.compile(rf"\bcity{_VALUE}", re.IGNORECASE)),
    ("country", re.compile(rf"\bcountry{_VALUE}", re.IGNORECASE)),
)
_LABEL_START = re.compile(
    r"\b(?:phone|mobile|company|job\s?title|title|role|website|city|country|email)\b",
    re.IGNORECASE,
)
_NAME_LABEL = re.compile(rf"\bname{_VALUE}", re.IGNORECASE)
_PLAIN_NAME = re.compile(r"^[^\W\d_]+(?:[ '-][^\W\d_]+){0,3}$")

_QUESTIONS: dict[str, str] = {
    "email": "What is the contact's email address? I use it to avoid duplicates.",
    "fields": "What should I change? For example: phone +92 300 1234567 or company Acme.",
    "note": "What should the note say?",
    "query": "Which contact should I look for? Send a name or email.",
}


@dataclass(frozen=True)
class CrmCommand:
    kind: Kind
    email: str | None = None
    query: str | None = None
    fields: tuple[tuple[str, str], ...] = ()
    note: str | None = None
    error: str | None = None
    missing: Missing | None = None
    provider: str | None = None  # the CRM the user named as the destination


def crm_provider_in(text: str, kind: str) -> str | None:
    """The CRM named as the destination. A note's body or a company name never selects one."""
    scope = text.split(":", 1)[0] if kind == "note" else text
    names = {
        re.sub(r"\s+", "", match["name"]).lower() for match in _DESTINATION.finditer(scope)
    }
    if len(names) > 1:
        return MULTIPLE_CRMS
    return names.pop() if names else None


def crm_from_dict(data: dict[str, Any]) -> CrmCommand:
    return CrmCommand(**{**data, "fields": tuple(tuple(pair) for pair in data["fields"])})


def finalize_crm(command: CrmCommand) -> CrmCommand:
    missing: Missing | None = None
    if command.kind == "find":
        missing = "query" if not (command.email or command.query) else None
    elif not command.email:
        missing = "email"
    elif command.kind == "update" and not command.fields:
        missing = "fields"
    elif command.kind == "note" and not command.note:
        missing = "note"
    return replace(command, missing=missing, error=_QUESTIONS[missing] if missing else None)


def _email(text: str) -> str | None:
    match = _ADDRESS.search(text)
    return match.group(0).lower() if match else None


def _clean(value: str) -> str:
    return " ".join(value.split()).strip(" .,:;")[:MAX_FIELD_CHARS]


def _labelled(text: str) -> list[tuple[str, str]]:
    without_email = _ADDRESS.sub(" ", text)
    found = []
    for field, pattern in _FIELDS:
        match = pattern.search(without_email)
        if match and (value := _clean(match.group("value"))):
            found.append((field, value))
    return found


def _name(text: str) -> list[tuple[str, str]]:
    labelled = _NAME_LABEL.search(_ADDRESS.sub(" ", text))
    if labelled:
        candidate = labelled.group("value")
    else:
        head = re.split(r"[,;\n]", _ADDRESS.split(text, maxsplit=1)[0], maxsplit=1)[0]
        label = _LABEL_START.search(head)
        candidate = head[: label.start()] if label else head
    name = _clean(re.sub(r"^\s*(?:named|called)\s+", "", candidate, flags=re.IGNORECASE))
    if not _PLAIN_NAME.match(name):
        return []
    first, _, last = name.partition(" ")
    return [("firstname", first)] + ([("lastname", last)] if last else [])


def _is_crm(text: str, noun: str | None, email: str | None) -> bool:
    if _OTHER_APP.search(text):
        return False
    if _CRM.search(text):
        return True
    return bool(email and noun and not noun.lower().startswith("customer"))


def parse_crm_command(command: str) -> CrmCommand | None:
    """A CrmCommand if this is a clear CRM instruction, otherwise None."""
    parsed = _parse(command)
    if parsed is None:
        return None
    return replace(parsed, provider=crm_provider_in(command, parsed.kind))


def _parse(command: str) -> CrmCommand | None:
    text = command.strip()
    email = _email(text)
    note = _NOTE.match(text)
    if note and _is_crm(text, note["noun"] or "contact", email):
        rest = _IN_CRM.sub(" ", note["rest"])
        body = rest.split(":", 1)[1] if ":" in rest else _ADDRESS.sub(" ", rest, count=1)
        value = " ".join(body.split())[:MAX_NOTE_CHARS] or None
        return finalize_crm(CrmCommand("note", email=email, note=value))
    created = _CREATE.match(text)
    if created and _is_crm(text, created["noun"], email):
        rest = _IN_CRM.sub(" ", created["rest"]).lstrip(" :")
        fields = [*_name(rest), *_labelled(rest)]
        if email:
            fields.insert(0, ("email", email))
        return finalize_crm(CrmCommand("create", email=email, fields=tuple(fields)))
    updated = _UPDATE.match(text)
    if updated and _is_crm(text, updated["noun"], email):
        rest = _IN_CRM.sub(" ", updated["rest"])
        return finalize_crm(CrmCommand("update", email=email, fields=tuple(_labelled(rest))))
    found = _FIND.match(text)
    if found and _is_crm(text, found["noun"], email):
        rest = _clean(_IN_CRM.sub(" ", found["rest"]))
        return finalize_crm(CrmCommand("find", email=email, query=None if email else rest or None))
    return None


def continue_crm(command: CrmCommand, reply: str) -> CrmCommand:
    text = reply.strip()
    if command.missing == "email":
        email = _email(text)
        if not email:
            return command
        fields = command.fields
        if command.kind == "create":
            fields = (("email", email), *fields)
        return finalize_crm(replace(command, email=email, fields=fields))
    if command.missing == "fields":
        return finalize_crm(replace(command, fields=tuple(_labelled(text))))
    if command.missing == "note":
        return finalize_crm(replace(command, note=" ".join(text.split())[:MAX_NOTE_CHARS] or None))
    if command.missing == "query":
        email = _email(text)
        return finalize_crm(replace(command, email=email, query=None if email else _clean(text)))
    return command
