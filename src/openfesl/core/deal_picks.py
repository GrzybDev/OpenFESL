import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from random import Random

from openfesl.core.catalogues import Catalogue, Item
from openfesl.core.deal_rules import DealRules, ItemSection, Offer, OfferSection
from openfesl.core.games import Game
from openfesl.core.inventory import PERMANENT, Ownership
from openfesl.core.store import AUTO, Bundle, NewProduct, Product

logger = logging.getLogger(__name__)


type Priced = Mapping[tuple[int, Ownership, str], Product]

# Owning an item for good, as a Deal of the Day sells its items.
FOR_GOOD = Ownership(PERMANENT)


@dataclass(frozen=True, slots=True)
class History:
    """What a section's new period must not repeat, and what the store's products cost.

    Attributes:
        recent: What the section sold that ended within its gap or still runs, by pool.
        live: What the rotation's other sections sell when the period starts, by pool.
        live_items: The items of the rotation's bundles that run when the period starts.
        products: The store's products, sold alone or not, by item, ownership and currency.
        boxes: The store's boxes, by key.
    """

    recent: frozenset[str]
    live: frozenset[str]
    live_items: frozenset[int]
    products: Priced
    boxes: Mapping[str, SoldBox]


@dataclass(frozen=True, slots=True)
class SoldBox:
    """A box of a game's store as hot deals sell it.

    Attributes:
        id: Its number.
        currency: The currency of its price.
        price: Its price.
        faction: The faction whose heroes may open it; any when none.
        hero_class: The class whose heroes may open it; any when none.
    """

    id: int
    currency: str
    price: int
    faction: str | None
    hero_class: str | None


@dataclass(frozen=True, slots=True)
class NewDeal:
    """A bundle that a period of a section brings, with its products, which the store may lack.

    Attributes:
        bundle: The bundle; its members are the products'.
        products: Its products, those the store lacks at the price its rules give them.
    """

    bundle: Bundle
    products: tuple[NewProduct, ...]


def pick_offers(
    game: Game,
    rules: DealRules,
    section: OfferSection,
    offers: Sequence[Offer],
    catalogue: Catalogue,
    history: History,
    random: Random,
) -> list[NewDeal]:
    """Return the deals of a period of a section of offers: every offer, or a few of each faction.

    Offers picked together, such as a family's boxes of both factions, count as one. An offer
    that the section sold within its gap, or another section sells, is left out, as is one with
    an item the catalogue lacks or the store does not price, or a box the store lacks.

    Args:
        game: The game.
        rules: The game's hot-deal rules.
        section: The section.
        offers: The offers of the period.
        catalogue: The game's imported catalogue.
        history: What the section must not repeat, and what the products cost.
        random: The source of chance.
    """
    used = history.recent | history.live
    taken = {given.picked for given in offers if given.name in used}
    pools: dict[str, list[NewDeal]] = {}
    for given in offers:
        if given.picked in taken:
            continue
        deal = _offer_deal(game, rules, section, catalogue, history, given, random)
        if deal is not None:
            pools.setdefault(given.picked, []).append(deal)
    groups: dict[str | None, list[list[NewDeal]]] = {}
    for deals in pools.values():
        factions = {deal.bundle.faction for deal in deals}
        groups.setdefault(factions.pop() if len(factions) == 1 else None, []).append(deals)
    picked = []
    for found in groups.values():
        chosen = random.sample(found, min(section.size, len(found))) if section.size else found
        picked += [deal for deals in chosen for deal in deals]
    return _ordered(game, picked)


def pick_items(
    game: Game,
    rules: DealRules,
    section: ItemSection,
    catalogue: Catalogue,
    history: History,
    random: Random,
) -> list[NewDeal]:
    """Return the deals of a period of a section of items, a few of each faction.

    A deal holds items of one faction, each worn in another slot, that the store sells alone
    for good and that no other bundle of the rotation holds.

    Args:
        game: The game, which has a store and heroes.
        rules: The game's hot-deal rules.
        section: The section.
        catalogue: The game's imported catalogue.
        history: What the period must not repeat, and what the products cost.
        random: The source of chance.
    """
    assert game.store is not None and game.heroes is not None
    classes = frozenset(game.heroes.classes)
    taken = set(history.live_items)
    deals = []
    for faction in game.heroes.factions:
        by_slot: dict[str, list[Item]] = {}
        for item in catalogue.contents.items.values():
            if (
                item.slot in section.slots
                and item.factions == {faction}
                and classes <= item.classes
                and game.store.sellable(item)
                and catalogue.name(item.id)
                and _sold(history, item.id, rules.currency)
            ):
                by_slot.setdefault(item.slot, []).append(item)
        for number in range(section.size):
            slots = [
                slot for slot, items in by_slot.items() if any(i.id not in taken for i in items)
            ]
            if len(slots) < section.pieces:
                break
            chosen = [
                random.choice([item for item in by_slot[slot] if item.id not in taken])
                for slot in random.sample(sorted(slots), section.pieces)
            ]
            taken.update(item.id for item in chosen)
            members = tuple(
                NewProduct(item.id, FOR_GOOD, rules.currency, _price(history, item.id, rules))
                for item in sorted(chosen, key=lambda item: item.id)
            )
            worth = sum(product.price for product in members)
            price = rules.rounded(worth * (100 - section.discount) // 100)
            bundle = Bundle(
                name=section.name,
                description=None,
                currency=rules.currency,
                price=price,
                original_price=worth if worth > price else None,
                faction=faction,
                # Each deal of a period needs a pool of its own; the first keeps the faction's.
                pool=f"{faction}:{number}" if number else faction,
                source=AUTO,
            )
            deals.append(NewDeal(bundle, members))
    return _ordered(game, deals)


def _offer_deal(
    game: Game,
    rules: DealRules,
    section: OfferSection,
    catalogue: Catalogue,
    history: History,
    given: Offer,
    random: Random,
) -> NewDeal | None:
    assert game.store is not None and game.heroes is not None
    if given.box is not None:
        return _box_deal(history, given)
    items = [catalogue.item(item_id) for item_id, _ in given.members]
    found = [item for item in items if item is not None and game.store.sellable(item)]
    fit = _fit(found, list(game.heroes.factions), list(game.heroes.classes))
    products = [
        _product(game, catalogue, history, member, rules.currency) for member in given.members
    ]
    members = tuple(product for product in products if product)
    if len(found) < len(items) or fit is None or len(members) < len(products):
        logger.debug("Leaving out the offer %s, whose items are not all sold", given.name)
        return None
    worth = sum(product.price for product in members)
    low, high = section.discount
    discount = random.choice(range(low, high + 1, 5))
    price = given.price or rules.rounded(worth * (100 - discount) // 100)
    bundle = Bundle(
        name=given.name,
        description=given.description,
        currency=rules.currency,
        price=price,
        original_price=worth if worth > price else None,
        faction=fit[0],
        hero_class=fit[1],
        pool=given.name,
        source=AUTO,
    )
    return NewDeal(bundle, members)


def _box_deal(history: History, given: Offer) -> NewDeal | None:
    box = history.boxes.get(given.box or "")
    if box is None:
        logger.debug("Leaving out the offer %s, whose box the store lacks", given.name)
        return None
    bundle = Bundle(
        name=given.name,
        description=given.description,
        currency=box.currency,
        price=given.price or box.price,
        faction=box.faction,
        hero_class=box.hero_class,
        box=box.id,
        pool=given.name,
        source=AUTO,
    )
    return NewDeal(bundle, ())


def _product(
    game: Game, catalogue: Catalogue, history: History, member: tuple[int, Ownership], currency: str
) -> NewProduct | None:
    # An item's product as the store prices it, or as its rules price it once it is added.
    item_id, ownership = member
    product = history.products.get((item_id, ownership, currency))
    if product is not None:
        return NewProduct(item_id, ownership, currency, product.price, published=False)
    item = catalogue.item(item_id)
    rules = game.store
    price = rules.price_of(item, ownership, currency) if rules and item else None
    return NewProduct(item_id, ownership, currency, price, published=False) if price else None


def _sold(history: History, item_id: int, currency: str) -> bool:
    # Whether the store sells an item alone for good in a currency.
    product = history.products.get((item_id, FOR_GOOD, currency))
    return product is not None and product.published


def _price(history: History, item_id: int, rules: DealRules) -> int:
    return history.products[(item_id, FOR_GOOD, rules.currency)].price


def _fit(
    items: Sequence[Item], factions: Sequence[str], classes: Sequence[str]
) -> tuple[str | None, str | None] | None:
    # The one faction and class that every item fits, none where they fit all; nothing when
    # they fit none, or more than one but not all.
    shared_factions = frozenset(factions).intersection(*(item.factions for item in items))
    shared_classes = frozenset(classes).intersection(*(item.classes for item in items))
    if len(shared_factions) not in (1, len(factions)) or len(shared_classes) not in (
        1,
        len(classes),
    ):
        return None
    faction = min(shared_factions) if len(shared_factions) < len(factions) else None
    hero_class = min(shared_classes) if len(shared_classes) < len(classes) else None
    return faction, hero_class


def _ordered(game: Game, deals: list[NewDeal]) -> list[NewDeal]:
    # The heroes' factions in their order, then by name, each deal's place in its section.
    factions = list(game.heroes.factions) if game.heroes else []

    def place(deal: NewDeal) -> tuple[int, str]:
        faction = deal.bundle.faction
        return (factions.index(faction) if faction in factions else len(factions)), deal.bundle.name

    ordered = sorted(deals, key=place)
    return [
        replace(deal, bundle=replace(deal.bundle, game_order=index))
        for index, deal in enumerate(ordered)
    ]
