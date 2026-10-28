import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from random import Random

import pytest
from accounts import execute, select_value
from heroes import MAGMA, wallets
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from store import ROYAL_SOLDIER, shopper, stock
from support import fetch

from openfesl.core.catalogues import Catalogues
from openfesl.core.chests import ChestRefusal, ChestRefused, Chests
from openfesl.core.drops import Drops
from openfesl.core.games import Game, load_games
from openfesl.core.heroes import Heroes
from openfesl.core.personas import Persona
from openfesl.core.purchases import Purchases
from openfesl.core.store import Product
from openfesl.storage.catalogues import SqlCatalogueStore
from openfesl.storage.entitlements import SqlEntitlementStore
from openfesl.storage.heroes import SqlHeroStore
from openfesl.storage.personas import SqlPersonaStore
from openfesl.storage.purchases import SqlPurchaseStore
from openfesl.storage.stats import SqlStatStore
from openfesl.storage.store import SqlProductStore

pytestmark = pytest.mark.anyio


@dataclass(frozen=True, slots=True)
class Services:
    """The services the treasure-chest page will call, which no page calls yet."""

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
    purchases = Purchases(products, paid, catalogues, heroes, entitlements, drops)
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


@pytest.mark.usefixtures("prompt_actions", "base")
async def test_a_key_bought_with_funds_opens_a_stacked_chest_into_an_item_that_fits_the_hero(
    services: Services, server_url: str, database_url: str
) -> None:
    cookie, number = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    hero = await hero_of(services, number)
    await chests(database_url, number, 2)
    key = await select_value(database_url, "SELECT id FROM store_product WHERE account")

    # The site sells the key, which the game would not know, to the account.
    await services.purchases.purchase_for_account(services.game, hero.account_id, f"OFB-DBFH:{key}")
    won = await opened(services, hero)
    without_key = await opened(services, hero)

    assert isinstance(won, Product)
    _, _, body = await fetch(server_url, f"/nucleus/entitlements/{number}", MAGMA, cookie)
    assert won.ownership.tag(won.item_id).encode() in body
    # Of the chest's drops in the invented catalogue, those of the Royal soldier.
    assert won.item_id in {2153, 2005, 3015, 521}
    assert without_key is ChestRefusal.NO_KEY
    assert (
        await held(database_url, "7353-lmct"),
        await held(database_url, "BFH_TREASURE_CHEST", number),
    ) == (0, 1)
    assert (await wallets(server_url, cookie, number))["_PF"] == 2500 - 499
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
