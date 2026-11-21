"""The exact Google Sheets actions that can be requested, and their payload rules.

Tool slugs, inputs and response shapes were verified against Composio's toolkit. The payload
is what the user approves and what gets signed:

    {"spreadsheet": {id, name, tab}, "arguments": <exact tool input>, "rows": [...], ...}

Values are always written as RAW, so a cell such as "=IMPORTXML(...)" stays plain text and
can never run as a formula.
"""

import re
from dataclasses import dataclass
from typing import Any

TOOLKIT = "googlesheets"
CREATE_ACTION = "sheets.create_spreadsheet"
APPEND_ACTION = "sheets.append_rows"
UPDATE_ACTION = "sheets.update_cell"
CREATE_TOOL = "GOOGLESHEETS_CREATE_GOOGLE_SHEET1"
APPEND_TOOL = "GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND"
UPDATE_TOOL = "GOOGLESHEETS_VALUES_UPDATE"
SEARCH_TOOL = "GOOGLESHEETS_SEARCH_SPREADSHEETS"
TABS_TOOL = "GOOGLESHEETS_GET_SHEET_NAMES"
VALUES_TOOL = "GOOGLESHEETS_VALUES_GET"

WRITE_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
)
MAX_ROWS = 200
MAX_COLUMNS = 26
MAX_CELL_CHARS = 5000
MAX_NAME_CHARS = 200

SPREADSHEET_ID = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
CELL = re.compile(r"^[A-Z]{1,3}[1-9]\d{0,6}$")
_APPEND_KEYS = {
    "spreadsheetId", "range", "valueInputOption", "insertDataOption", "majorDimension", "values",
}
_UPDATE_KEYS = {
    "spreadsheet_id", "range", "value_input_option", "major_dimension", "auto_expand_sheet",
    "values",
}


class InvalidSheetActionError(ValueError):
    pass


@dataclass(frozen=True)
class FollowUp:
    """A second call that completes one approved action."""

    tool_slug: str
    arguments: dict[str, Any]


def quote_tab(tab: str) -> str:
    return "'" + tab.replace("'", "''") + "'"


def _check_rows(rows: Any, *, allow_empty: bool = False) -> None:
    if not isinstance(rows, list) or (not rows and not allow_empty) or len(rows) > MAX_ROWS:
        raise InvalidSheetActionError(f"Provide 1 to {MAX_ROWS} rows")
    for row in rows:
        if not isinstance(row, list) or not row or len(row) > MAX_COLUMNS:
            raise InvalidSheetActionError(f"Each row needs 1 to {MAX_COLUMNS} values")
        if any(not isinstance(cell, str) or len(cell) > MAX_CELL_CHARS for cell in row):
            raise InvalidSheetActionError("Cells must be text within the size limit")


def _check_target(payload: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    if set(payload) != keys:
        raise InvalidSheetActionError(f"Payload must have exactly {sorted(keys)}")
    target, arguments = payload["spreadsheet"], payload["arguments"]
    if not isinstance(target, dict) or not isinstance(arguments, dict):
        raise InvalidSheetActionError("Spreadsheet and arguments are required")
    if not isinstance(target.get("name"), str) or not target["name"].strip():
        raise InvalidSheetActionError("A spreadsheet name is required")
    if len(target["name"]) > MAX_NAME_CHARS:
        raise InvalidSheetActionError("The spreadsheet name is too long")
    return arguments


def _check_existing(target: dict[str, Any]) -> tuple[str, str]:
    spreadsheet_id, tab = target.get("id"), target.get("tab")
    if not isinstance(spreadsheet_id, str) or not SPREADSHEET_ID.match(spreadsheet_id):
        raise InvalidSheetActionError("A valid spreadsheet id is required")
    if not isinstance(tab, str) or not tab:
        raise InvalidSheetActionError("A worksheet tab is required")
    return spreadsheet_id, tab


def validate_create(payload: dict[str, Any]) -> None:
    arguments = _check_target(payload, {"spreadsheet", "arguments", "rows"})
    if arguments != {"title": payload["spreadsheet"]["name"]}:
        raise InvalidSheetActionError("The title must match the approved name")
    _check_rows(payload["rows"], allow_empty=True)


def validate_append(payload: dict[str, Any]) -> None:
    arguments = _check_target(payload, {"spreadsheet", "arguments", "rows"})
    spreadsheet_id, tab = _check_existing(payload["spreadsheet"])
    if set(arguments) != _APPEND_KEYS or arguments["spreadsheetId"] != spreadsheet_id:
        raise InvalidSheetActionError("Unexpected append fields")
    if arguments["range"] != tab or arguments["values"] != payload["rows"]:
        raise InvalidSheetActionError("The write must match the approved rows and tab")
    if (
        arguments["valueInputOption"] != "RAW"
        or arguments["insertDataOption"] != "INSERT_ROWS"
        or arguments["majorDimension"] != "ROWS"
    ):
        raise InvalidSheetActionError("Rows must be inserted as plain text")
    _check_rows(payload["rows"])


def validate_update(payload: dict[str, Any]) -> None:
    arguments = _check_target(payload, {"spreadsheet", "arguments", "cell", "value", "previous"})
    spreadsheet_id, tab = _check_existing(payload["spreadsheet"])
    cell, value = payload["cell"], payload["value"]
    if not isinstance(cell, str) or not CELL.match(cell):
        raise InvalidSheetActionError("A valid cell is required")
    if not isinstance(value, str) or len(value) > MAX_CELL_CHARS:
        raise InvalidSheetActionError("The value must be text within the size limit")
    expected = {
        "spreadsheet_id": spreadsheet_id,
        "range": f"{quote_tab(tab)}!{cell}",
        "value_input_option": "RAW",
        "major_dimension": "ROWS",
        "auto_expand_sheet": False,
        "values": [[value]],
    }
    if set(arguments) != _UPDATE_KEYS or arguments != expected:
        raise InvalidSheetActionError("The write must match the approved cell and value")


def build_create_payload(*, name: str, rows: list[list[str]]) -> dict[str, Any]:
    payload = {
        "spreadsheet": {"id": None, "name": name, "tab": None},
        "arguments": {"title": name},
        "rows": rows,
    }
    validate_create(payload)
    return payload


def build_append_payload(
    *, spreadsheet_id: str, name: str, tab: str, rows: list[list[str]]
) -> dict[str, Any]:
    payload = {
        "spreadsheet": {"id": spreadsheet_id, "name": name, "tab": tab},
        "arguments": {
            "spreadsheetId": spreadsheet_id,
            "range": tab,
            "valueInputOption": "RAW",
            "insertDataOption": "INSERT_ROWS",
            "majorDimension": "ROWS",
            "values": rows,
        },
        "rows": rows,
    }
    validate_append(payload)
    return payload


def build_update_payload(
    *, spreadsheet_id: str, name: str, tab: str, cell: str, value: str, previous: str
) -> dict[str, Any]:
    payload = {
        "spreadsheet": {"id": spreadsheet_id, "name": name, "tab": tab},
        "arguments": {
            "spreadsheet_id": spreadsheet_id,
            "range": f"{quote_tab(tab)}!{cell}",
            "value_input_option": "RAW",
            "major_dimension": "ROWS",
            "auto_expand_sheet": False,
            "values": [[value]],
        },
        "cell": cell,
        "value": value,
        "previous": previous,
    }
    validate_update(payload)
    return payload


def create_follow_up(payload: dict[str, Any], data: Any) -> FollowUp | None:
    """After creating the file, write the approved rows into its real first tab."""
    rows = payload.get("rows") or []
    if not rows:
        return None
    if not isinstance(data, dict):
        raise InvalidSheetActionError("The new spreadsheet was not returned")
    try:
        tab = data["sheets"][0]["properties"]["title"]
    except (KeyError, IndexError, TypeError) as exc:
        raise InvalidSheetActionError("The new worksheet was not returned") from exc
    spreadsheet_id = data.get("spreadsheetId")
    if not isinstance(spreadsheet_id, str) or not SPREADSHEET_ID.match(spreadsheet_id):
        raise InvalidSheetActionError("The new spreadsheet id was not returned")
    return FollowUp(
        APPEND_TOOL,
        {
            "spreadsheetId": spreadsheet_id,
            "range": tab,
            "valueInputOption": "RAW",
            "insertDataOption": "INSERT_ROWS",
            "majorDimension": "ROWS",
            "values": rows,
        },
    )


def result_id(data: Any) -> str | None:
    value = data.get("spreadsheetId") if isinstance(data, dict) else None
    return value if isinstance(value, str) and SPREADSHEET_ID.match(value) else None


def result_url(_data: Any, spreadsheet_id: str | None) -> str | None:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit" if spreadsheet_id else None
