from collections.abc import AsyncIterator
from pathlib import Path
from random import Random

import pytest
from game_files import COPY, NEEDS_COPY
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from openfesl.core.catalogues import Catalogue, Catalogues
from openfesl.core.deal_picks import History, pick_items, pick_offers
from openfesl.core.deal_rules import ItemSection, OfferSection
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
    return History(frozenset(), frozenset(), frozenset(), products)


async def test_every_deal_of_the_game_data_is_made_of_the_copys_items_and_named_by_its_texts(
    catalogue: Catalogue,
    bfheroes: Game,
) -> None:
    game = bfheroes
    rules = game.deals
    assert rules is not None
    history = sold_alone(game, catalogue)

    made: dict[str, set[str]] = {}
    for name, section in rules.sections.items():
        if isinstance(section, ItemSection):
            deals = pick_items(game, rules, section, catalogue, history, Random(1))
        else:
            every = section.model_copy(update={"size": None})
            deals = pick_offers(game, rules, every, catalogue, history, Random(1))
        made[name] = {deal.bundle.faction or "" for deal in deals} | {
            deal.bundle.name for deal in deals if isinstance(section, OfferSection)
        }

    # Each faction's Deal of the Day, and every offer of the other sections.
    assert made["daily"] == {"royal", "national"}
    for name, section in rules.sections.items():
        if isinstance(section, OfferSection):
            assert {offer.name for offer in rules.offers(section)} <= made[name], name
    named = [
        text
        for offer in rules.sets
        + tuple(
            offer
            for section in rules.sections.values()
            if isinstance(section, OfferSection)
            for offer in section.offers
        )
        for text in (offer.name, offer.description)
        if text
    ]
    assert [text for text in named if catalogue.text(text) is None] == []
