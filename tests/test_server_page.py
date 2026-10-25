import re

import anyio
import pytest
from support import (
    FESL,
    THEATER,
    Answer,
    Game,
    enter_as_player,
    fetch,
    game,
    host_game,
    ids,
    let_in,
    log_in_persona,
    markup,
)

from openfesl.core.games import Games, Roster, load_games
from openfesl.server import app

pytestmark = pytest.mark.anyio

# What a Bad Company 2 dedicated server publishes about its game and its players.
RUSH = [("B-U-level", "Levels/MP_002GR"), ("B-U-gamemode", "RUSH"), ("B-U-location", "UNKNOWN")]
DETAILS = [
    ("D-FriendlyFire", "1.0000"),
    ("D-KillCam", "0"),
    ("D-ServerDescriptionCount", "2"),
    ("D-ServerDescription0", "Welcome to the invented "),
    ("D-ServerDescription1", "harbour."),
    # The server's list lags: a player who left is still on it, one who just came is not yet.
    ("D-pdat00", "LeftAlready|900|9|1|50|0"),
    ("D-pdat01", "SlowRunner|300|3|4|10|0"),
    ("D-pdat02", "FastRunner|1200|12|2|80|0"),
    ("D-pdat03", "|0|0|0|0"),
]


async def host_harbour(server: Game) -> Answer:
    created = await host_game(server, {"NAME": "Invented Harbour Rush"})
    await server.send_theater(THEATER, "UGAM", [*ids(created), *RUSH])
    await server.send_theater(THEATER, "UGDE", [*ids(created), *DETAILS])
    return created


@pytest.fixture
def scored_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    # Game data that knows what the server's numbers hold; the server starts with it.
    def load() -> Games:
        games = load_games()
        bfbc2 = games.by_id("bfbc2")
        assert bfbc2 is not None
        roster = Roster(attribute="D-pdat", fields=["name", "score", "kills", "deaths", ""])
        listing = bfbc2.listing.model_copy(update={"roster": roster})
        return Games([bfbc2.model_copy(update={"listing": listing})])

    monkeypatch.setattr(app, "load_games", load)


def names(body: bytes) -> list[str]:
    """Return the players a server's scoreboard shows, in its order."""
    table = markup(body).split('id="server-scoreboard"')[1]
    return re.findall(r'<div role="cell"><a href="/(?:pl/)?bfbc2/players/([^"]+)">', table)


@pytest.mark.usefixtures("open_hosting")
async def test_a_servers_page_shows_its_settings_message_and_players(server_url: str) -> None:
    async with (
        game(server_url) as server,
        game(server_url) as slow,
        game(server_url) as fast,
        game(server_url) as idle,
    ):
        created = await host_harbour(server)
        for player, name in ((slow, "SlowRunner"), (fast, "FastRunner")):
            await enter_as_player(player, f"{name.lower()}@example.invalid", name)
            await let_in(server, player, created)
        await log_in_persona(idle, FESL, "idle@example.invalid", "IdleWalker")

        status, _, body = await fetch(server_url, "/bfbc2/servers/invented-harbour-rush")
        page = markup(body)
        assert status == 200
        for shown in (
            "Rush",
            "Valparaiso",
            "Friendly fire",
            "Kill cam",
            "Welcome to the invented harbour.",
        ):
            assert shown in page
        assert re.search(r"Friendly fire</dt>.*?</span>On</dd>", page)
        assert re.search(r"Kill cam</dt>.*?</span>Off</dd>", page)
        # The game's own players, in the order the server lists them; its numbers stay hidden
        # while the game data does not say what they hold.
        assert names(body) == ["SlowRunner", "FastRunner"]
        assert ">Score</div>" not in page
        # Who is online without being on a server.
        assert 'href="/bfbc2/players/IdleWalker"' in page

        _, _, body = await fetch(
            server_url, "/bfbc2/servers/invented-harbour-rush?player=fastrunner"
        )
        assert re.search(r'data-key="FastRunner" aria-current="true"', markup(body))

        # The game's own Polish words, with on and off the right way round.
        _, _, body = await fetch(server_url, "/pl/bfbc2/servers/invented-harbour-rush")
        page = markup(body)
        assert re.search(r"Własny ogień</dt>.*?</span>Wł\.</dd>", page)
        assert re.search(r"Kamera zabójcy</dt>.*?</span>Wył\.</dd>", page)

    # A server that stopped hosting keeps its page with what it published last, once the
    # server noticed that its connection closed.
    with anyio.fail_after(5):
        while "Not hosting since" not in page:
            status, _, body = await fetch(server_url, "/bfbc2/servers/invented-harbour-rush")
            page = markup(body)
    assert status == 200
    assert "Last known settings" in page
    assert "No scoreboard while the server is not hosting." in page

    # An address no server had answers 404 with a search for its words.
    status, _, body = await fetch(server_url, "/bfbc2/servers/frost-harbour-rush")
    assert status == 404
    assert 'value="frost harbour rush"' in markup(body)


@pytest.mark.usefixtures("scored_roster", "open_hosting")
async def test_numbers_the_game_data_names_show_on_the_scoreboard_by_score(server_url: str) -> None:
    async with game(server_url) as server, game(server_url) as slow, game(server_url) as fast:
        created = await host_harbour(server)
        for player, name in ((slow, "SlowRunner"), (fast, "FastRunner")):
            await enter_as_player(player, f"{name.lower()}@example.invalid", name)
            await let_in(server, player, created)

        _, _, body = await fetch(server_url, "/bfbc2/servers/invented-harbour-rush")
        scoreboard = markup(body).split('id="server-scoreboard"')[1]
        assert names(body) == ["FastRunner", "SlowRunner"]
        for heading in ("Score", "Kills", "Deaths"):
            assert heading in scoreboard
        assert re.findall(r'data-count="(\d+)"', scoreboard) == ["1200", "12", "2", "300", "3", "4"]
