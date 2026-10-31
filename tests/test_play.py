import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from support import fetch, links, markup

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
    assert "Point the hook at this service" in page
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
    for shown in ("5 kluczy", "Gra sieciowa", "Wskaż hookowi ten serwis"):
        assert shown in page


async def test_a_game_its_launcher_signs_in_is_set_up_without_making_an_account_in_it(
    server_url: str,
) -> None:
    # Battlefield Heroes has no login screen: the hook's launcher signs in before it starts it.
    _, _, guide = await fetch(server_url, "/bfheroes/play")
    _, _, front = await fetch(server_url, "/bfheroes")

    assert ">4</span>Sign in" in guide.decode()
    assert "registered on this site or made in another game" in guide.decode()
    step = markup(guide).partition(">4</span>Sign in")[2]
    assert ("/register", "Create an account") in links(step)
    for page in (guide.decode(), front.decode()):
        assert "BFHeroes.exe" in page and "sign-in window" in page
        assert "create an account" not in page
