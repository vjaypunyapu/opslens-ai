"""Alembic environment configuration for OpsLens AI."""
import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import your declarative Base so Alembic can detect model changes
from apps.api.db.session import Base, _async_db_url, _extract_sslmode, _ssl_connect_args  # noqa: F401

# Alembic Config object (access alembic.ini values)
config = context.config

# Override sqlalchemy.url from environment variable
# Railway injects postgres:// or postgresql:// — asyncpg needs postgresql+asyncpg://
# `sslmode` (if present) is stripped and translated separately: asyncpg has no
# `sslmode` kwarg, so leaving it in the URL crashes with an unexpected-keyword
# TypeError before a connection is ever attempted.
_db_url, _db_sslmode = _extract_sslmode(_async_db_url(os.environ["DATABASE_URL"]))
config.set_main_option("sqlalchemy.url", _db_url)
_connect_args = _ssl_connect_args(_db_sslmode)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL script, no DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=_connect_args,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
