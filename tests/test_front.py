import pytest
from support import THEATER, enter_as_player, fetch, game, host_game, ids, let_in

pytestmark = pytest.mark.anyio

# What a Bad Company 2 dedicated server publishes once its level has loaded.
ATACAMA_HARDCORE = [
    ("B-U-level", "Levels/MP_005"),
    ("B-U-gamemode", "CONQUEST"),
    ("B-U-type", "HARDCORE"),
    ("B-U-mod", "BC2"),
    ("B-U-location", "UNKNOWN"),
]


@pytest.mark.usefixtures("open_hosting")
async def test_the_game_front_recommends_the_busiest_server_with_its_map_and_mode(
    server_url: str,
) -> None:
    status, _, body = await fetch(server_url, "/bfbc2")
    page = body.decode()
    assert status == 200
    assert "No servers are online right now." in page
    assert 'href="/bfbc2/host"' in page
    # The steps to set up the game name its executable and the key for online play.
    assert "BFBC2Game.exe" in page
    assert "ACTIVATE-GAME" in page

    async with game(server_url) as server, game(server_url) as player:
        created = await host_game(server, {"NAME": "Invented Dune Hardcore"})
        await server.send_theater(THEATER, "UGAM", [*ids(created), *ATACAMA_HARDCORE])
        await enter_as_player(player, "dune@example.invalid", "DuneWalker")
        await let_in(server, player, created)

        status, _, body = await fetch(server_url, "/bfbc2")
        page = body.decode()
        assert status == 200
        assert 'href="/bfbc2/servers/invented-dune-hardcore"' in page
        for shown in ("Atacama Desert", "Conquest", "Hardcore", "Base game", "Unknown", "31 free"):
            assert shown in page
        assert "Up since" in page

        _, _, body = await fetch(server_url, "/pl/bfbc2")
        page = body.decode()
        for shown in ("Pustynia Atakama", "Podbój", "Gra podstawowa", "Wolne miejsca: 31"):
            assert shown in page
        assert 'href="/pl/bfbc2/servers/invented-dune-hardcore"' in page
