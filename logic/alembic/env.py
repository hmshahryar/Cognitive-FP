import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from cognitive_mesh import models  # noqa: F401  (registers all tables on Base.metadata)
from cognitive_mesh.config import get_settings
from cognitive_mesh.db import Base, Database

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# The database URL always comes from app settings (logic/.env), never alembic.ini.
DATABASE_URL = config.attributes.get("database_url") or get_settings().database_url


def _configure(**kwargs) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        target_metadata=target_metadata,
        # SQLite can't ALTER most things in place; batch mode rebuilds tables safely.
        render_as_batch=DATABASE_URL.startswith("sqlite"),
        compare_type=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=DATABASE_URL, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    db = Database(DATABASE_URL)  # same engine setup (SQLite pragmas) as the app
    async with db.engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await db.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
