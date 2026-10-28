from collections.abc import AsyncIterator
from pathlib import Path
from random import Random

import pytest
from game_files import COPY, NEEDS_COPY
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from openfesl.core.catalogues import Catalogue, Catalogues
from openfesl.core.deal_picks import History, SoldBox, pick_items, pick_offers
from openfesl.core.deal_rules import ItemSection, OfferSection
from openfesl.core.games import Game, load_games
from openfesl.core.store import Product
from openfesl.core.texts import translate
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
    """Return a history of nothing sold before, with the products and boxes the store seeds."""
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
    boxes = {
        box.key: SoldBox(0, box.currency, box.value, box.faction, box.hero_class)
        for box in rules.boxes
    }
    return History(frozenset(), frozenset(), frozenset(), products, boxes)


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
            offers = rules.offers(section)
            deals = pick_offers(game, rules, every, offers, catalogue, history, Random(1))
        made[name] = {deal.bundle.faction or "" for deal in deals} | {
            deal.bundle.name for deal in deals if isinstance(section, OfferSection)
        }

    # Each faction's Deal of the Day, and every offer and box of the other sections.
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


async def test_every_reward_of_the_game_data_names_the_copys_items_and_texts(
    catalogue: Catalogue,
    bfheroes: Game,
) -> None:
    game = bfheroes
    rules = game.rewards
    assert rules is not None
    boxes = {box.key for box in game.store.boxes} if game.store else set()

    unfit: list[tuple[object, ...]] = []
    texts: list[str] = []
    for key, event in rules.events.items():
        last = event.window.days(2026)
        days = range(1, (last[1] - last[0]).days + 2) if event.daily else [None]
        for part in event.parts.values():
            for day in days:
                gift = part.on(day)
                texts += [
                    text.replace("{day}", str(day))
                    for text in (part.name, part.description)
                    if text
                ]
                for item_id, _ in gift.items:
                    item = catalogue.item(item_id)
                    if item is None or part.faction not in item.factions:
                        unfit.append((key, day, item_id))
                assert gift.box is None or gift.box in boxes
    packs = rules.starter.packs if rules.starter else {}
    for faction, classes in packs.items():
        for hero_class, gift in classes.items():
            for item_id, _ in gift.items:
                item = catalogue.item(item_id)
                if item is None or faction not in item.factions or hero_class not in item.classes:
                    unfit.append((faction, hero_class, item_id))
    named = [*rules.milestones.values(), *[rules.starter, rules.first_chest]]
    texts += [text for found in named if found for text in (found.name, found.description) if text]
    fallback = catalogue.item(rules.fallback[0])

    assert unfit == []
    assert fallback and fallback.kind == "ability"
    # A text of the game's language table, else one of the server's own.
    missing = [text for text in texts if catalogue.text(text) is None and not translated(text)]
    assert missing == []


def translated(key: str) -> bool:
    try:
        translate(key, "pl")
    except KeyError:
        return False
    return True
