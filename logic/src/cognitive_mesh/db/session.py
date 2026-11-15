from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _configure_sqlite(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")  # SQLite ignores FKs unless enabled
        cursor.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
        cursor.execute("PRAGMA busy_timeout=5000")  # wait for locks instead of failing
        cursor.close()


class Database:
    """Owns the async engine and session factory for one app instance."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        parsed = make_url(url)
        self.is_sqlite = parsed.get_backend_name() == "sqlite"

        if self.is_sqlite and parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)

        self.engine: AsyncEngine = create_async_engine(url, echo=echo)
        if self.is_sqlite:
            _configure_sqlite(self.engine)

        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def dispose(self) -> None:
        await self.engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, rolled back on error."""
    db: Database = request.app.state.db
    async with db.sessionmaker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
