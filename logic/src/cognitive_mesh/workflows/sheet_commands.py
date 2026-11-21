"""Understand Google Sheets commands with plain code.

Cell values are taken exactly from the user's words. Anything missing becomes one question,
and the reply continues the same command.
"""

import csv
import io
import re
from dataclasses import dataclass, replace
from typing import Any, Literal

Kind = Literal["list", "read", "create", "append", "update"]
Missing = Literal["name", "target", "rows", "cell", "value"]

_SHEET = r"(?:google\s+)?(?:spread\s?)?sheets?"
_URL_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,128})")
_QUOTED = r"[\"“]([^\"”]{1,200})[\"”]"
_CELL_REF = re.compile(r"\b([A-Za-z]{1,3}[1-9]\d{0,6})\b")
_RANGE = re.compile(r"\b([A-Za-z]{1,3}[1-9]\d{0,6}:[A-Za-z]{1,3}[1-9]\d{0,6})\b")
_LIST = re.compile(
    r"^\s*(?:please\s+)?(?:show|list|see|find)\s+(?:me\s+)?(?:all\s+)?(?:of\s+)?(?:my\s+)?"
    r"(?:google\s+)?(?:spread\s?)?sheets\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_CREATE = re.compile(
    rf"^\s*(?:please\s+)?(?:create|make|start|set\s+up|new)\s+(?:(?:a|an|new|the|my)\s+)*{_SHEET}\b",
    re.IGNORECASE,
)
_READ = re.compile(
    rf"^\s*(?:please\s+)?(?:read|show|open|get|view|what(?:'s|’s|\s+is)\s+in)\b.{{0,40}}?\b{_SHEET}\b",
    re.IGNORECASE | re.DOTALL,
)
_APPEND = re.compile(r"^\s*(?:please\s+)?(?:add|append|insert|put|write)\b", re.IGNORECASE)
_UPDATE = re.compile(r"^\s*(?:please\s+)?(?:update|set|change)\b", re.IGNORECASE)
_TARGET = re.compile(
    rf"\b(?:to|into|in|on|of|from)\s+(?:(?:my|the|this|that)\s+)*{_SHEET}"
    rf"(?:\s+(?:called|named|titled))?(?:\s+{_QUOTED}|\s+(?P<name>[^:\n]+?))?"
    r"(?=\s*:|\s+to\s|\s+tab\b|\s*$|\n)",
    re.IGNORECASE,
)
_TO_IT = re.compile(rf"\b(?:to|into|in|on)\s+(?:it|(?:this|that)\s+{_SHEET})\b", re.IGNORECASE)
_NAME = re.compile(
    rf"\b(?:called|named|titled|name)\s+(?:{_QUOTED}|(.+?))"
    r"(?=\s+(?:with|having|and\s+columns|columns?|headers?)\b|\s*:|\s*$)",
    re.IGNORECASE,
)
_COLUMNS = re.compile(r"\b(?:columns?|headers?)\s*:?\s*(.+?)\s*$", re.IGNORECASE | re.DOTALL)
_TAB = re.compile(rf"\b(?:tab|worksheet)\s+(?:{_QUOTED}|([\w -]{{1,60}}?))(?=\s*:|\s*$|\s+to\s)", re.IGNORECASE)
_ROW_WORD = re.compile(r"^\s*(?:please\s+)?(?:add|append|insert|put|write)\s+(?:(?:a|an|new|this|these|the)\s+)*(?:rows?|data|entry|record)?\s*:?\s*", re.IGNORECASE)
_UPDATE_TO = re.compile(r"\bto\s+(.+?)\s*$", re.IGNORECASE | re.DOTALL)

_QUESTIONS: dict[str, str] = {
    "name": "What should I name the new sheet?",
    "target": "Which sheet do you mean? Reply with its exact name or paste its link.",
    "rows": "What should I add? Separate values with commas, and rows with a new line.",
    "cell": "Which cell should I change? For example B2.",
    "value": "What value should go in that cell?",
}


@dataclass(frozen=True)
class SheetCommand:
    kind: Kind
    name: str | None = None
    spreadsheet_id: str | None = None
    use_last: bool = False
    tab: str | None = None
    rows: tuple[tuple[str, ...], ...] = ()
    cell: str | None = None
    value: str | None = None
    cell_range: str | None = None
    error: str | None = None
    missing: Missing | None = None


def sheet_from_dict(data: dict[str, Any]) -> SheetCommand:
    rows = tuple(tuple(row) for row in data["rows"])
    return SheetCommand(**{**data, "rows": rows})


def finalize_sheet(command: SheetCommand) -> SheetCommand:
    has_target = bool(command.name or command.spreadsheet_id or command.use_last)
    missing: Missing | None = None
    if command.kind == "create":
        missing = "name" if not command.name else None
    elif command.kind == "read":
        missing = "target" if not has_target else None
    elif command.kind == "append":
        missing = "target" if not has_target else "rows" if not command.rows else None
    elif command.kind == "update":
        if not has_target:
            missing = "target"
        elif not command.cell:
            missing = "cell"
        elif command.value is None:
            missing = "value"
    return replace(command, missing=missing, error=_QUESTIONS[missing] if missing else None)


def parse_rows(text: str) -> tuple[tuple[str, ...], ...]:
    """Exact values from comma-separated text. Quoted values may contain commas."""
    rows = []
    for line in re.split(r"\n|;", text):
        if not line.strip():
            continue
        cells = next(csv.reader(io.StringIO(line.strip()), skipinitialspace=True), [])
        cleaned = tuple(cell.strip() for cell in cells)
        if any(cleaned):
            rows.append(cleaned)
    return tuple(rows)


def _columns(text: str) -> tuple[str, ...]:
    return tuple(c.strip() for c in re.split(r",|\band\b", text, flags=re.IGNORECASE) if c.strip())


def _target(text: str) -> dict[str, Any]:
    url = _URL_ID.search(text)
    if url:
        return {"spreadsheet_id": url.group(1)}
    if _TO_IT.search(text):
        return {"use_last": True}
    match = _TARGET.search(text)
    if not match:
        return {}
    name = (match.group(1) or match.group("name") or "").strip(" .,")
    return {"name": name} if name else {}


def _tab(text: str) -> str | None:
    match = _TAB.search(text)
    return (match.group(1) or match.group(2)).strip() if match else None


def _create(text: str) -> SheetCommand:
    columns_match = _COLUMNS.search(text)
    name_match = _NAME.search(text)
    name = (name_match.group(1) or name_match.group(2)).strip(" .,") if name_match else None
    rows = (_columns(columns_match.group(1)),) if columns_match else ()
    return finalize_sheet(SheetCommand("create", name=name, rows=rows))


def _append(text: str) -> SheetCommand | None:
    target = _target(text)
    match = _TARGET.search(text) or _TO_IT.search(text)
    if not target and not match:
        return None
    colon = text.find(":", match.end() - 1) if match else -1
    if colon >= 0 and "://" not in text[colon : colon + 3]:
        values = text[colon + 1 :]
    elif match:
        values = _ROW_WORD.sub("", text[: match.start()], count=1)
    else:
        values = ""
    return finalize_sheet(
        SheetCommand("append", tab=_tab(text), rows=parse_rows(values), **target)
    )


def _update(text: str) -> SheetCommand | None:
    target = _target(text)
    if not target:
        return None
    head = _URL_ID.sub(" ", text)
    without_target = _TARGET.sub(" ", head, count=1)
    cell = _CELL_REF.search(without_target)
    value_match = _UPDATE_TO.search(without_target)
    return finalize_sheet(
        SheetCommand(
            "update",
            tab=_tab(text),
            cell=cell.group(1).upper() if cell else None,
            value=value_match.group(1).strip() if value_match else None,
            **target,
        )
    )


def parse_sheet_command(command: str) -> SheetCommand | None:
    """A SheetCommand if this is a clear Sheets instruction, otherwise None."""
    if _LIST.match(command):
        return SheetCommand("list")
    if _CREATE.match(command):
        return _create(command)
    if _UPDATE.match(command):
        updated = _update(command)
        if updated is not None:
            return updated
    if _APPEND.match(command):
        appended = _append(command)
        if appended is not None:
            return appended
    if _READ.match(command):
        cell_range = _RANGE.search(command)
        target = _target(command)
        if not target:
            tail = re.split(rf"\b{_SHEET}\b", command, maxsplit=1, flags=re.IGNORECASE)[-1]
            name = _RANGE.sub(" ", tail).strip(" .,:\"“”")
            target = {"name": name} if name else {}
        return finalize_sheet(
            SheetCommand(
                "read",
                tab=_tab(command),
                cell_range=cell_range.group(1).upper() if cell_range else None,
                **target,
            )
        )
    return None


def continue_sheet(command: SheetCommand, reply: str) -> SheetCommand:
    text = reply.strip()
    if command.missing == "name":
        return finalize_sheet(replace(command, name=text.strip("\"“” ")[:200]))
    if command.missing == "target":
        url = _URL_ID.search(text)
        if url:
            return finalize_sheet(replace(command, spreadsheet_id=url.group(1)))
        return finalize_sheet(replace(command, name=text.strip("\"“” ")[:200]))
    if command.missing == "rows":
        return finalize_sheet(replace(command, rows=parse_rows(text)))
    if command.missing == "cell":
        cell = _CELL_REF.search(text)
        return finalize_sheet(replace(command, cell=cell.group(1).upper())) if cell else command
    if command.missing == "value":
        return finalize_sheet(replace(command, value=text))
    return command
