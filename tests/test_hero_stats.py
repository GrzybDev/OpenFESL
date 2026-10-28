from datetime import date
from pathlib import Path

import anyio
import pytest
from accounts import execute, select_value
from game_files import invented_copy
from heroes import create_hero, start
from support import FESL, Game, Update, cli, game, update_stats

from openfesl.core import hero_items
from openfesl.core.stats import ACCOUNT, PERSONA, Owner

pytestmark = pytest.mark.anyio

# The hero's stats that the game writes and reads back, and its level, which it never writes.
HERO_KEYS = ("c_apr", "c_eqp", "c_emo", "c_cmid1", "c_wmid0", "c_wmid1", "c_ltp", "level")
ACCOUNT_KEYS = ("c_tut", "c_ltm", "c_slm")


def setting(key: str, value: str = "0", text: str = "") -> Update:
    """Return a stat as the game writes its own: set for all time."""
    return (0, key, value, text, "0")


async def write(player: Game, owner: int, *stats: Update) -> str | None:
    """Write stats of an owner as the player's game does; return the error code, if any."""
    answer = await player.request(FESL, "rank", update_stats((str(owner), "1", list(stats))))
    return answer.get("errorCode")


async def read(player: Game, owner: int, *keys: str) -> list[str]:
    """Return an owner's stats as the game reads them, each its text or else its number."""
    asked = [
        ("TXN", "GetStats"),
        ("owner", str(owner)),
        ("ownerType", "1"),
        ("periodId", "0"),
        ("periodPast", "0"),
        *((f"keys.{index}", key) for index, key in enumerate(keys)),
        ("keys.[]", str(len(keys))),
    ]
    answer = await player.request(FESL, "rank", asked)
    return [
        answer.get(f"stats.{index}.text") or answer[f"stats.{index}.value"]
        for index in range(len(keys))
    ]


async def keep(database_url: str, hero: int, tag: str, expired: bool = False) -> None:
    """Keep an item for a hero, as the store will once it sells them."""
    await execute(
        database_url,
        "INSERT INTO entitlement (account_id, persona_id, tag, status, expires_at)"
        " SELECT account_id, id, :tag, 'ACTIVE', CASE WHEN :expired THEN now() END FROM persona"
        " WHERE id = :hero",
        hero=hero,
        tag=tag,
        expired=expired,
    )


async def report(database_url: str, owner: Owner, key: str) -> None:
    """Keep 1 in an owner's stat for all time, as a server reports it."""
    await execute(
        database_url,
        "INSERT INTO stat (partition, owner_type, owner_id, period, period_start, key, value,"
        " text) VALUES ('/eagames/bfwest', :type, :id, 0, '1970-01-01', :key, 1, '')",
        type=owner.type,
        id=owner.id,
        key=key,
    )


async def test_the_game_saves_its_heros_look_and_equipment_and_nothing_the_hero_lacks(
    base: str, server_url: str, database_url: str
) -> None:
    bob_hero = await create_hero(base, "bob@example.test", "Mudlark")
    await create_hero(base, "ann@example.test", "Brassbuckle", faction="royal")
    bob = await select_value(database_url, "SELECT id FROM account WHERE nuid = 'bob@example.test'")
    assert isinstance(bob, int)
    async with game(server_url) as player:
        _, account, hero = await start(server_url, player, "ann@example.test")
        # As the game writes them: its hero's under the hero, its account's under the account.
        # The look has base legs of the faction in place of the trousers, and the hero's own
        # hair; the mission is the loadout's.
        saved = [
            await write(
                player,
                hero,
                setting("c_apr", text="29;32;31;4;85;105"),
                setting("c_eqp", text="2005;3156;2136;3013;3001"),
                setting("c_emo", text="5016;5000"),
                setting("c_cmid1"),
                setting("c_wmid0", "6000.0000"),
                setting("c_ltp", "7.0000"),
            ),
            await write(
                player,
                account,
                setting("c_tut", "2.0000"),
                setting("c_ltm", "1793232000.0000"),
                setting("c_slm", "45.0000"),
            ),
        ]
        # Without the catalogue an item counts by what the server keeps for the hero alone, and
        # a chest is no item to wear or use.
        await keep(database_url, hero, "3169-unlm")
        await keep(database_url, hero, "3170-tlmd", expired=True)
        await keep(database_url, hero, "BFH_TREASURE_CHEST")
        saved.append(await write(player, hero, setting("c_eqp", text="3169;3013;3001")))
        # An emptied list of emotes.
        saved.append(await write(player, hero, setting("c_emo")))

        refused = [
            # An item the hero lacks, or whose time ran out, and a wish the server cannot check.
            await write(player, hero, setting("c_apr", text="29;32;5;3")),
            await write(player, hero, setting("c_eqp", text="3170;3001")),
            await write(player, hero, setting("c_wmid1", "6001.0000")),
            # Another player's hero and account.
            await write(player, bob_hero, setting("c_apr", text="10;981;979;56")),
            await write(player, bob, setting("c_tut", "1.0000")),
            # Progress, values out of their range or added to, and stats the game never writes.
            await write(player, hero, (3, "level", "1.0000", "", "0")),
            await write(player, account, setting("c_tut", "3.0000")),
            await write(player, hero, setting("c_cmid1", "6000.0000")),
            await write(player, hero, (3, "c_ltp", "1.0000", "", "0")),
            await write(player, hero, setting("c_emo", "1.0000", "5000")),
            await write(player, hero, setting("c_team", "1.0000")),
        ]
        stored = await read(player, hero, *HERO_KEYS)
        stored_account = await read(player, account, *ACCOUNT_KEYS)

    assert saved == [None, None, None, None]
    assert refused == ["21"] * len(refused)
    assert stored == [
        "29;32;31;4;85;105",
        "3169;3013;3001",
        "0.0000",
        "0.0000",
        "6000.0000",
        "0.0000",
        "7.0000",
        "1.0000",
    ]
    assert stored_account == ["2.0000", "1793232000.0000", "45.0000"]


@pytest.mark.usefixtures("prompt_actions")
async def test_with_the_catalogue_items_fit_the_hero_and_missions_are_taken_as_the_game_allows(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", faction="royal")
    # A National hair style, a gunner's weapon, an ability any hero may use and an emote; the
    # invented catalogue's hat is a base item of the Royal Army, which needs keeping by no one.
    for tag in ("2-unlm", "3000-unlm", "2010-lmct", "5000-unlm"):
        await keep(database_url, hero, tag)
    copy, file = tmp_path / "copy", tmp_path / "catalogue.zip"
    invented_copy(copy)
    await cli(capsys, "catalogue", "extract", str(copy), "-o", str(file))
    await cli(capsys, "catalogue", "import", str(file))
    # 2 January, whose daily mission is 6001.
    monkeypatch.setattr(hero_items, "_today", lambda: date(2026, 1, 2))
    async with game(server_url) as player:
        _, account, hero = await start(server_url, player, "ann@example.test")
        # The game grants every hero the missions of its team and class at their level.
        with anyio.fail_after(5):
            while await write(player, hero, setting("c_wmid0", "6003.0000")):
                await anyio.sleep(0.05)
        saved = await write(
            player,
            hero,
            setting("c_apr", text="1"),
            setting("c_eqp", text="2010"),
            setting("c_emo", text="5000"),
        )

        refused = [
            # Two items in one slot, another faction's, another class's, of a kind the stat
            # does not hold, one the hero lacks, a mission of a higher level, one whose
            # prerequisite the hero has not completed, and another day's daily mission.
            await write(player, hero, setting("c_apr", text="1;1")),
            await write(player, hero, setting("c_apr", text="2")),
            await write(player, hero, setting("c_eqp", text="3000")),
            await write(player, hero, setting("c_eqp", text="2010;5000")),
            await write(player, hero, setting("c_eqp", text="2000")),
            await write(player, hero, setting("c_wmid1", "6001.0000")),
            await write(player, hero, setting("c_wmid1", "6002.0000")),
            await write(player, hero, setting("c_wmid1", "6000.0000")),
        ]
        # The hero reaches level 5 and completes its first mission, as servers report them.
        await execute(
            database_url,
            "UPDATE stat SET value = 5 WHERE key = 'level' AND owner_id = :hero",
            hero=hero,
        )
        await report(database_url, Owner(PERSONA, hero), "aw6003")
        reached = [
            await write(player, hero, setting("c_wmid1", "6002.0000")),
            await write(player, hero, setting("c_wmid2", "6001.0000")),
            # A completed mission stays where it was wished.
            await write(player, hero, setting("c_wmid0", "6003.0000")),
        ]
        # The account completes the day's daily mission, on day 1 of the year from 0.
        await report(database_url, Owner(ACCOUNT, account), "cdm")
        # A completed mission that does not repeat, and a daily mission completed today.
        taken = [
            await write(player, hero, setting("c_wmid1", "6003.0000")),
            await write(player, hero, setting("c_wmid1", "6001.0000")),
        ]
        stored = await read(
            player, hero, "c_apr", "c_eqp", "c_emo", "c_wmid0", "c_wmid1", "c_wmid2"
        )

    assert saved is None and reached == [None, None, None]
    assert refused == ["21"] * len(refused) and taken == ["21", "21"]
    assert stored == ["1", "2010", "5000", "6003.0000", "6002.0000", "6001.0000"]
