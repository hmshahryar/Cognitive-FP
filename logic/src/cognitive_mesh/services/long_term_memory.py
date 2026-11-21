"""Long-term memory on LangGraph's AsyncSqliteStore.

Each workspace has its own namespace, and every method requires the workspace id from the
verified session. Only what the user explicitly asked to remember is stored. Lookup uses
keyword matching, so no embedding tokens are spent.
"""

import asyncio
import contextlib
import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite
from langgraph.store.sqlite.aio import AsyncSqliteStore

MAX_LISTED = 200
MAX_CONTEXT_MEMORIES = 5
_WORD = re.compile(r"[^\W_]{3,}")
_STOP_WORDS = {"the", "and", "for", "with", "about", "write", "post", "email", "that", "this"}


@dataclass(frozen=True)
class MemoryItem:
    id: str
    kind: str  # "contact" | "fact"
    text: str
    name: str | None
    email: str | None
    updated_at: datetime


def _key(kind: str, value: str) -> str:
    return f"{kind}-{hashlib.sha256(value.lower().encode()).hexdigest()[:24]}"


def _namespace(workspace_id: uuid.UUID) -> tuple[str, ...]:
    return ("workspaces", str(workspace_id), "memories")


class LongTermMemory:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._store: AsyncSqliteStore | None = None
        self._lock: asyncio.Lock | None = None

    async def _open(self) -> AsyncSqliteStore:
        self._lock = self._lock or asyncio.Lock()
        async with self._lock:
            if self._store is None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._conn = await aiosqlite.connect(self._path.as_posix(), isolation_level=None)
                store = AsyncSqliteStore(self._conn)
                await store.setup()
                self._store = store
            return self._store

    async def close(self) -> None:
        store, conn = self._store, self._conn
        self._store, self._conn, self._lock = None, None, None
        task = getattr(store, "_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if conn is not None:
            await conn.close()

    async def remember_contact(self, workspace_id: uuid.UUID, name: str, email: str) -> None:
        store = await self._open()
        await store.aput(
            _namespace(workspace_id),
            _key("contact", name),
            {"kind": "contact", "name": name, "email": email, "text": f"{name}: {email}"},
            index=False,
        )

    async def remember_fact(self, workspace_id: uuid.UUID, text: str) -> None:
        store = await self._open()
        await store.aput(
            _namespace(workspace_id),
            _key("fact", text),
            {"kind": "fact", "name": None, "email": None, "text": text},
            index=False,
        )

    async def list_items(self, workspace_id: uuid.UUID) -> list[MemoryItem]:
        store = await self._open()
        rows = await store.asearch(_namespace(workspace_id), limit=MAX_LISTED)
        items = [
            MemoryItem(
                id=row.key,
                kind=row.value["kind"],
                text=row.value["text"],
                name=row.value.get("name"),
                email=row.value.get("email"),
                updated_at=row.updated_at,
            )
            for row in rows
        ]
        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    async def delete(self, workspace_id: uuid.UUID, memory_id: str) -> bool:
        store = await self._open()
        namespace = _namespace(workspace_id)
        if await store.aget(namespace, memory_id) is None:
            return False
        await store.adelete(namespace, memory_id)
        return True

    async def forget(self, workspace_id: uuid.UUID, phrase: str) -> tuple[int, int]:
        """(deleted, matched). Deletes only when the phrase identifies exactly one memory."""
        wanted = phrase.strip().lower()
        items = await self.list_items(workspace_id)
        exact = [i for i in items if wanted in {(i.name or "").lower(), i.text.lower()}]
        matches = exact or [i for i in items if wanted in i.text.lower()]
        if len(matches) != 1:
            return 0, len(matches)
        await self.delete(workspace_id, matches[0].id)
        return 1, 1

    async def contacts(self, workspace_id: uuid.UUID) -> dict[str, str]:
        return {
            item.name: item.email
            for item in await self.list_items(workspace_id)
            if item.kind == "contact" and item.name and item.email
        }

    async def relevant_facts(self, workspace_id: uuid.UUID, query: str) -> list[str]:
        """Facts for the writer. Contact addresses are never included."""
        facts = [i.text for i in await self.list_items(workspace_id) if i.kind == "fact"]
        words = {w.lower() for w in _WORD.findall(query)} - _STOP_WORDS
        ranked = sorted(
            facts,
            key=lambda fact: len(words & {w.lower() for w in _WORD.findall(fact)}),
            reverse=True,
        )
        return ranked[:MAX_CONTEXT_MEMORIES]
