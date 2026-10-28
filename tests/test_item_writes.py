from datetime import UTC, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

import pytest
from accounts import execute, select_value
from heroes import MAGMA
from store import ROYAL_SOLDIER, buy, grant, kept_by_server, shopper, stock
from support import fetch
from test_purchases import BANDAGES, RIFLE_DAY, ends

pytestmark = pytest.mark.anyio


async def write(
    server_url: str, cookie: dict[str, str], entitlement: int, change: str
) -> tuple[str, str | None]:
    """Change an item as the game does, such as useCount/3; return the root and the cause."""
    path = f"/nucleus/entitlement/{entitlement}/{change}"
    answer = ElementTree.fromstring((await fetch(server_url, path, MAGMA, cookie, b""))[2])
    failure = answer.find("failure")
    return answer.tag, failure.get("cause") if failure is not None else None


async def listed(server_url: str, cookie: dict[str, str], hero: int) -> list[str]:
    """Return a hero's items as the game loads them, each as its tag, uses and status."""
    _, _, body = await fetch(server_url, f"/nucleus/entitlements/{hero}", MAGMA, cookie)
    return [
        " ".join(entry.findtext(name) or "" for name in ("entitlementTag", "useCount", "status"))
        for entry in ElementTree.fromstring(body)
    ]


async def entitlement(database_url: str, tag: str, persona: int | None = None) -> int:
    """Return the number of an entitlement by its tag and the persona holding it, if any."""
    found = await select_value(
        database_url,
        "SELECT id FROM entitlement WHERE tag = :tag AND persona_id IS NOT DISTINCT FROM :persona",
        tag=tag,
        persona=persona,
    )
    assert isinstance(found, int)
    return found


@pytest.mark.usefixtures("open_hosting")
async def test_a_dedicated_server_takes_the_uses_its_player_used_and_never_adds_any(
    server_url: str, database_url: str
) -> None:
    async with kept_by_server(server_url) as (cookie, hero):
        # The bandages every hero starts with.
        bandages = await entitlement(database_url, "2005-lmct", hero)

        used = [await write(server_url, cookie, bandages, "useCount/3")]
        after_three = await listed(server_url, cookie, hero)
        # More uses than are left leave none, and fewer than none add nothing.
        used += [
            await write(server_url, cookie, bandages, change)
            for change in ("useCount/20", "useCount/-5")
        ]
        after_all = await listed(server_url, cookie, hero)

    assert used == [("success", None), ("success", None), ("error", "NEGATIVE_USES")]
    assert after_three == ["2005-lmct 7 ACTIVE"]
    assert after_all == ["2005-lmct 0 ACTIVE"]


@pytest.mark.usefixtures("prompt_actions")
async def test_the_game_ends_or_removes_its_heros_items_which_the_hero_may_buy_again(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    cookie, hero = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    _, other = await shopper(server_url, "bob@example.test", "Mudlark")
    await grant(database_url, hero, "_DV", 600)
    rented = await buy(server_url, cookie, hero, RIFLE_DAY)
    # Tonics beside the bandages and the rental, a pet the admin took away and an entitlement of
    # the account.
    await execute(
        database_url,
        "INSERT INTO entitlement (account_id, persona_id, tag, status, use_count) SELECT"
        " account_id, id, item.tag, item.status, item.uses FROM persona, (VALUES"
        " ('2153-lmct', 'ACTIVE', 5), ('521-unlm', 'DISABLED', NULL)) AS item (tag, status, uses)"
        " WHERE persona.id = :hero",
        hero=hero,
    )
    await execute(
        database_url,
        "INSERT INTO entitlement (account_id, tag, status) SELECT account_id, 'BFHEROES:PC:ADDON',"
        " 'ACTIVE'"
        " FROM persona WHERE id = :hero",
        hero=hero,
    )
    rifle = await entitlement(database_url, "3015-tlmd", hero)
    bandages = await entitlement(database_url, "2005-lmct", hero)
    tonics = await entitlement(database_url, "2153-lmct", hero)
    taken = await entitlement(database_url, "521-unlm", hero)
    addon = await entitlement(database_url, "BFHEROES:PC:ADDON")
    others = await entitlement(database_url, "2005-lmct", other)

    refused = [
        await write(server_url, cookie, rifle, f"status/{status}")
        for status in ("ACTIVE", "BANNED", "PENDING")
    ]
    refused += [
        await write(server_url, cookie, taken, "status/DELETED"),
        await write(server_url, cookie, taken, "useCount/1"),
        await write(server_url, cookie, addon, "useCount/1"),
        await write(server_url, cookie, others, "status/DELETED"),
        await write(server_url, {"Cookie": "magma=0"}, bandages, "useCount/1"),
    ]
    changed = [
        await write(server_url, cookie, rifle, "status/DISABLED"),
        await write(server_url, cookie, bandages, "status/DISABLED"),
        await write(server_url, cookie, tonics, "status/DELETED"),
    ]
    ended = await listed(server_url, cookie, hero)
    rental_ended = await ends(database_url, hero, 3015)
    rebought = [await buy(server_url, cookie, hero, product) for product in (RIFLE_DAY, BANDAGES)]

    assert rented == ("success", None)
    assert refused == [
        ("error", "STATUS_NOT_ALLOWED"),
        ("error", "STATUS_NOT_ALLOWED"),
        ("error", "STATUS_NOT_ALLOWED"),
        ("error", "UNKNOWN_ENTITLEMENT"),
        ("error", "UNKNOWN_ENTITLEMENT"),
        ("error", "UNKNOWN_ENTITLEMENT"),
        ("error", "USER_DOES_NOT_MATCH"),
        ("error", "NO_SESSION"),
    ]
    assert changed == [("success", None)] * 3
    # The rental has ended and the bandages are used up, as the game shows them; the tonics are
    # gone, and the admin's decision stands.
    assert ended == ["2005-lmct 0 ACTIVE", "3015-tlmd 0 ACTIVE", "521-unlm 0 DISABLED"]
    assert rental_ended <= datetime.now(UTC)
    # Both are bought again: the rental from now, the uses added.
    assert rebought == [("success", None)] * 2
    assert await listed(server_url, cookie, hero) == [
        "2005-lmct 25 ACTIVE",
        "3015-tlmd 0 ACTIVE",
        "521-unlm 0 DISABLED",
    ]
    left = await ends(database_url, hero, 3015) - datetime.now(UTC)
    assert timedelta(hours=23, minutes=59) < left <= timedelta(days=1)
    assert (
        await select_value(
            database_url, "SELECT status FROM entitlement WHERE id = :addon", addon=addon
        )
        == "ACTIVE"
    )
