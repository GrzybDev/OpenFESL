from collections.abc import Mapping
from dataclasses import dataclass, replace
from random import Random

from openfesl.core.catalogues import Catalogue, Item
from openfesl.core.deal_rules import DealRules, ItemSection
from openfesl.core.games import Game
from openfesl.core.inventory import PERMANENT, Ownership
from openfesl.core.store import AUTO, Bundle, NewProduct, Product

type Priced = Mapping[tuple[int, Ownership, str], Product]

# Owning an item for good, as a Deal of the Day sells its items.
FOR_GOOD = Ownership(PERMANENT)


@dataclass(frozen=True, slots=True)
class History:
    """What a section's new period must not repeat, and what the store's products cost.

    Attributes:
        live_items: The items of the rotation's bundles that run when the period starts.
        products: The store's products, sold alone or not, by item, ownership and currency.
    """

    live_items: frozenset[int]
    products: Priced


@dataclass(frozen=True, slots=True)
class NewDeal:
    """A bundle that a period of a section brings, with its products, which the store may lack.

    Attributes:
        bundle: The bundle; its members are the products'.
        products: Its products, those the store lacks at the price its rules give them.
    """

    bundle: Bundle
    products: tuple[NewProduct, ...]


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


def _sold(history: History, item_id: int, currency: str) -> bool:
    # Whether the store sells an item alone for good in a currency.
    product = history.products.get((item_id, FOR_GOOD, currency))
    return product is not None and product.published


def _price(history: History, item_id: int, rules: DealRules) -> int:
    return history.products[(item_id, FOR_GOOD, rules.currency)].price


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
