from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in, select_value
from heroes import create_hero
from identity import form_token
from living import import_catalogue
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from store import ROYAL_SOLDIER
from support import markup, visible

from openfesl.core.stats import PERSONA, Change, Owner, bucket_of
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
# The weapons it used and one that killed it, two maps, the jeep, two heroic moments, a mission
# it takes on and one it completed twice.
PLAYED = {
    "tw3003": 7260,
    "kw3003": 4210,
    "dww3003": 618,
    "hw3003": 1210,
    "sw3003": 4800,
    "tw2026": 1800,
    "kw2026": 64,
    "dfw3015": 95,
    "m_ct3": 3600,
    "m_win3": 9,
    "m_los3": 3,
    "m_ct8": 1200,
    "m_win8": 2,
    "tv0": 600,
    "kv0": 31,
    "kvr0": 9,
    "dstrv0": 4,
    "dfv0": 2,
    "div0": 3,
    "ige19": 96,
    "ige12": 41,
    "mid0": 6001,
    "aw6000": 2,
}


async def play(
    database_url: str, hero: int, stats: Mapping[str, float], weeks_ago: int = 0
) -> None:
    """Add a hero's rounds to its stats, for all time and a week, as its server writes them.

    The week is the bucket the game reads through FESL with that many periods past.
    """
    engine = create_async_engine(database_url)
    store = SqlStatStore(async_sessionmaker(engine))
    week = bucket_of(Period.WEEKLY, weeks_ago)
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
    await play(database_url, brass, ROUNDS | PLAYED)
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
        " Suicides 37 Experience 0 Score 1,284,560 · not ranked yet Wins · losses 612 · 488"
        " Ratio 1.25"
        " Flag points 1,906 Rocket captures 44 Time 1 h 30 min"
    ) in shown
    day = f"{today.day} {today:%b %Y}"
    assert f"Enlisted {day}" in shown
    assert "Stats as the game reports them at the end of each round." in shown
    assert "Wynik 1\u00a0284\u00a0560" in visible(polish.content)
    # The weapons used, the longest first, and those that killed the hero, each by number, as
    # are maps without names; the vehicles go by OpenFESL's own words.
    assert (
        "Maps, weapons and vehicles Weapons used Item 3003 Time 2 h 1 min Kills 4,210"
        " Deaths using it 618 Accuracy 0.252 Item 2026 Time 0 h 30 min Kills 64"
        " Deaths using it 0 Weapons that defeated this hero Item 3015 Deaths 95"
        " Maps Map 3 Time 1 h 0 min Wins 9 Losses 3 Ratio 3.00 Map 8 Time 0 h 20 min Wins 2"
        " Losses 0 Ratio 2.00 Vehicles Jeep Time 0 h 10 min Kills 31 Road kills 9 Destroyed 4"
        " Deaths from it 2 Deaths inside 3"
    ) in shown
    assert "Heroic Moments Heroic moment 19 · 96 Heroic moment 12 · 41" in shown
    assert "Missions Mission 6001 · current Mission 6000 · completed 2 times" in shown
    # A hero without rounds keeps its rows, empty until its first round.
    assert "Maps, weapons and vehicles" not in visible(unplayed.content)
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
async def test_with_a_catalogue_a_profile_names_the_heros_items_maps_moments_and_rank(
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
    invented[6001] = ("", "mission", {"teamPrereq": "0", "kits": "63"})
    texts = {
        "Player_Prestige_Level_9": "[Invented Colonel ]",
        "Player_Prestige_Rotation_51": "LI",
        "WEST_LEVELNAME_LAKE": "Invented Bay",
        "HEROIC_MOMENT_HEADSHOT": "Invented Headshot",
        "AUTO_RULES_MISSION_6001": "Invented Mastery II",
    }
    await import_catalogue(server_url, tmp_path, capsys, invented, texts)
    brass = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_GUNNER)
    await play(database_url, brass, ROUNDS | PLAYED)
    async with httpx2.AsyncClient() as client:
        shown = visible((await client.get(f"{base}/bfheroes/heroes/{brass}")).content)

    assert "Invented Colonel LI · player Quill_Invented" in shown
    # Without the icons the catalogue's items wear their kind's glyph, and only those it does not
    # know the question mark.
    assert "Equipped Invented Cap ? Item 36 ? Item 444 Invented Repeater" in shown
    assert "Names and icons" not in shown
    assert "Weapons used Invented Repeater Time 2 h 1 min" in shown
    assert "Maps Invented Bay Time 1 h 0 min" in shown
    assert "Heroic Moments Invented Headshot · 96 Heroic moment 12 · 41" in shown
    assert "Missions Invented Mastery II · current Mission 6000 · completed 2 times" in shown


async def test_a_players_profile_lists_their_heroes_totals_and_asks_them_as_a_friend(
    base: str, database_url: str
) -> None:
    await name_players(database_url)
    brass = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_GUNNER)
    pip = await create_hero(base, "ann@example.test", "Pip-Lantern", **ROYAL_SOLDIER)
    await play(database_url, brass, ROUNDS | {"games": 30})
    await play(database_url, pip, {"ct": 1800, "games": 4})
    ann = await select_value(database_url, "SELECT id FROM account WHERE nuid = 'ann@example.test'")
    profile = f"{base}/bfheroes/player/{ann}"
    async with httpx2.AsyncClient() as client:
        signed_out = await client.get(profile)
        hero = await client.get(f"{base}/bfheroes/heroes/{brass}")
        await post_sign_in(client, base, "bob@example.test", PASSWORD)
        page = await client.get(profile)
        form = {"csrf": form_token(page.text), "action": "ask", "account": str(ann)}
        asked = await client.post(
            f"{base}/bfheroes/friends",
            data=form | {"name": "Quill_Invented", "next": f"/bfheroes/player/{ann}"},
        )
        after = await client.get(base + asked.headers["location"])
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        own = visible((await client.get(profile)).content)
        await execute(
            database_url, "UPDATE account SET state = 'deactivated' WHERE nuid = 'ann@example.test'"
        )
        hidden = await client.get(profile)
        unknown = await client.get(f"{base}/bfheroes/player/999999")

    shown = visible(signed_out.content)
    today = datetime.now(UTC)
    # A visitor may sign up through the player's link of Recruit a Friend.
    joined = "Sign up through Quill_Invented's personal link On OpenFESL since"
    assert f"Quill_Invented {joined} {today.day} {today:%b %Y}" in shown
    assert "Friend requests need a signed-in account. Sign in" in shown
    assert f'href="/sign-in?next=%2Fbfheroes%2Fplayer%2F{ann}"' in signed_out.text
    assert (
        "Heroes · 2 Brassbuckle Gunner of the Royal Army level 1"
        " Pip-Lantern Soldier of the Royal Army level 1"
    ) in shown
    assert f'href="/bfheroes/heroes/{brass}"' in signed_out.text
    assert "Totals Time played 2 h 0 min Rounds played 34" in shown
    # A hero's profile leads to its player's.
    assert f'href="/bfheroes/player/{ann}" class="profile-link">Quill_Invented</a>' in markup(
        hero.content
    )
    assert "Add as friend" in visible(page.content)
    assert asked.headers["location"] == f"/bfheroes/player/{ann}"
    assert "Request sent Manage friends" in visible(after.content)
    assert "Add as friend" not in own and "Friend requests need" not in own
    # A deactivated account's profile answers as one that never existed.
    assert hidden.status_code == unknown.status_code == 404
    assert visible(hidden.content) == visible(unknown.content)


async def test_the_sites_lists_lead_to_the_profiles_of_the_heroes_and_players_they_name(
    base: str, database_url: str
) -> None:
    await name_players(database_url)
    brass = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_GUNNER)
    kettle = await create_hero(base, "bob@example.test", "Kettlebrook", **ROYAL_SOLDIER)
    await play(database_url, brass, {"rs": 500, "ct": 7200})
    ids = "SELECT id FROM account WHERE nuid = :nuid"
    ann = await select_value(database_url, ids, nuid="ann@example.test")
    bob = await select_value(database_url, ids, nuid="bob@example.test")
    hero, player = f'href="/bfheroes/heroes/{brass}"', f'href="/bfheroes/player/{ann}"'
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        token = form_token((await client.get(f"{base}/bfheroes/friends")).text)
        asked = {"csrf": token, "action": "ask", "account": str(bob), "name": "Kettle_Inv"}
        await client.post(f"{base}/bfheroes/friends", data=asked)
        heroes = markup((await client.get(f"{base}/bfheroes/heroes")).content)
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "bob@example.test", PASSWORD)
        friends = markup((await client.get(f"{base}/bfheroes/friends")).content)
        search = markup((await client.get(f"{base}/bfheroes/search", params={"q": "Bra"})).content)
        front = markup((await client.get(f"{base}/bfheroes")).content)
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        # An account made in a game has no public pages until it picks a display name, and a
        # hero the admin took out of play has none either.
        unnamed = "UPDATE account SET display_name = NULL WHERE nuid = 'ann@example.test'"
        await execute(database_url, unnamed)
        nameless = markup((await client.get(f"{base}/bfheroes/heroes")).content)
        await name_players(database_url)
        disabled = "UPDATE persona SET state = 'disabled' WHERE id = :hero"
        await execute(database_url, disabled, hero=brass)
        out_of_play = markup((await client.get(f"{base}/bfheroes/heroes")).content)

    # The request that came names its player, and the default hero's name its hero.
    assert player in friends and hero in friends
    assert hero in search and player in search
    assert hero in front and player in front
    # The account's own heroes lead to their profiles, the default one from the bar too.
    assert heroes.count(hero) == 3
    assert f'href="/bfheroes/heroes/{kettle}"' not in heroes
    # No link leads to a profile that answers as unknown: the bar's hero leads to the heroes
    # page instead.
    assert hero not in nameless and hero not in out_of_play
    assert 'href="/bfheroes/heroes" class="hero-pill' in nameless
