import pytest
from fastapi import FastAPI
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from cognitive_mesh.models import User, Workspace


async def test_migrations_create_tables_and_unique_keys(app: FastAPI) -> None:
    def _inspect(sync_conn):  # type: ignore[no-untyped-def]
        insp = inspect(sync_conn)
        indexes = {
            table: {ix["name"]: ix["unique"] for ix in insp.get_indexes(table)}
            for table in ("users", "workspaces")
        }
        return set(insp.get_table_names()), indexes

    async with app.state.db.engine.connect() as conn:
        tables, indexes = await conn.run_sync(_inspect)

    assert {"users", "workspaces", "alembic_version"} <= tables
    assert bool(indexes["users"]["ix_users_clerk_user_id"])
    assert bool(indexes["workspaces"]["ix_workspaces_owner_id"])


async def test_sqlite_foreign_keys_are_enforced(app: FastAPI) -> None:
    async with app.state.db.engine.connect() as conn:
        result = await conn.exec_driver_sql("PRAGMA foreign_keys")
        assert result.scalar() == 1


async def test_health_checks_database(client) -> None:  # type: ignore[no-untyped-def]
    res = await client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["database"] == "ok"


async def test_database_rejects_second_workspace_for_same_user(app: FastAPI) -> None:
    async with app.state.db.sessionmaker() as session:
        user = User(clerk_user_id="user_dupe")
        session.add(user)
        await session.flush()
        session.add(Workspace(owner_id=user.id))
        await session.commit()

        session.add(Workspace(owner_id=user.id))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        count = await session.scalar(select(func.count()).select_from(Workspace))
        assert count == 1
