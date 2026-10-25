import re

import pytest
from support import THEATER, enter_as_player, fetch, game, host_game, ids, let_in, markup

pytestmark = pytest.mark.anyio


def listed(body: bytes) -> list[str]:
    """Return the addresses of the servers a list shows, in its order."""
    return re.findall(
        r'<a href="/(?:pl/)?bfbc2/servers/([^"]+)" class="server-name">', markup(body)
    )


async def test_an_empty_list_points_to_hosting_and_playing(server_url: str) -> None:
    status, _, body = await fetch(server_url, "/bfbc2/servers")
    page = markup(body)
    assert status == 200
    assert "No servers are online right now." in page
    assert 'href="/bfbc2/host"' in page
    assert 'href="/bfbc2/play"' in page


@pytest.mark.usefixtures("open_hosting")
async def test_the_server_list_filters_sorts_and_opens_a_servers_details(server_url: str) -> None:
    async with (
        game(server_url) as rush,
        game(server_url) as conquest,
        game(server_url) as other,
        game(server_url) as player,
    ):
        created = await host_game(rush, {"NAME": "Invented Harbour Rush"})
        await rush.send_theater(
            THEATER,
            "UGAM",
            [*ids(created), ("B-U-level", "Levels/MP_006GR"), ("B-U-gamemode", "RUSH")],
        )
        await enter_as_player(player, "harbour@example.invalid", "HarbourRunner")
        await let_in(rush, player, created)
        created = await host_game(conquest, {"NAME": "Invented Atacama Conquest"})
        await conquest.send_theater(
            THEATER,
            "UGAM",
            [*ids(created), ("B-U-level", "Levels/MP_005"), ("B-U-gamemode", "CONQUEST")],
        )
        # The answer to the bracket that follows comes after the change took effect.
        await conquest.theater(THEATER, "UBRA", ids(created))
        # A level the game data does not name shows its id.
        created = await host_game(other, {"NAME": "Invented Unknown Level"})
        await other.send_theater(THEATER, "UGAM", [*ids(created), ("B-U-level", "Levels/XP_999")])
        await other.theater(THEATER, "UBRA", ids(created))

        status, _, body = await fetch(server_url, "/bfbc2/servers")
        page = markup(body)
        assert status == 200
        # The busiest first, then by age.
        assert listed(body) == [
            "invented-harbour-rush",
            "invented-atacama-conquest",
            "invented-unknown-level",
        ]
        for shown in ("Arica Harbor", "Atacama Desert", "Levels/XP_999", "Servers found:"):
            assert shown in page

        _, _, body = await fetch(server_url, "/bfbc2/servers?mode=conquest")
        assert listed(body) == ["invented-atacama-conquest"]
        _, _, body = await fetch(server_url, "/bfbc2/servers?sort=name")
        assert listed(body) == [
            "invented-atacama-conquest",
            "invented-harbour-rush",
            "invented-unknown-level",
        ]
        # A form sends the button pressed last, after the hidden field that keeps the choice.
        _, _, body = await fetch(server_url, "/bfbc2/servers?mode=conquest&mode=rush")
        assert listed(body) == ["invented-harbour-rush"]

        # Without scripts, a row's details open with the page; they name who plays there.
        _, _, body = await fetch(server_url, "/bfbc2/servers?open=invented-harbour-rush")
        page = markup(body)
        assert re.search(r'id="row-invented-harbour-rush" data-key="[^"]+" data-open', page)
        assert 'href="/bfbc2/players/HarbourRunner"' in page

        # A list filtered to nothing says how many servers there are and how to widen it.
        _, _, body = await fetch(server_url, "/pl/bfbc2/servers?mode=rush&hardcore=1")
        page = markup(body)
        assert listed(body) == []
        assert "Łącznie działają 3 serwery." in page
        assert 'href="/pl/bfbc2/servers?mode=rush"' in page
        assert 'href="/pl/bfbc2/servers?hardcore=1"' in page
