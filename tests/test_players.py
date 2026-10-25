import re
import struct
from base64 import b64encode

import anyio
import pytest
from pydantic import ValidationError
from support import (
    FESL,
    THEATER,
    enter_theater,
    fetch,
    game,
    host_game,
    let_in,
    log_in_persona,
    update_stats,
)

from openfesl.core.games import Game, load_games
from openfesl.web.pages import STATIC

pytestmark = pytest.mark.anyio


def dog_tag(name: str, days: float, rank: int, bronze: int) -> str:
    """Return a dog tag as the player's game keeps it in its dogtags record."""
    tag = struct.pack("<16sfHHHBx", name.encode(), days, 0, 0, bronze, rank)
    return b64encode(tag).decode()


@pytest.mark.usefixtures("open_hosting")
async def test_a_players_page_shows_rank_stats_awards_and_the_dog_tags_taken(
    server_url: str,
) -> None:
    async with game(server_url) as server, game(server_url) as ace, game(server_url) as rival:
        created = await host_game(server, {"NAME": "Invented Ridge Conquest"})
        ace_id, lkey = await log_in_persona(ace, FESL, "ace@example.invalid", "RidgeAce")
        await enter_theater(ace, THEATER, "bfbc2-pc", lkey)
        await let_in(server, ace, created)
        rival_id, _ = await log_in_persona(rival, FESL, "rival@example.invalid", "RidgeRival")
        stats = {
            "sc_general": 20000,
            "sc_award": 4000,
            "sc_assault": 9000,
            "sc_support": 3000,
            "kills": 120,
            "deaths": 80,
            "dogt": 7,
            "time": 36000,
            "p13_00": 3,
            "i01_00": 1,
        }
        updates = [(3, key, f"{value:.4f}", "", "0") for key, value in stats.items()]
        await server.request(FESL, "rank", update_stats((str(ace_id), "1", updates)))
        await server.request(
            FESL, "rank", update_stats((str(rival_id), "1", [(3, "sc_team", "100.0000", "", "0")]))
        )
        # The player's own game keeps the tags it took, one entry per player.
        tags = [
            ("TXN", "AddRecord"),
            ("recordName", "dogtags"),
            ("values.[]", "1"),
            ("values.0.key", str(rival_id)),
            # 6,872 days after 2008-01-01 is 2026-10-25, at 06:00.
            ("values.0.value", dog_tag("RidgeRival", 6872.25, 3, 2)),
            ("TTL", "0"),
        ]
        assert "errorCode" not in await ace.request(FESL, "recp", tags)

        status, _, body = await fetch(server_url, "/bfbc2/players/ridgeace")
        page = body.decode()
        assert status == 200
        # 24,000 score reaches rank 3 and leaves 4,000 to rank 4 at 28,000.
        for shown in (
            "Private III",
            "Specialist I",
            "Specialist I at 28,000 score: 4,000 to go.",
            "In a match",
            'href="/bfbc2/servers/invented-ridge-conquest"',
            "Assault score",
            "+20,000",
            "+4,000",
            "=24,000",
            "1.50",
            "10 h",
            "1 of 2",
        ):
            assert shown in page

        _, _, body = await fetch(server_url, "/bfbc2/players/RidgeRival")
        page = body.decode()
        assert "Online in the game&#39;s menus, not on a server." in page
        assert "Recruit" in page

        _, _, body = await fetch(server_url, "/bfbc2/players/RidgeAce/awards")
        page = body.decode()
        for shown in ("Combat efficiency pin", ">3<", "3 in all", "Elite marksman combat"):
            assert shown in page
        assert "1 of 54 earned" in page
        # p13 draws a crosshair; i01, the one earned insignia, a marksman's sight in tier 4.
        pins = page.split('class="pins"')[1].split('class="insignias"')[0]
        assert '#crosshair"' in pins
        assert re.findall(r'data-tier="(\d)".*?#hex".*?#([a-z-]+)"', page, re.S) == [
            ("4", "marksman")
        ]
        _, _, themes = await fetch(server_url, "/themes.css")
        assert b'[data-game="bfbc2"] [data-tier="4"]{--mark:' in themes

        _, _, body = await fetch(server_url, "/bfbc2/players/RidgeAce/dog-tags")
        page = body.decode()
        for shown in ('href="/bfbc2/players/RidgeRival"', "Private III", "25 Oct 2026, 06:00"):
            assert shown in page

        _, _, body = await fetch(server_url, "/pl/bfbc2/players/RidgeAce/awards")
        assert "Szpilka za skuteczność w walce" in body.decode()

    # A player who logged out is offline, once the server noticed; a name nobody plays under
    # answers 404.
    with anyio.fail_after(5):
        while b"Not online now." not in body:
            _, _, body = await fetch(server_url, "/bfbc2/players/RidgeAce")
    status, _, body = await fetch(server_url, "/bfbc2/players/GhostNine")
    assert status == 404
    assert b"OpenFESL has no Battlefield: Bad Company 2 player called GhostNine." in body
    assert b'value="GhostNine"' in body


def test_every_award_mark_a_game_names_can_be_drawn() -> None:
    drawn = set(re.findall(r'<symbol id="([^"]+)"', (STATIC / "icons.svg").read_text("utf-8")))
    for listed in load_games():
        awards = listed.stats.awards
        named = {*awards.pins.values(), *(mark.glyph for mark in awards.insignias.values())}
        assert named <= drawn, f"{listed.id} names glyphs the icons lack: {named - drawn}"

    # A game whose insignia names a tier its theme has no colours for does not load.
    bfbc2 = load_games().by_id("bfbc2")
    assert bfbc2
    data = bfbc2.model_dump()
    data["theme"]["tiers"] = data["theme"]["tiers"][:4]
    with pytest.raises(ValidationError, match="tier 5"):
        Game.model_validate(data)
