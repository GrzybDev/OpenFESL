from importlib.resources import files

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine


async def migrate(engine: AsyncEngine) -> None:
    """Bring the database schema up to the server's version.

    Args:
        engine: The database to migrate.
    """
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade)


def _upgrade(connection: Connection) -> None:
    config = Config()
    config.set_main_option("script_location", str(files("openfesl.storage") / "migrations"))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")
