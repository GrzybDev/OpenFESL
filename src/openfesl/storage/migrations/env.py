import asyncio
import os

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from openfesl.storage.models import Base


def run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_from_environment() -> None:
    # Only for `alembic revision --autogenerate` while developing; the server passes its own
    # connection through the config's attributes.
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.connect() as connection:
        await connection.run_sync(run)
    await engine.dispose()


if (connection := context.config.attributes.get("connection")) is not None:
    run(connection)
else:
    asyncio.run(run_from_environment())
