import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from support import fetch

pytestmark = pytest.mark.anyio


async def test_the_guide_names_the_hook_this_server_and_the_enabled_keys(
    server_url: str, database_url: str
) -> None:
    status, _, body = await fetch(server_url, "/bfbc2/play")
    page = body.decode()
    assert status == 200
    keys = ("Online play", "VIP map packs", "Veteran rank", "6 keys")
    for shown in ("dinput8.dll", "BFBC2Game.exe", *keys):
        assert shown in page
    # This server is not the hook's default, so the guide tells the hook where it is.
    relay = server_url.rstrip("/")
    assert "Point the hook at this server" in page
    assert f"[Server]\nUrl={relay}</pre>" in page
    assert ">4</span>Create an account" in page

    # A key the admin disabled is no longer offered.
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE product_key SET enabled = false WHERE code = 'ACTIVATE-VETERAN'")
        )
    await engine.dispose()
    _, _, body = await fetch(server_url, "/pl/bfbc2/play")
    page = body.decode()
    assert "Status weterana" not in page
    for shown in ("5 kluczy", "Gra sieciowa", "Wskaż hookowi ten serwer"):
        assert shown in page
