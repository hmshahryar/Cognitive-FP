"""Read Google Sheets and prepare one exact change for the user to approve.

Spreadsheet content is untrusted and never reaches a model here. Nothing in this module
creates or changes a sheet.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.integrations import google_sheets as gs
from cognitive_mesh.integrations.gateway import (
    IntegrationGateway,
    IntegrationUnavailableError,
    ToolExecutionError,
)
from cognitive_mesh.models import Publication, PublicationStatus, Task
from cognitive_mesh.workflows.sheet_commands import SheetCommand

READ_FAILED = (
    "I couldn't read Google Sheets just now, so nothing was changed. "
    "Press Retry, or reconnect Google Sheets on the Connectors page."
)
MAX_LISTED = 15
MAX_READ_ROWS = 20
READ_WINDOW = "A1:Z20"
_SHEET_TOOLS = (gs.CREATE_TOOL, gs.APPEND_TOOL, gs.UPDATE_TOOL)


class SheetStop(Exception):
    """The command can't continue. `message` tells the user what to do."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PreparedSheetAction:
    action_type: str
    payload: dict[str, Any]
    text: str


async def _read(
    gateway: IntegrationGateway, user_id: str, tool: str, arguments: dict[str, Any]
) -> Any:
    try:
        return await gateway.execute_read_only(
            user_id=user_id, toolkit_slug=gs.TOOLKIT, tool_slug=tool, arguments=arguments
        )
    except (IntegrationUnavailableError, ToolExecutionError) as exc:
        raise SheetStop(READ_FAILED) from exc


async def last_spreadsheet(session: AsyncSession, chat_id: uuid.UUID) -> str | None:
    """The sheet this chat last changed, for commands such as "add this to it"."""
    return await session.scalar(
        select(Publication.external_id)
        .join(Task, Task.id == Publication.task_id)
        .where(
            Task.chat_id == chat_id,
            Publication.tool_slug.in_(_SHEET_TOOLS),
            Publication.status == PublicationStatus.PUBLISHED,
        )
        .order_by(Publication.created_at.desc())
        .limit(1)
    )


async def _search(gateway: IntegrationGateway, user_id: str, query: str | None) -> list[dict]:
    arguments: dict[str, Any] = {"max_results": MAX_LISTED, "search_type": "name"}
    if query:
        arguments["query"] = query
    data = await _read(gateway, user_id, gs.SEARCH_TOOL, arguments)
    rows = data.get("spreadsheets") if isinstance(data, dict) else None
    return [
        row for row in rows or []
        if isinstance(row, dict) and gs.SPREADSHEET_ID.match(str(row.get("id") or ""))
    ]


async def _resolve(
    gateway: IntegrationGateway, user_id: str, command: SheetCommand, last_id: str | None
) -> tuple[str, str]:
    """(spreadsheet id, display name). Only an exact, single name match is accepted."""
    if command.spreadsheet_id:
        return command.spreadsheet_id, command.name or "Linked spreadsheet"
    if command.use_last:
        if not last_id:
            raise SheetStop("Which sheet do you mean? Reply with its exact name or paste its link.")
        return last_id, "Latest sheet in this chat"
    wanted = (command.name or "").strip().casefold()
    matches = await _search(gateway, user_id, command.name)
    exact = [row for row in matches if str(row.get("name", "")).strip().casefold() == wanted]
    if len(exact) == 1:
        return str(exact[0]["id"]), str(exact[0]["name"])
    if len(exact) > 1:
        raise SheetStop("More than one sheet has that name. Paste the link to the one you mean.")
    if matches:
        names = "\n".join(f"- {row.get('name')}" for row in matches[:5])
        raise SheetStop(f"I couldn't find a sheet with that exact name. Did you mean:\n{names}")
    raise SheetStop("I couldn't find that sheet. Check its name, or paste its link.")


async def _resolve_tab(
    gateway: IntegrationGateway, user_id: str, spreadsheet_id: str, requested: str | None
) -> str:
    data = await _read(gateway, user_id, gs.TABS_TOOL, {"spreadsheet_id": spreadsheet_id})
    tabs = [str(t) for t in (data.get("sheet_names") or [])] if isinstance(data, dict) else []
    if not tabs:
        raise SheetStop(READ_FAILED)
    if requested:
        found = [t for t in tabs if t.casefold() == requested.casefold()]
        if len(found) == 1:
            return found[0]
        raise SheetStop("I couldn't find that tab. Available tabs: " + ", ".join(tabs[:10]))
    if len(tabs) == 1:
        return tabs[0]
    raise SheetStop(
        "This sheet has several tabs. Send the command again and name one with: tab <name>. "
        "Available tabs: " + ", ".join(tabs[:10])
    )


async def answer_read(
    gateway: IntegrationGateway, user_id: str, command: SheetCommand, last_id: str | None
) -> str:
    if command.kind == "list":
        sheets = await _search(gateway, user_id, None)
        if not sheets:
            return "I couldn't find any spreadsheets."
        return "Your recent sheets:\n" + "\n".join(f"- {row.get('name')}" for row in sheets)
    spreadsheet_id, name = await _resolve(gateway, user_id, command, last_id)
    tab = await _resolve_tab(gateway, user_id, spreadsheet_id, command.tab)
    cells = command.cell_range or READ_WINDOW
    data = await _read(
        gateway, user_id, gs.VALUES_TOOL,
        {"spreadsheet_id": spreadsheet_id, "range": f"{gs.quote_tab(tab)}!{cells}"},
    )
    rows = data.get("values") if isinstance(data, dict) else None
    if not rows:
        return f"{name} ({tab}) has no data in {cells}."
    shown = rows[:MAX_READ_ROWS]
    lines = "\n".join(" | ".join(str(cell)[:80] for cell in row) for row in shown)
    more = f"\nShowing the first {MAX_READ_ROWS} rows." if len(rows) > MAX_READ_ROWS else ""
    return f"{name} ({tab}, {cells}):\n{lines}{more}"


async def prepare_change(
    gateway: IntegrationGateway, user_id: str, command: SheetCommand, last_id: str | None
) -> PreparedSheetAction:
    rows = [list(row) for row in command.rows]
    if command.kind == "create":
        payload = gs.build_create_payload(name=command.name or "", rows=rows)
        return PreparedSheetAction(gs.CREATE_ACTION, payload, f"Create sheet: {command.name}")

    spreadsheet_id, name = await _resolve(gateway, user_id, command, last_id)
    tab = await _resolve_tab(gateway, user_id, spreadsheet_id, command.tab)
    if command.kind == "append":
        payload = gs.build_append_payload(
            spreadsheet_id=spreadsheet_id, name=name, tab=tab, rows=rows
        )
        return PreparedSheetAction(gs.APPEND_ACTION, payload, f"Add {len(rows)} row(s) to {name}")

    cell = command.cell or ""
    data = await _read(
        gateway, user_id, gs.VALUES_TOOL,
        {"spreadsheet_id": spreadsheet_id, "range": f"{gs.quote_tab(tab)}!{cell}"},
    )
    values = data.get("values") if isinstance(data, dict) else None
    previous = str(values[0][0]) if values and values[0] else ""
    payload = gs.build_update_payload(
        spreadsheet_id=spreadsheet_id, name=name, tab=tab, cell=cell,
        value=command.value or "", previous=previous[:gs.MAX_CELL_CHARS],
    )
    return PreparedSheetAction(gs.UPDATE_ACTION, payload, f"Update {cell} in {name}")
