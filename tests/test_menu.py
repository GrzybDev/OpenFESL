import re

import pytest
from support import (
    FESL,
    THEATER,
    enter_as_player,
    fetch,
    game,
    host_game,
    ids,
    let_in,
    log_in_persona,
)

pytestmark = pytest.mark.anyio


def counts(body: bytes) -> list[int]:
    """Return the numbers a page refreshes, in the order it shows them."""
    return [int(value) for value in re.findall(rb'data-count="(\d+)"', body)]


@pytest.mark.usefixtures("open_hosting")
async def test_the_menu_counts_each_games_players_and_servers(server_url: str) -> None:
    status, _, body = await fetch(server_url, "/")
    # Online, in matches, in the menus and servers on the tile, then servers and online in the
    # table.
    assert (status, counts(body)) == (200, [0, 0, 0, 0, 0, 0])
    assert b"No servers are online right now." in body

    async with game(server_url) as server, game(server_url) as playing, game(server_url) as idle:
        created = await host_game(server, {"NAME": "Invented Ridge Rush", "MAX-PLAYERS": "8"})
        await enter_as_player(playing, "ridge@example.invalid", "RidgeRunner")
        await let_in(server, playing, created)
        await log_in_persona(idle, FESL, "idle@example.invalid", "IdleWalker")

        status, _, body = await fetch(server_url, "/")
        page = body.decode()
        # The busiest server with room for a squad of four comes after the tile's figures.
        assert (status, counts(body)) == (200, [2, 1, 1, 1, 1, 1, 2])
        assert "Invented Ridge Rush" in page
        assert "7 free" in page
        assert "2 online" in page

        # The shell counts in the page's language, in the form the number takes there.
        _, _, body = await fetch(server_url, "/pl")
        assert "2 osoby online" in body.decode()

        # A server without room for a whole squad is never the one recommended.
        await server.send_theater(THEATER, "UGAM", [*ids(created), ("MAX-PLAYERS", "4")])
        # The answer to the bracket that follows comes after the change took effect.
        await server.theater(THEATER, "UBRA", ids(created))
        _, _, body = await fetch(server_url, "/")
        assert b"No server has room for a squad right now." in body
