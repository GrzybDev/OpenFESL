from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.catalogues import Catalogue, Catalogues, paragraphs
from openfesl.core.deal_rules import DealRules, ItemSection
from openfesl.core.deals import Deal, HotDeals
from openfesl.core.games import Game, Games
from openfesl.core.hero_rules import HeroRules, SalePrice
from openfesl.core.heroes import Hero, Heroes
from openfesl.core.rewards import Rewards
from openfesl.core.store import Offer as Listing
from openfesl.core.store import ProductStore, discount
from openfesl.core.texts import localize
from openfesl.web.catalogue_files import icon_url
from openfesl.web.heroes import HEROES, SLOTS
from openfesl.web.pages import Pages
from openfesl.web.site import site_bar
from openfesl.web.store import ITEM
from openfesl.web.store_front import kind_glyphs
from openfesl.web.store_texts import StoreTexts

# The shelf of the admin's own offers that name no section of the rotation.
OFFERS = "offers"
# The shelf that the sales of hero slots and new names open.
SALE = "sale"
# The most item icons a card shows, and the most items it names, a line each or the first of
# a set in one line.
MOST_ICONS = 6
SHOWN_ITEMS = 3


@dataclass(frozen=True, slots=True)
class Icon:
    """An item of a deal as its card draws it.

    Attributes:
        src: The address of its imported icon, where the page shows the game's art.
        glyph: The glyph of its kind, which OpenFESL's tile draws in place of the icon.
    """

    src: str | None
    glyph: str


@dataclass(frozen=True, slots=True)
class DealCard:
    """A hot deal as its card on the hot deals page shows it.

    Attributes:
        number: Its bundle's number, or the name of what a sale that is no bundle sells.
        href: The address of its page, where it is bought.
        name: Its name.
        box: Whether it is a random box.
        off: How much of its usual price it takes off, in whole percent; empty without a sale.
        price: Its price as the page's language writes it.
        original: Its usual price, while it is on sale.
        currency: The currency of its price.
        faction: The faction whose heroes may have it; none for any hero's.
        icons: Its items as the card draws them; none for a box.
        lines: What it holds or may give, a line each.
        requires: Which heroes may have it.
        heroes: The names of the account's heroes who may; none when nobody is signed in.
        stock: How many are left of a stock, while some are; empty without a stock.
        sold_out: That its stock is sold, while it stays on the page until its end; empty
            while some are left.
        ends: When it ends or leaves the page; none when it stays.
        soon: Whether that is a day away at most, which its countdown says in red.
        left: The time it has left, as the countdown starts it.
        until: When it ends, as the page's language writes it.
        leaves: Whether it leaves the page for a while rather than end.
        free: Whether it is a box that an event that runs gives free once a day.
        action: What the way to its page says; the deals' usual words when empty.
    """

    number: int | str
    href: str
    name: str
    box: bool
    off: str
    price: str
    original: str
    currency: str
    faction: str | None
    icons: list[Icon]
    lines: list[str]
    requires: str
    heroes: list[str] | None
    stock: str
    sold_out: str
    ends: datetime | None
    soon: bool
    left: str
    until: str
    leaves: bool
    free: bool
    action: str = ""


@dataclass(frozen=True, slots=True)
class Shelf:
    """A section of the hot deals page with its cards.

    Attributes:
        key: Its name, which the ids of its texts hold.
        cards: Its deals.
        discount: The discount of its deals in percent, where its rules give one alone.
        sales: Whether it holds the sales of hero slots and new names.
    """

    key: str
    cards: list[DealCard]
    discount: int | None = None
    sales: bool = False


class HotDealsPages:
    """The hot deals page of a game's site.

    It lists the rotation's sections, the admin's offers and the sales of hero slots and new names.
    """

    def __init__(
        self,
        pages: Pages,
        games: Games,
        deals: HotDeals,
        catalogues: Catalogues,
        products: ProductStore,
        rewards: Rewards,
        heroes: Heroes,
    ) -> None:
        """Prepare the hot deals page of each game that lists it.

        Args:
            pages: Renders the pages.
            games: The games, of which those listing the page get it.
            deals: Lists the deals sold now, making the rotation's as their periods start.
            catalogues: Name and draw the deals' items in the page's language.
            products: Hold the boxes that random boxes give from.
            rewards: Tell which boxes the events that run give free each day.
            heroes: Tell which sales of hero slots and new names run.
        """
        self._pages = pages
        self._games = games
        self._deals = deals
        self._catalogues = catalogues
        self._products = products
        self._rewards = rewards
        self._heroes = heroes

    def routes(self) -> APIRouter:
        """Return the hot deals page of each game that lists it."""
        router = APIRouter()
        for game in self._games.with_page("hot-deals"):
            assert game.site is not None
            path = f"/{game.id}{game.site.path('hot-deals')}"
            router.add_api_route(path, self._page(game), response_class=HTMLResponse)
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_deals(request: Request) -> Response:
            language = request.state.language
            texts = StoreTexts(game, language, datetime.now(UTC))
            bar = await site_bar(request)
            heroes = bar.roster.heroes if bar and bar.roster else None
            rules = await self._deals.rules(game)
            catalogue = await self._catalogues.of(game, language)
            shown = self._pages.shows_art(request, game)
            # The shelves in the order of the rotation's sections, each where its first section
            # is.
            sections = rules.sections.items() if rules else []
            shelves: dict[str, list[DealCard]] = {
                section.shelf or name: [] for name, section in sections
            }
            free = {
                gift.box
                for run in await self._rewards.running(game)
                if run.event.daily
                for part in run.event.parts.values()
                if (gift := part.on(run.day)).box
            }
            for deal in await self._deals.hot_deals(game, language, sold_out=True):
                card = await deal_card(
                    texts, catalogue, self._products, deal, heroes, shown, rules, free
                )
                # The admin's offers that name no section of the rotation have a shelf of their
                # own.
                section = deal.listed.bundle.section
                found = rules.sections.get(section) if rules and section else None
                shelf = (found.shelf or section) if found and section else OFFERS
                shelves.setdefault(shelf, []).append(card)
            sales = _sale_cards(texts, await self._heroes.rules(game)) if game.heroes else []
            if sales:
                shelves[SALE] = sales + shelves.get(SALE, [])
            return self._pages.render(
                request,
                "store.HotDeals",
                game=game,
                shelves=[
                    Shelf(key, cards, _discount(rules, key), bool(sales) and key == SALE)
                    for key, cards in shelves.items()
                    if cards
                ],
                rotation=bool(rules and rules.enabled),
                midnight=texts.text("web.deals.midnight", zone=rules.zone if rules else "UTC"),
            )

        return show_deals


async def deal_card(
    texts: StoreTexts,
    catalogue: Catalogue,
    products: ProductStore,
    deal: Deal,
    heroes: list[Hero] | None,
    shown: bool,
    rules: DealRules | None,
    free: set[str],
) -> DealCard:
    """Return a hot deal as its card shows it.

    Args:
        texts: Writes the page's language for the deal's game.
        catalogue: The game's imported catalogue, which names and draws the deal's items.
        products: Hold the boxes that random boxes give from.
        deal: The deal.
        heroes: The account's heroes, which the card says may have it; none for a visitor.
        shown: Whether the page shows the game's art to its viewer.
        rules: How the game's rotation makes deals; none without a rotation.
        free: The keys of the boxes that an event that runs gives free once a day.
    """
    game = texts.game
    listed = deal.listed
    bundle = listed.bundle
    lines: list[str] = []
    icons: list[Icon] = []
    box = await products.box(game.id, bundle.box) if bundle.box is not None else None
    if box is not None:
        count = len(box.drops)
        if box.worth_rule:
            worth = texts.priced(box.value, box.currency)
            lines.append(texts.count("web.deals.card.box.worth", count, worth=worth))
        else:
            lines.append(texts.count("web.deals.card.box", count))
        if bundle.description:
            lines.append(" ".join(paragraphs(deal.description)))
    else:
        lines = _items(texts, deal)
        glyphs = kind_glyphs(game)
        for member in deal.members[:MOST_ICONS]:
            src = icon_url(game.id, catalogue, member.product.item_id, shown)
            icons.append(Icon(src, glyphs.get(member.kind, "bag")))
    faction, hero_class = bundle.faction, bundle.hero_class
    fit = [
        hero.persona.name
        for hero in heroes or []
        if (not faction or hero.faction == faction)
        and (not hero_class or hero.hero_class == hero_class)
    ]
    section = rules.sections.get(bundle.section) if rules and bundle.section else None
    original = bundle.original_price
    left = bundle.stock - bundle.sold if bundle.stock is not None else 0
    ends = bundle.ends
    return DealCard(
        number=listed.id,
        href=localize(f"/{game.id}{ITEM.format(number=listed.id)}", texts.language),
        name=deal.name,
        box=box is not None,
        off=texts.amount(discount(bundle.price, original))
        if original and original > bundle.price
        else "",
        price=texts.amount(bundle.price),
        original=texts.amount(original) if original and original > bundle.price else "",
        currency=bundle.currency,
        faction=faction,
        icons=icons,
        lines=lines,
        requires=_requires(texts, faction, hero_class),
        heroes=fit if heroes is not None else None,
        stock=texts.count("web.deals.card.only_left", left) if left > 0 else "",
        sold_out=texts.count("web.deals.card.sold_out", bundle.stock or 0)
        if bundle.sold_out
        else "",
        ends=ends,
        soon=bool(ends and texts.days_left(ends) <= 1),
        left=texts.countdown(ends) if ends else "",
        until=texts.moment(ends) if ends else "",
        leaves=bool(section and section.leaves),
        free=box is not None and box.key in free,
    )


def _sale_cards(texts: StoreTexts, rules: HeroRules) -> list[DealCard]:
    # The sales of a hero slot and a new name that run, which no rotation sells and no game's own
    # hot deals list, as a hero slot and a name are no products.
    game, cards = texts.game, []

    def card(
        name: str, sale: SalePrice, price: int, glyph: str, href: str, lines: list[str], action: str
    ) -> DealCard:
        return DealCard(
            number=name,
            href=localize(f"/{game.id}{href}", texts.language),
            name=texts.text(f"web.sale.{name}.name"),
            box=False,
            off=texts.amount(sale.off),
            price=texts.amount(price),
            original=texts.amount(sale.usual),
            currency=rules.currency,
            faction=None,
            icons=[Icon(None, glyph)],
            lines=lines,
            requires=texts.text("web.sale.requires"),
            heroes=None,
            stock="",
            sold_out="",
            ends=sale.ends,
            soon=texts.days_left(sale.ends) <= 1,
            left=texts.countdown(sale.ends),
            until=texts.moment(sale.ends),
            leaves=False,
            free=False,
            action=action,
        )

    if rules.slot_sale:
        lines = [texts.text("web.sale.slot.line"), texts.text("web.sale.slot.kept")]
        action = texts.text("web.slots.buy")
        cards.append(card("slot", rules.slot_sale, rules.slot_price, "coin", SLOTS, lines, action))
    if rules.rename_sale:
        lines = [
            texts.text("web.sale.rename.line"),
            texts.text("web.sale.rename.login", game=game.name),
        ]
        action = texts.text("web.deals.card.choose")
        price = rules.rename_price
        cards.append(card("rename", rules.rename_sale, price, "edit", HEROES, lines, action))
    return cards


def _discount(rules: DealRules | None, shelf: str) -> int | None:
    # The discount that every deal of a shelf's sections takes, if their rules give one alone.
    shelved = [
        section
        for name, section in (rules.sections.items() if rules else [])
        if (section.shelf or name) == shelf
    ]
    found = {
        section.discount if isinstance(section, ItemSection) else section.discount[0]
        for section in shelved
        if isinstance(section, ItemSection) or section.discount[0] == section.discount[1]
    }
    return found.pop() if len(found) == 1 else None


def _items(texts: StoreTexts, deal: Deal) -> list[str]:
    # A line for each of up to three items with how a hero owns it; more as one line of the
    # first names, and how they are owned where it is the same for all.
    def owned(member: Listing) -> str:
        return texts.ownership(member.product.ownership, inline=True)

    members = deal.members
    if len(members) <= SHOWN_ITEMS:
        return [
            texts.text("web.deals.card.item", item=member.name, ownership=owned(member))
            for member in members
        ]
    names = texts.text(
        "web.deals.card.more",
        names=", ".join(member.name for member in members[:SHOWN_ITEMS]),
        count=texts.amount(len(members) - SHOWN_ITEMS),
    )
    kinds = {owned(member) for member in members}
    return (
        [texts.text("web.deals.card.item", item=names, ownership=kinds.pop())]
        if len(kinds) == 1
        else [names]
    )


def _requires(texts: StoreTexts, faction: str | None, hero_class: str | None) -> str:
    # Which heroes a deal is for: its faction's, its class's, both, or any hero.
    named = {
        "faction": texts.factions(frozenset({faction})) if faction else "",
        "hero_class": texts.classes(frozenset({hero_class})) if hero_class else "",
    }
    if faction and hero_class:
        return texts.text("web.deals.card.requires.both", **named)
    if faction:
        return texts.text("web.deals.card.requires.faction", **named)
    if hero_class:
        return texts.text("web.deals.card.requires.class", **named)
    return texts.text("web.store.front.any_hero")
