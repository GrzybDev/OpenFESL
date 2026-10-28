from collections.abc import AsyncIterator
from pathlib import Path
from random import Random

import pytest
from game_files import COPY, NEEDS_COPY
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from openfesl.core.catalogues import Catalogue, Catalogues
from openfesl.core.deal_picks import History, pick_items
from openfesl.core.games import Game, load_games
from openfesl.core.store import Product
from openfesl.extraction import extract, find_game
from openfesl.storage.catalogues import SqlCatalogueStore
from openfesl.storage.database import migrate

# The living site is that of 1.89, so a copy of 1.46, which has no hot deals, is skipped.
pytestmark = [NEEDS_COPY, pytest.mark.anyio]


@pytest.fixture
async def catalogue(database_url: str, bfheroes: Game) -> AsyncIterator[Catalogue]:
    assert COPY
    folder = Path(COPY)
    if b"game/hotDeals" not in (folder / bfheroes.executable).read_bytes():
        pytest.skip("the copy is of a version without hot deals")
    written = extract(find_game(load_games(), folder), folder).write()
    engine = create_async_engine(database_url)
    await migrate(engine)
    catalogues = Catalogues(SqlCatalogueStore(async_sessionmaker(engine)), None, load_games())
    try:
        await catalogues.import_file(written, admin=None)
        yield await catalogues.of(bfheroes, "en")
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE catalogue, admin_action CASCADE"))
        await engine.dispose()


def sold_alone(game: Game, catalogue: Catalogue) -> History:
    """Return a history of nothing sold before, with every product the store's data seeds."""
    rules = game.store
    assert rules
    products = {
        (item.id, ownership, rule.currency): Product(
            0, item.id, ownership, rule.currency, price, None, 0
        )
        for item in catalogue.contents.items.values()
        if rules.sellable(item) and catalogue.name(item.id)
        for rule in rules.rules(item)
        for ownership in rule.sold
        if (price := rules.price(rule, ownership))
    }
    return History(frozenset(), products)


async def test_the_deal_of_the_day_of_the_game_data_is_made_of_the_copys_items(
    catalogue: Catalogue,
    bfheroes: Game,
) -> None:
    game = bfheroes
    rules = game.deals
    assert rules is not None
    history = sold_alone(game, catalogue)

    made = {
        name: {
            deal.bundle.faction
            for deal in pick_items(game, rules, section, catalogue, history, Random(1))
        }
        for name, section in rules.sections.items()
    }

    # Each faction's Deal of the Day.
    assert made["daily"] == {"royal", "national"}
