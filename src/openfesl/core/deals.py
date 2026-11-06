from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from random import Random
from typing import Protocol

from babel import Locale
from babel.lists import format_list

from openfesl.core.calendar import Period, Window, periods
from openfesl.core.catalogues import Catalogue, Catalogues, game_text
from openfesl.core.deal_picks import History, NewDeal, pick_items, pick_offers
from openfesl.core.deal_rules import DealRules, ItemSection, Offer, OfferSection
from openfesl.core.deal_uploads import offer_of
from openfesl.core.games import Game
from openfesl.core.overrides import with_settings
from openfesl.core.policies import SettingStore
from openfesl.core.store import (
    AUTO,
    Bundle,
    ListedBundle,
    ProductStore,
    offer,
    store_rules,
)
from openfesl.core.store import Offer as Listing
from openfesl.core.texts import BASE_LANGUAGE

# How many hot deals the game's home screen shows, of which the first ones big.
HOME = 5
BIG = 2


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

    async def made(self, game: str, since: datetime) -> list[ListedBundle]:
        """Return the bundles the rotation made of a game that ran after a moment, by start.

        Args:
            game: The game's data-file name.
            since: The moment.
        """

    async def preview(
        self, game: str, section: str, period: Period, gap: int, make: Maker
    ) -> list[NewDeal]:
        """Return the bundles of a period of a section as they would be made now, keeping none.

        Args:
            game: The game's data-file name.
            section: The section's name.
            period: The period, which has not started.
            gap: The days after a pick left the section before it may come back.
            make: Makes the bundles from what the section must not repeat.
        """

    async def shown(self, game: str, section: str) -> dict[str, datetime]:
        """Return when the rotation last brought each offer of a section, by its picked name.

        Args:
            game: The game's data-file name.
            section: The section's name.
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
    what came before. A period's picks are drawn by chance seeded with the game, the section and
    the period's start, so that a preview of a step shows what the step then brings.
    """

    def __init__(
        self,
        products: ProductStore,
        store: DealStore,
        settings: SettingStore,
        catalogues: Catalogues,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Run the rotation over what stores keep.

        Args:
            products: Where the store's products and bundles are kept.
            store: Where the rotation keeps the bundles it made.
            settings: The instance's settings, which override the rotation's game data.
            catalogues: The games' imported catalogues, whose items the deals sell.
            clock: Tells the time; the system's own when none.
        """
        self._products = products
        self._store = store
        self._settings = settings
        self._catalogues = catalogues
        self._clock = clock or (lambda: datetime.now(UTC))
        # The periods made or found made, which later looks need not ask the store about.
        self._made: set[tuple[str, str, datetime, datetime]] = set()

    async def rules(self, game: Game) -> DealRules | None:
        """Return how a game's hot deals change, with the instance's settings over its data.

        Args:
            game: The game.
        """
        return await with_settings(game.deals, game.id, "deals", self._settings)

    async def current(self, game: Game, sold_out: bool = False) -> list[ListedBundle]:
        """Return the bundles a game's store sells now, in the order its hot deals list them.

        The rotation's bundles come first, by section, and the admin's after those of their
        section, but pinned offers first of all in theirs; a section switched off is left out,
        and with the rotation off only the admin's bundles are listed.

        Args:
            game: The game.
            sold_out: Whether bundles whose stock is sold are listed too, as no longer sold.
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
            if bundle.sold_out and not sold_out:
                return False
            return bundle.source != AUTO or bool(rules and rules.enabled and section)

        pinned = set(rules.pinned) if rules else set()

        def place(listed: ListedBundle) -> tuple[int, bool, bool, int, int]:
            bundle = listed.bundle
            index = sections.index(bundle.section) if bundle.section in sections else len(sections)
            unpinned = offer_of(bundle) not in pinned
            return index, unpinned, bundle.source != AUTO, bundle.game_order, listed.id

        listed = await self._products.bundles(game.id, now)
        return sorted((found for found in listed if shown(found.bundle)), key=place)

    async def bundle(self, game: Game, number: int) -> ListedBundle | None:
        """Return a bundle a game's hot deals list now, if they list one by a number.

        Args:
            game: The game.
            number: The bundle's number.
        """
        return next((listed for listed in await self.current(game) if listed.id == number), None)

    async def hot_deals(self, game: Game, language: str, sold_out: bool = False) -> list[Deal]:
        """Return the bundles a game's store sells now, named in a language, in the game's order.

        A name or description is a text of the game's language table, else one of the
        server's own; a bundle without a description is described by its items' names. As the
        game's home screen shows the first deals, the pinned ones come right after its big ones.

        Args:
            game: The game.
            language: The language code; English where the texts lack it.
            sold_out: Whether bundles whose stock is sold are listed too, as no longer sold.
        """
        if game.store is None:
            return []
        catalogue = await self._catalogues.of(game, language)
        rules = await self.rules(game)
        pinned = set(rules.pinned) if rules else set()
        found = await self.current(game, sold_out)
        rest = found[BIG:]
        ordered = [
            *found[:BIG],
            *(listed for listed in rest if offer_of(listed.bundle) in pinned),
            *(listed for listed in rest if offer_of(listed.bundle) not in pinned),
        ]
        return [
            _deal(game, catalogue, listed, order, language)
            for order, listed in enumerate(ordered, 1)
        ]

    async def deal(self, game: Game, listed: ListedBundle, language: str) -> Deal:
        """Return one bundle of a game's store named in a language, as its hot deals name it.

        Args:
            game: The game, which has a store.
            listed: The bundle, sold now or not; its place among the hot deals is 0.
            language: The language code; English where the texts lack it.
        """
        return _deal(game, await self._catalogues.of(game, language), listed, 0, language)

    async def shown(self, game: Game, section: str) -> dict[str, datetime]:
        """Return when the rotation last brought each offer of a section, by its picked name.

        Args:
            game: The game.
            section: The section's name.
        """
        return await self._store.shown(game.id, section)

    async def draw(
        self, game: Game, rules: DealRules, starts: Mapping[str, datetime]
    ) -> dict[str, list[NewDeal]]:
        """Return the deals that sections' periods starting at a moment bring, as made now.

        The draws are those that making the periods then gives, while what they read stays as
        it is now; nothing is kept.

        Args:
            game: The game.
            rules: How the game's hot deals change.
            starts: The moment of each section's periods, by section.

        Returns:
            The deals of each section; none before the game's catalogue is imported.
        """
        catalogue = await self._catalogues.of(game, BASE_LANGUAGE)
        if not catalogue.imported:
            return {}
        priced = game.model_copy(update={"store": await store_rules(game, self._settings)})
        drawn: dict[str, list[NewDeal]] = {}
        for name, at in starts.items():
            section = rules.sections[name]
            gap = section.gap if isinstance(section, OfferSection) else 0
            drawn[name] = []
            for period, offers in _steps(rules, section, at):
                if period.start == at:
                    chance = _chance(game, name, period)
                    make = partial(_picked, priced, rules, section, offers, catalogue, chance)
                    drawn[name] += await self._store.preview(game.id, name, period, gap, make)
        return drawn

    async def _rotate(self, game: Game, rules: DealRules, now: datetime) -> None:
        catalogue = await self._catalogues.of(game, BASE_LANGUAGE)
        if not catalogue.imported:
            return
        # Items the store lacks products of are priced by its rules as the instance sets them.
        game = game.model_copy(update={"store": await store_rules(game, self._settings)})
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
                chance = _chance(game, name, period)
                make = partial(_picked, game, rules, section, offers, catalogue, chance)
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


def _chance(game: Game, section: str, period: Period) -> Random:
    # The same draw for a period of a section wherever and whenever it is made.
    return Random(f"{game.id}:{section}:{period.start.isoformat()}")


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


def _deal(
    game: Game, catalogue: Catalogue, listed: ListedBundle, order: int, language: str
) -> Deal:
    assert game.store is not None
    bundle = listed.bundle
    members = [offer(game, catalogue, product) for product in listed.members]
    own, about = bundle.texts.get(language, (None, None))
    name = own or game_text(game, catalogue, bundle.name, language, bundle.faction)
    if not about and bundle.description:
        about = game_text(game, catalogue, bundle.description, language, bundle.faction)
    names = [member.name for member in members]
    description = about or format_list(names, locale=Locale.parse(language, sep="-"))
    product_id = game.store.product_id(listed.id)
    return Deal(listed, product_id, order, name, description, members)
