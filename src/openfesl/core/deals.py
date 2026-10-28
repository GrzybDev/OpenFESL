from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from random import Random, SystemRandom
from typing import Protocol

from babel import Locale
from babel.lists import format_list

from openfesl.core.calendar import Period, Window, periods
from openfesl.core.catalogues import Catalogue, Catalogues
from openfesl.core.deal_picks import History, NewDeal, pick_items, pick_offers
from openfesl.core.deal_rules import DealRules, ItemSection, Offer, OfferSection
from openfesl.core.games import Game
from openfesl.core.overrides import overridden
from openfesl.core.policies import SettingStore
from openfesl.core.store import (
    AUTO,
    Bundle,
    ListedBundle,
    ProductStore,
    offer,
)
from openfesl.core.store import Offer as Listing
from openfesl.core.texts import BASE_LANGUAGE, translate

type Maker = Callable[[History], list[NewDeal]]


class DealStore(Protocol):
    """Where the rotation keeps the bundles it made."""

    async def generate(
        self, game: str, section: str, period: Period, gap: int, make: Maker
    ) -> None:
        """Store the bundles of a period of a section once, as the first to look makes them.

        Stores on other servers take turns, and a period that has bundles keeps them.

        Args:
            game: The game's data-file name.
            section: The section's name.
            period: The period.
            gap: The days after a pick left the section before it may come back.
            make: Makes the bundles from what the section must not repeat.
        """


@dataclass(frozen=True, slots=True)
class Deal:
    """A bundle as a game lists it among its hot deals, in one language.

    Attributes:
        listed: The bundle.
        product_id: The id the game buys it by.
        order: Its place among the hot deals, from 1.
        name: Its name in the language.
        description: What the game shows about it in the language.
        members: Its products as offers, named in the language.
    """

    listed: ListedBundle
    product_id: str
    order: int
    name: str
    description: str
    members: list[Listing]


class HotDeals:
    """The hot deals of the games' stores: the admin's offers and a rotation that changes itself.

    The rotation has no scheduler: the first look after a section's period starts makes its
    bundles, which the store keeps, so their prices hold for the period and later periods know
    what came before.
    """

    def __init__(
        self,
        products: ProductStore,
        store: DealStore,
        settings: SettingStore,
        catalogues: Catalogues,
        random: Random | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Run the rotation over what stores keep.

        Args:
            products: Where the store's products and bundles are kept.
            store: Where the rotation keeps the bundles it made.
            settings: The instance's settings, which override the rotation's game data.
            catalogues: The games' imported catalogues, whose items the deals sell.
            random: The source of chance; the system's own when none.
            clock: Tells the time; the system's own when none.
        """
        self._products = products
        self._store = store
        self._settings = settings
        self._catalogues = catalogues
        self._random = random or SystemRandom()
        self._clock = clock or (lambda: datetime.now(UTC))
        # The periods made or found made, which later looks need not ask the store about.
        self._made: set[tuple[str, str, datetime, datetime]] = set()

    async def rules(self, game: Game) -> DealRules | None:
        """Return how a game's hot deals change, with the instance's settings over its data.

        Args:
            game: The game.
        """
        if game.deals is None:
            return None
        prefix = f"{game.id}.deals."
        return overridden(game.deals, prefix, await self._settings.settings(prefix))

    async def current(self, game: Game) -> list[ListedBundle]:
        """Return the bundles a game's store sells now, in the order its hot deals list them.

        The rotation's bundles come first, by section, and the admin's after those of their
        section; a section switched off is left out, and with the rotation off only the
        admin's bundles are listed.

        Args:
            game: The game.
        """
        now = self._clock()
        rules = await self.rules(game)
        if rules is not None and rules.enabled:
            await self._rotate(game, rules, now)
        sections = list(rules.sections) if rules else []

        def shown(bundle: Bundle) -> bool:
            section = rules.sections.get(bundle.section) if rules and bundle.section else None
            if section is not None and not section.enabled:
                return False
            return bundle.source != AUTO or bool(rules and rules.enabled and section)

        def place(listed: ListedBundle) -> tuple[int, bool, int, int]:
            bundle = listed.bundle
            index = sections.index(bundle.section) if bundle.section in sections else len(sections)
            return index, bundle.source != AUTO, bundle.game_order, listed.id

        listed = await self._products.bundles(game.id, now)
        return sorted((found for found in listed if shown(found.bundle)), key=place)

    async def bundle(self, game: Game, number: int) -> ListedBundle | None:
        """Return a bundle a game's hot deals list now, if they list one by a number.

        Args:
            game: The game.
            number: The bundle's number.
        """
        return next((listed for listed in await self.current(game) if listed.id == number), None)

    async def hot_deals(self, game: Game, language: str) -> list[Deal]:
        """Return the bundles a game's store sells now, named in a language.

        A name or description is a text of the game's language table, else one of the
        server's own; a bundle without a description is described by its items' names.

        Args:
            game: The game.
            language: The language code; English where the texts lack it.
        """
        if game.store is None:
            return []
        catalogue = await self._catalogues.of(game, language)
        deals = []
        for order, listed in enumerate(await self.current(game), 1):
            bundle = listed.bundle
            members = [offer(game, catalogue, product) for product in listed.members]
            name = _text(game, catalogue, bundle.name, language, bundle.faction)
            names = [member.name for member in members]
            description = (
                _text(game, catalogue, bundle.description, language, bundle.faction)
                if bundle.description
                else format_list(names, locale=Locale.parse(language, sep="-"))
            )
            product_id = game.store.product_id(listed.id)
            deals.append(Deal(listed, product_id, order, name, description, members))
        return deals

    async def _rotate(self, game: Game, rules: DealRules, now: datetime) -> None:
        catalogue = await self._catalogues.of(game, BASE_LANGUAGE)
        if not catalogue.imported:
            return
        # Offers before items, so that a period's items avoid the offers that run with them.
        ordered = sorted(rules.sections.items(), key=lambda pair: pair[1].kind == "items")
        for name, section in ordered:
            if not section.enabled:
                continue
            for period, offers in _steps(rules, section, now):
                made = (game.id, name, period.start, period.end)
                if made in self._made:
                    continue
                gap = section.gap if isinstance(section, OfferSection) else 0
                make = partial(_picked, game, rules, section, offers, catalogue, self._random)
                await self._store.generate(game.id, name, period, gap, make)
                self._made.add(made)


def _steps(
    rules: DealRules, section: ItemSection | OfferSection, now: datetime
) -> list[tuple[Period, Sequence[Offer]]]:
    # The periods of the section's schedule that run, then those of its offers' windows, each
    # with its offers.
    if isinstance(section, ItemSection):
        return [(period, ()) for period in periods(section.schedule, now, rules.zone)]
    offers = rules.offers(section)
    plain = [given for given in offers if given.window is None]
    steps: list[tuple[Period, Sequence[Offer]]] = (
        [(period, plain) for period in periods(section.schedule, now, rules.zone)]
        if section.schedule
        else []
    )
    windows: dict[Window, list[Offer]] = {}
    for given in offers:
        if given.window is not None:
            windows.setdefault(given.window, []).append(given)
    for window, sold in windows.items():
        if running := window.running(now, rules.zone):
            steps.append((running, sold))
    return steps


def _picked(
    game: Game,
    rules: DealRules,
    section: ItemSection | OfferSection,
    offers: Sequence[Offer],
    catalogue: Catalogue,
    random: Random,
    history: History,
) -> list[NewDeal]:
    if isinstance(section, ItemSection):
        return pick_items(game, rules, section, catalogue, history, random)
    return pick_offers(game, rules, section, offers, catalogue, history, random)


def _text(game: Game, catalogue: Catalogue, name: str, language: str, faction: str | None) -> str:
    # A text of the game's language table, else of the server's own, with the faction's name.
    if found := catalogue.text(name):
        return found
    try:
        text = translate(name, language)
    except KeyError:
        return name
    named = translate(f"games.{game.id}.faction.{faction}", language) if faction else ""
    return text.format(faction=named)
