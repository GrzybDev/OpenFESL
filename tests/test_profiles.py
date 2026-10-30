from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from accounts import execute
from heroes import create_hero
from living import import_catalogue
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from store import ROYAL_SOLDIER
from support import visible

from openfesl.core.stats import PERSONA, Bucket, Change, Owner, bucket_of, period_start
from openfesl.protocol.rank import Period, UpdateType
from openfesl.storage.stats import SqlStatStore

pytestmark = pytest.mark.anyio

ROYAL_GUNNER = {"faction": "royal", "class": "gunner", "hair_style": "82", "facial_hair": "102"}
# A round's stats as a dedicated server adds them to a hero: an hour and a half of rounds, its
# kills and deaths, shots, wins and losses, flags and rockets, and its rating and prestige rank:
# Colonel of the 51st rotation.
ROUNDS = {
    "ki": 9214,
    "dt": 6880,
    "hi": 2410,
    "fi": 10000,
    "su": 37,
    "rs": 1284560,
    "win": 612,
    "los": 488,
    "cpc": 1906,
    "rc": 44,
    "ct": 5427,
    "elo": 1412,
    "prs": 51 * 256 + 9,
}


async def play(database_url: str, hero: int, stats: Mapping[str, float]) -> None:
    """Add a hero's rounds to its stats, for all time and this week, as its server writes them."""
    engine = create_async_engine(database_url)
    store = SqlStatStore(async_sessionmaker(engine))
    week = Bucket(Period.WEEKLY, period_start(Period.WEEKLY, datetime.now(UTC).date()))
    owner = Owner(PERSONA, hero)
    changes = [
        Change(owner, key, bucket, UpdateType.ADD, value, None)
        for key, value in stats.items()
        for bucket in (bucket_of(Period.ALL_TIME, 0), week)
    ]
    await store.apply("/eagames/bfwest", changes)
    await engine.dispose()


async def name_players(database_url: str) -> None:
    for address, name in (
        ("ann@example.test", "Quill_Invented"),
        ("bob@example.test", "Kettle_Inv"),
    ):
        named = "UPDATE account SET display_name = :name WHERE nuid = :address"
        await execute(database_url, named, name=name, address=address)


async def test_a_heros_profile_shows_its_stats_as_its_game_keeps_them_to_anyone(
    base: str, database_url: str
) -> None:
    await name_players(database_url)
    brass = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_GUNNER)
    pip = await create_hero(base, "ann@example.test", "Pip-Lantern", **ROYAL_SOLDIER)
    kettle = await create_hero(base, "bob@example.test", "Kettlebrook", **ROYAL_SOLDIER)
    await play(database_url, brass, ROUNDS)
    await execute(
        database_url,
        "UPDATE stat SET value = 24 WHERE key = 'level' AND owner_id = :hero",
        hero=brass,
    )
    async with httpx2.AsyncClient() as client:
        profile = await client.get(f"{base}/bfheroes/heroes/{brass}")
        polish = await client.get(f"{base}/pl/bfheroes/heroes/{brass}")
        unplayed = await client.get(f"{base}/bfheroes/heroes/{pip}")
        await execute(
            database_url, "UPDATE persona SET state = 'disabled' WHERE id = :hero", hero=pip
        )
        await execute(
            database_url, "UPDATE account SET state = 'deactivated' WHERE nuid = 'bob@example.test'"
        )
        hidden = [
            await client.get(f"{base}/bfheroes/heroes/{hero}") for hero in (pip, kettle, 999999)
        ]
        # A ban whose time has passed lets the account log in again, and hides nothing.
        ended = "UPDATE account SET state = 'banned', state_until = now() - interval '1 minute'"
        await execute(database_url, f"{ended} WHERE nuid = 'ann@example.test'")
        unbanned = await client.get(f"{base}/bfheroes/heroes/{brass}")

    shown = visible(profile.content)
    today = datetime.now(UTC)
    assert profile.status_code == 200
    assert "Royal Army Brassbuckle Gunner level 24 player Quill_Invented" in shown
    # The game's own loadout, by number while the server has no catalogue; the faction's base
    # items, the body under the clothes, are not listed.
    worn = "? Item 4 ? Item 36 ? Item 444 ? Item 3003 ? Item 3009 ? Item 2026"
    assert f"Equipped {worn}" in shown
    assert "Names and icons of items show once this service imports the game's catalogue." in shown
    assert (
        "Soldier Info Skill rating 1,412 Kills · deaths 9,214 · 6,880 Ratio 1.34 Accuracy 0.241"
        " Suicides 37 Experience 0 Score 1,284,560 Wins · losses 612 · 488 Ratio 1.25"
        " Flag points 1,906 Rocket captures 44 Time 1 h 30 min"
    ) in shown
    day = f"{today.day} {today:%b %Y}"
    assert f"Enlisted {day}" in shown
    assert "Stats as the game reports them at the end of each round." in shown
    assert "Wynik 1\u00a0284\u00a0560" in visible(polish.content)
    # A hero without rounds keeps its rows, empty until its first round.
    assert (
        "No rounds yet. These numbers fill after Pip-Lantern's first round. Skill rating \u2013"
        " Kills · deaths \u2013 Ratio \u2013"
    ) in visible(unplayed.content)
    # A hero the admin took out of play, one of a deactivated account and one that never
    # existed answer alike.
    assert {page.status_code for page in hidden} == {404}
    assert len({visible(page.content) for page in hidden}) == 1
    assert "No hero or player page is at this address." in visible(hidden[0].content)
    assert unbanned.status_code == 200


@pytest.mark.usefixtures("prompt_actions")
async def test_with_a_catalogue_a_profile_names_the_heros_items_and_rank(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await name_players(database_url)
    invented = {
        4: ("Invented Cap", "appearance", {"faction": "1", "slot": "10"}),
        2026: ("Invented Shield", "ability", {"faction": "3", "kits": "36"}),
        2027: ("Invented Shield", "ability", {"faction": "3", "kits": "36"}),
        3003: ("Invented Repeater", "weapon", {"faction": "1", "kits": "36"}),
    }
    ranks = {"Player_Prestige_Level_9": "[Invented Colonel ]", "Player_Prestige_Rotation_51": "LI"}
    await import_catalogue(server_url, tmp_path, capsys, invented, ranks)
    brass = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_GUNNER)
    await play(database_url, brass, ROUNDS)
    async with httpx2.AsyncClient() as client:
        shown = visible((await client.get(f"{base}/bfheroes/heroes/{brass}")).content)

    assert "Invented Colonel LI · player Quill_Invented" in shown
    # Without the icons the catalogue's items wear their kind's glyph, and only those it does not
    # know the question mark.
    assert "Equipped Invented Cap ? Item 36 ? Item 444 Invented Repeater" in shown
    assert "Names and icons" not in shown
