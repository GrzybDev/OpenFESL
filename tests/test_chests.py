import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from random import Random

import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in, select_value
from heroes import MAGMA, create_hero, wallets
from identity import form_token
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from store import NAMES, ROYAL_SOLDIER, kept_by_server, shopper, stock
from support import fetch, visible
from test_server_calls import ask

from openfesl.core.catalogues import Catalogues
from openfesl.core.chests import ChestRefusal, ChestRefused, Chests
from openfesl.core.deals import HotDeals
from openfesl.core.drops import Drops
from openfesl.core.games import Game, load_games
from openfesl.core.heroes import Heroes
from openfesl.core.personas import Persona
from openfesl.core.purchases import Purchases
from openfesl.core.store import Product
from openfesl.storage.catalogues import SqlCatalogueStore
from openfesl.storage.deals import SqlDealStore
from openfesl.storage.entitlements import SqlEntitlementStore
from openfesl.storage.heroes import SqlHeroStore
from openfesl.storage.personas import SqlPersonaStore
from openfesl.storage.purchases import SqlPurchaseStore
from openfesl.storage.settings import SqlSettingStore
from openfesl.storage.stats import SqlStatStore
from openfesl.storage.store import SqlProductStore

pytestmark = pytest.mark.anyio

PAGE = "/bfheroes/treasure-chests"


@dataclass(frozen=True, slots=True)
class Services:
    """The services behind the treasure chests page, called as its forms call them."""

    game: Game
    purchases: Purchases
    chests: Chests
    personas: SqlPersonaStore


@pytest.fixture
async def services(
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> AsyncIterator[Services]:
    await stock(server_url, tmp_path, capsys)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine)
    games = load_games()
    game = games.by_id("bfheroes")
    assert game is not None
    catalogues = Catalogues(SqlCatalogueStore(sessions), None, games)
    await catalogues.load()
    entitlements = SqlEntitlementStore(sessions)
    heroes = Heroes(SqlHeroStore(sessions), SqlStatStore(sessions))
    # A source of chance that gives the same drops on every run.
    drops = Drops(catalogues, entitlements, Random(20120625))
    products, paid = SqlProductStore(sessions), SqlPurchaseStore(sessions)
    deals = HotDeals(products, SqlDealStore(sessions), SqlSettingStore(sessions), catalogues)
    purchases = Purchases(products, paid, catalogues, heroes, entitlements, drops, deals)
    chests = Chests(products, paid, heroes, drops)
    yield Services(game, purchases, chests, SqlPersonaStore(sessions))
    await engine.dispose()


async def chests(database_url: str, hero: int, count: int) -> None:
    """Stack chests on a hero as its dedicated server does at a round's end."""
    await execute(
        database_url,
        "INSERT INTO entitlement (account_id, persona_id, tag, use_count, status) SELECT"
        " account_id, id, 'BFH_TREASURE_CHEST', :count, 'ACTIVE' FROM persona WHERE id = :hero",
        hero=hero,
        count=count,
    )


async def held(database_url: str, tag: str, hero: int | None = None) -> object:
    """Return the uses of a hero's item by its tag, or of its account's own without a hero."""
    return await select_value(
        database_url,
        "SELECT use_count FROM entitlement WHERE tag = :tag AND (persona_id = :hero"
        " OR (CAST(:hero AS bigint) IS NULL AND persona_id IS NULL))",
        tag=tag,
        hero=hero,
    )


async def hero_of(services: Services, number: int) -> Persona:
    persona = await services.personas.find_by_id(number)
    assert persona is not None
    return persona


async def opened(services: Services, persona: Persona) -> Product | ChestRefusal:
    try:
        return await services.chests.open(services.game, persona)
    except ChestRefused as refused:
        return refused.refusal


@pytest.mark.usefixtures("prompt_actions", "open_hosting")
async def test_a_stacked_chest_opens_on_the_page_into_an_item_the_game_lists(
    services: Services, server_url: str, database_url: str
) -> None:
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with kept_by_server(server_url) as (cookie, number, token):
        hero = await hero_of(services, number)
        # The round's end, at which the server stacks the chests the hero picked up.
        path = f"/nucleus/stackEntitlementUseCount/{hero.account_id}/{number}/BFH_TREASURE_CHEST/2"
        stacked = await ask(server_url, path, token, post=True)
        key = await select_value(database_url, "SELECT id FROM store_product WHERE account")
        # The site sells the key, which the game would not know, to the account.
        await services.purchases.purchase_for_account(
            services.game, hero.account_id, f"OFB-DBFH:{key}"
        )
        others = await select_value(database_url, "SELECT id FROM persona WHERE name = 'host'")
        async with httpx2.AsyncClient() as site:
            await post_sign_in(site, base, "ann@example.test", PASSWORD)
            page = await site.get(f"{base}{PAGE}")
            opening = f"{base}{PAGE}/{number}/open"
            # A form from another site opens nothing.
            await site.post(opening, data={"csrf": "forged"})
            expired = visible((await site.get(f"{base}{PAGE}")).content)
            form = {"csrf": form_token(page.text)}
            await site.post(opening, data=form)
            revealed = visible((await site.get(f"{base}{PAGE}")).content)
            await site.post(opening, data=form)
            without_key = visible((await site.get(f"{base}{PAGE}")).content)
            not_hers = await site.post(f"{base}{PAGE}/{others}/open", data=form)
        _, _, body = await fetch(server_url, f"/nucleus/entitlements/{number}", MAGMA, cookie)
        balances = await wallets(server_url, cookie, number)

    assert stacked == b"<success />"
    assert "No chest was opened: the form had expired. No key was used." in expired
    # Of the chest's drops in the invented catalogue, those of the Royal soldier.
    [(item, won)] = [
        (item, NAMES[item])
        for item in (2153, 2005, 3015, 521)
        if f"for ann {NAMES[item]} " in revealed
    ]
    assert f"{item}-".encode() in body
    assert f"Chest opened for ann: {won}, " in revealed
    assert f"1 key used; no keys left. Chest opened Item for ann {won} " in revealed
    assert "No keys left; ann has 1 chest to open." in revealed
    assert "No chest was opened: the account has no key left." in without_key
    assert not_hers.status_code == 404
    assert (
        await held(database_url, "7353-lmct"),
        await held(database_url, "BFH_TREASURE_CHEST", number),
    ) == (0, 1)
    assert balances["_PF"] == 2500 - 499
    # Nothing in the game's own lists sells the key.
    _, _, offered = await fetch(server_url, "/ofb/products", MAGMA)
    assert b"7353-" not in offered


@pytest.mark.usefixtures("prompt_actions", "base")
async def test_a_chest_opens_once_for_one_key_and_never_without_a_chest(
    services: Services, server_url: str, database_url: str
) -> None:
    _, number = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    hero = await hero_of(services, number)
    key = await select_value(database_url, "SELECT id FROM store_product WHERE account")
    await services.purchases.purchase_for_account(services.game, hero.account_id, f"OFB-DBFH:{key}")

    no_chest = await opened(services, hero)
    await chests(database_url, number, 2)
    racing = await asyncio.gather(*(opened(services, hero) for _ in range(2)))

    assert no_chest is ChestRefusal.NO_CHEST
    assert sorted(isinstance(result, Product) for result in racing) == [False, True]
    assert ChestRefusal.NO_KEY in racing
    assert await held(database_url, "BFH_TREASURE_CHEST", number) == 1


@pytest.mark.usefixtures("prompt_actions")
async def test_the_chests_page_lists_each_heros_chests_and_what_a_chest_gives(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    brassbuckle = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await create_hero(base, "ann@example.test", "Tin_Tankard")
    engine = create_async_engine(database_url)
    store = SqlEntitlementStore(async_sessionmaker(engine))
    # Two rounds' ends, at which the dedicated server stacks the chests the hero picked up.
    for picked in (2, 1):
        await store.stack_uses(1, brassbuckle, "BFH_TREASURE_CHEST", picked)
    await engine.dispose()
    async with httpx2.AsyncClient() as visitor, httpx2.AsyncClient() as ann:
        signed_out = visible((await visitor.get(f"{base}{PAGE}")).content)
        await post_sign_in(ann, base, "ann@example.test", PASSWORD)
        mine = visible((await ann.get(f"{base}{PAGE}")).content)
        await post_sign_in(visitor, base, "bob@example.test", PASSWORD)
        heroless = visible((await visitor.get(f"{base}{PAGE}")).content)
        await create_hero(base, "bob@example.test", "Copperpot")
        chestless = visible((await visitor.get(f"{base}{PAGE}")).content)

    today = datetime.now(UTC)
    assert "Sign in to open chests" in signed_out and "Keys" not in signed_out
    assert "3 chests to open Royal Army Brassbuckle" in mine
    assert f"3 chests to open · last found {today.day} {today:%b %Y}" in mine
    assert "Tin_Tankard Commando · level 1 No chests yet No chests · none found yet" in mine
    assert "Keys 0 keys A key costs 499 Funds, belongs to the account" in mine
    assert "worth at least 499 Funds" in mine
    # Of the chest's drops in the invented catalogue, a Royal hero may get the tonic, the bandage
    # and the monkey, and a soldier the rifle too; a National hero the tonic and the bandage, and
    # a commando the longrifle too. A soldier's tonic shares its tier with the bandage, and the
    # rifle's tier with nothing.
    for page in (signed_out, mine):
        assert "Invented Tonic · 75 uses chance 30%\N{EN DASH}42.9%" in page
        assert "Invented Rifle · 7 days chance up to 28%" in page
        assert "Invented Monkey · permanent chance 10%\N{EN DASH}14.3%" in page
        assert "Invented Longrifle · permanent chance up to 2.2%" in page
    assert "The account has no hero yet." in heroless and "Create a hero" in heroless
    assert "No chests to open" in chestless and "Play Now as Copperpot" in chestless
