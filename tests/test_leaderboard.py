import re
from collections.abc import Mapping

import pytest
from support import (
    FESL,
    THEATER,
    Game,
    enter_theater,
    fetch,
    game,
    host_game,
    let_in,
    log_in_persona,
    markup,
    update_stats,
)

pytestmark = pytest.mark.anyio


async def upload(server: Game, persona: int, stats: Mapping[str, float]) -> None:
    """Add to a player's all-time stats, as a dedicated server does at the end of a round."""
    updates = [(3, key, f"{value:.4f}", "", "0") for key, value in stats.items()]
    answer = await server.request(FESL, "rank", update_stats((str(persona), "1", updates)))
    assert "errorCode" not in answer


def names(body: bytes) -> list[str]:
    """Return the players a leaderboard lists, in its order."""
    return re.findall(r'class="line-name">([^<]+)<', markup(body))


@pytest.mark.usefixtures("open_hosting")
async def test_the_leaderboard_ranks_players_by_their_stats_and_says_where_they_are(
    server_url: str,
) -> None:
    status, _, body = await fetch(server_url, "/bfbc2/leaderboard")
    assert status == 200
    assert b"No statistics yet" in body
    assert b"Players found: 0" in body

    async with game(server_url) as server, game(server_url) as ace, game(server_url) as rookie:
        created = await host_game(server, {"NAME": "Invented Quarry Rush"})
        ace_id, lkey = await log_in_persona(ace, FESL, "ace@example.invalid", "QuarryAce")
        await enter_theater(ace, THEATER, "bfbc2-pc", lkey)
        await let_in(server, ace, created)
        rookie_id, _ = await log_in_persona(rookie, FESL, "rookie@example.invalid", "QuarryRookie")
        async with game(server_url) as veteran:
            veteran_id, _ = await log_in_persona(
                veteran, FESL, "veteran@example.invalid", "QuarryVeteran"
            )
        # The total score is the sum of the game's score buckets.
        await upload(
            server,
            ace_id,
            {"sc_general": 5000, "sc_award": 2000, "kills": 30, "deaths": 10, "time": 7200},
        )
        await upload(server, rookie_id, {"sc_team": 1000, "kills": 50, "dogt": 1})
        await upload(
            server, veteran_id, {"sc_objective": 20000, "kills": 10, "deaths": 20, "dogt": 4}
        )

        _, _, body = await fetch(server_url, "/bfbc2/leaderboard")
        page = markup(body)
        assert names(body) == ["QuarryVeteran", "QuarryAce", "QuarryRookie"]
        # The shield follows the rank the total score reaches.
        for shown in ("Private III", "Private I", "Recruit", "Players found: 3", "2 h"):
            assert shown in page
        rows = page.split('role="row" id="persona-')[1:]
        assert "Offline" in rows[0]
        assert 'href="/bfbc2/servers/invented-quarry-rush"' in rows[1]
        assert "In the menus" in rows[2]

        # A player who never died has as many kills per death as kills.
        _, _, body = await fetch(server_url, "/bfbc2/leaderboard?stat=kd")
        assert names(body) == ["QuarryRookie", "QuarryAce", "QuarryVeteran"]
        assert b">50.00<" in body

        # Players online keep their overall positions.
        _, _, body = await fetch(server_url, "/bfbc2/leaderboard?show=online")
        assert names(body) == ["QuarryAce", "QuarryRookie"]
        positions = re.findall(r'class="line-place line-cell">(\d+)<', markup(body))
        assert positions == ["2", "3"]

        # Finding a player by part of the name opens the page with the player's line.
        _, _, body = await fetch(server_url, "/bfbc2/leaderboard?player=ACE")
        page = markup(body)
        assert "Position 2 by score · page 1" in page
        assert f'id="persona-{ace_id}" data-key="{ace_id}" aria-current="true"' in page
        _, _, body = await fetch(server_url, "/bfbc2/leaderboard?player=Ghost")
        assert b"Ghost is not on this leaderboard." in body
        _, _, body = await fetch(server_url, "/bfbc2/leaderboard?show=online&player=veteran")
        assert b"QuarryVeteran is offline now." in body

        # Polish rank names and numbers, grouped from five digits on.
        _, _, body = await fetch(server_url, "/pl/bfbc2/leaderboard")
        page = markup(body)
        for shown in ("Szeregowy III", "Rekrut", "20\xa0000", "7000", "W rankingu: 3"):
            assert shown in page
