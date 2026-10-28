from importlib.resources import files

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.anyio


async def test_players_registered_before_their_origin_was_kept_count_as_made_in_bad_company_2(
    database_url: str,
) -> None:
    # A database of its own, as the server's database is at the newest revision already.
    admin = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(text("DROP DATABASE IF EXISTS before_origins"))
        await connection.execute(text("CREATE DATABASE before_origins"))
    await admin.dispose()
    engine = create_async_engine(make_url(database_url).set(database="before_origins"))
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade, "0021")
        await connection.execute(
            text(
                "INSERT INTO account (nuid, password_hash, country, server) VALUES"
                " ('ann@example.test', 'hash', 'PL', false), ('bfbc2-server', NULL, '', true)"
            )
        )
        await connection.run_sync(_upgrade, "head")
        rows = await connection.execute(text("SELECT nuid, made_in FROM account"))
        made_in = {nuid: game for nuid, game in rows}
    await engine.dispose()
    assert made_in == {"ann@example.test": "bfbc2", "bfbc2-server": None}


def _upgrade(connection: Connection, revision: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(files("openfesl.storage") / "migrations"))
    config.attributes["connection"] = connection
    command.upgrade(config, revision)
