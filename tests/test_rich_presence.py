import json
import time
from pathlib import Path
from typing import Any

import anyio
import pytest
from support import (
    FESL,
    THEATER,
    Game,
    cli,
    enter_theater,
    game,
    host_game,
    ids,
    let_in,
    log_in_again,
    log_in_persona,
    png_image,
)

from openfesl.core import rich_presence
from openfesl.core.rich_presence import clean_name
from openfesl.core.texts import translate
from openfesl.protocol.relay import Presence, encode_frame
from openfesl.server.settings import Settings

pytestmark = pytest.mark.anyio

APPLICATION = "112233445566778899"
PUBLIC_URL = "https://fesl.example.test"


@pytest.fixture
def presence_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Before the server starts, which reads its settings once.
    monkeypatch.setenv("DISCORD_APPLICATION_ID", APPLICATION)
    monkeypatch.setenv("PUBLIC_URL", PUBLIC_URL)
    monkeypatch.setattr(rich_presence, "UPDATE_INTERVAL", 0.05)


async def shown(game: Game, details: str | None) -> dict[str, Any] | None:
    """Wait for presence with a first line, or for presence cleared, and return its activity."""
    with anyio.fail_after(5):
        while True:
            for frame in [frame for frame in game.frames if isinstance(frame, Presence)]:
                game.frames.remove(frame)
                update = json.loads(frame.body)
                assert update["application"] == APPLICATION
                activity: dict[str, Any] | None = update["activity"]
                if (activity["details"] if activity else None) == details:
                    return activity
            game.frames.append(await game.next_frame())


async def test_a_players_presence_follows_it_from_the_menus_through_a_match_and_away(
    presence_settings: None,
    prompt_actions: None,
    open_hosting: None,
    server_url: str,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    async with game(server_url) as server, game(server_url) as player:
        # The hook asks on every game, since it cannot tell this game's servers from players.
        request = encode_frame(Presence(b'{"enabled":true}'))
        await server.websocket.send(request)
        await player.websocket.send(request)
        created = await host_game(server, {"NAME": "Invented Server #1 (203.0.113.7:19567)"})
        conquest = [("B-U-level", "Levels/MP_006CQ"), ("B-U-gamemode", "CONQUEST")]
        await server.send_theater(THEATER, "UGAM", [*ids(created), *conquest])
        _, lkey = await log_in_persona(player, FESL, "ann@example.test", "Ann_01", "pl_PL")

        menus = await shown(player, "W menu")
        assert menus is not None
        assert abs(menus.pop("startedAt") - time.time() * 1000) < 60_000
        assert menus == {
            "name": "Battlefield: Bad Company 2",
            "details": "W menu",
            "smallImage": f"{PUBLIC_URL}/static/apple-touch-icon.png",
            "smallText": "OpenFESL",
            "page": "/bfbc2",
        }

        # The game's icon shows from the moment the admin stores one.
        icon = tmp_path / "invented-icon.png"
        icon.write_bytes(png_image(64, 64))
        await cli(capsys, "icons", "set", "bfbc2", str(icon))
        pictured = await shown(player, "W menu")
        assert pictured is not None
        assert (pictured["largeImage"], pictured["largeText"]) == (
            f"{PUBLIC_URL}/bfbc2/icon.png",
            "Battlefield: Bad Company 2",
        )

        await enter_theater(player, THEATER, "bfbc2-pc", lkey)
        pid = await let_in(server, player, created)
        match = await shown(player, "Podbój na mapie Port Arica")
        assert match is not None
        assert (match["state"], match["party"]) == ("Invented Server #1", {"size": 1, "max": 32})

        # A map rotation keeps the match's timer.
        rush = [("B-U-level", "Levels/MP_005GR"), ("B-U-gamemode", "RUSH")]
        await server.send_theater(THEATER, "UGAM", [*ids(created), *rush])
        rotated = await shown(player, "Gorączka na mapie Pustynia Atakama")
        assert rotated is not None
        assert rotated["startedAt"] == match["startedAt"]

        assert (await server.theater(THEATER, "PLVT", [*ids(created), ("PID", pid)])).error is None
        assert await shown(player, "W menu") is not None

        # The account's login elsewhere leaves this game nothing to show.
        async with game(server_url) as elsewhere:
            await log_in_again(elsewhere, FESL, "ann@example.test", "Ann_01")
            assert await shown(player, None) is None

        # The dedicated server, which hosts the game, never got any.
        assert (
            await server.theater(THEATER, "UBRA", [*ids(created), ("START", "0")])
        ).error is None
        assert not [frame for frame in server.frames if isinstance(frame, Presence)]


async def test_a_player_whose_presence_fails_leaves_the_others_presence_going(
    presence_settings: None,
    server_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken_in_polish(key: str, language: str) -> str:
        if language == "pl":
            raise KeyError("mapa")
        return translate(key, language)

    monkeypatch.setattr(rich_presence, "translate", broken_in_polish)
    request = encode_frame(Presence(b'{"enabled":true}'))
    async with game(server_url) as broken, game(server_url) as player:
        # The login's answer comes after the request is handled, so the broken session is
        # the first that presence goes to.
        await broken.websocket.send(request)
        await log_in_persona(broken, FESL, "ann@example.test", "Ann_01", "pl_PL")
        await player.websocket.send(request)
        await log_in_persona(player, FESL, "bea@example.test", "Bea_01")
        assert await shown(player, "In the menus") is not None
        await anyio.sleep(rich_presence.UPDATE_INTERVAL * 4)
    assert caplog.text.count("Could not build a player's presence") == 1


def test_an_empty_discord_application_id_turns_presence_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Hosts such as Dokploy switch a variable off by leaving it empty.
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "")
    settings = Settings(database_url="postgresql://db.example.test/fesl")
    assert settings.discord_application_id is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param(
            "[iad]Invented Heroes (EU)(198.51.100.4:18567)", "Invented Heroes (EU)", id="labelled"
        ),
        pytest.param(
            "[iad]Invented Heroes 24/7((unknown):18567)", "Invented Heroes 24/7", id="no-address"
        ),
        # Without the address that ends a host's label, a tag in brackets belongs to the name.
        pytest.param("[ABC] Invented Clan Server", "[ABC] Invented Clan Server", id="clan-tag"),
        pytest.param("Invented\tServer\x07  #2 203.0.113.9", "Invented Server #2", id="controls"),
    ],
)
def test_a_servers_name_shows_without_its_label_address_or_control_characters(
    name: str, expected: str
) -> None:
    assert clean_name(name) == expected
