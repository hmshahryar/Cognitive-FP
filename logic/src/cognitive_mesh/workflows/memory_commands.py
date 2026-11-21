"""Explicit long-term memory commands, understood with plain code (no tokens)."""

import re
from dataclasses import dataclass
from typing import Any, Literal

from cognitive_mesh.integrations.gmail import ADDRESS

Operation = Literal["remember_contact", "remember_fact", "forget", "list"]
MAX_MEMORY_CHARS = 500

_REMEMBER = re.compile(r"^\s*(?:please\s+)?remember(?:\s+that)?\s*:?\s+(.+?)\s*$", re.IGNORECASE | re.DOTALL)
_CONTACT = re.compile(
    r"^(?:my\s+|our\s+)?(?P<name>[A-Za-z][\w .'-]{0,39}?)(?:'s)?"
    rf"(?:\s+(?:e-?mail(?:\s+address)?|address))?\s*(?:\bis\b|=|:)\s*(?P<email>{ADDRESS})[.!]?$",
    re.IGNORECASE,
)
_FORGET = re.compile(
    r"^\s*(?:please\s+)?forget(?:\s+that)?(?:\s+about)?\s+(?:my\s+|our\s+)?(?P<what>.+?)[.!]?\s*$",
    re.IGNORECASE,
)
_LIST = re.compile(
    r"^\s*(?:what\s+do\s+you\s+remember|(?:show|list)\s+(?:my\s+)?memor(?:y|ies))\b", re.IGNORECASE
)
_SENSITIVE = re.compile(
    r"\b(?:passwords?|passcodes?|api[\s_-]?keys?|secrets?|tokens?|otp|one[- ]time\s+code|"
    r"credit\s+card|card\s+number|cvv|ssn|social\s+security|bank\s+account|iban|private\s+key)\b"
    r"|\b\d{13,19}\b|\bsk[-_][A-Za-z0-9_-]{12,}",
    re.IGNORECASE,
)

SENSITIVE_REFUSAL = (
    "I can't store passwords, keys, tokens or financial details in memory, so I didn't save that."
)
TOO_LONG = f"That is too long to remember. Keep it under {MAX_MEMORY_CHARS} characters."


@dataclass(frozen=True)
class MemoryCommand:
    op: Operation
    name: str | None = None
    email: str | None = None
    text: str | None = None
    error: str | None = None


def memory_from_dict(data: dict[str, Any]) -> MemoryCommand:
    return MemoryCommand(**data)


def parse_memory_command(command: str) -> MemoryCommand | None:
    if _LIST.match(command):
        return MemoryCommand("list")
    remember = _REMEMBER.match(command)
    if remember:
        text = remember.group(1).strip()
        if _SENSITIVE.search(text):
            return MemoryCommand("remember_fact", error=SENSITIVE_REFUSAL)
        if len(text) > MAX_MEMORY_CHARS:
            return MemoryCommand("remember_fact", error=TOO_LONG)
        contact = _CONTACT.match(text)
        if contact:
            return MemoryCommand(
                "remember_contact",
                name=" ".join(contact["name"].lower().split()),
                email=contact["email"],
            )
        return MemoryCommand("remember_fact", text=text)
    forget = _FORGET.match(command)
    if forget and forget["what"].strip().lower() != "it":
        return MemoryCommand("forget", text=forget["what"].strip())
    return None
