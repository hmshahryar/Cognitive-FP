from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession


def upsert_insert(session: AsyncSession, model: type):  # type: ignore[no-untyped-def]
    """Dialect-specific INSERT supporting ON CONFLICT (SQLite now, Postgres later)."""
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        return sqlite.insert(model)
    if dialect == "postgresql":
        return postgresql.insert(model)
    raise NotImplementedError(f"Unsupported database dialect: {dialect}")
