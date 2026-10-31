from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from babel import Locale
from starlette.datastructures import ImmutableMultiDict
from starlette.exceptions import HTTPException

from openfesl.core.catalogues import Catalogue, Item
from openfesl.core.deal_offers import offer_state
from openfesl.core.deal_picks import fit
from openfesl.core.deal_rules import DealRules, ItemSection, OfferSection
from openfesl.core.games import Game
from openfesl.core.store import ADMIN, Bundle, ListedBundle, Product
from openfesl.core.texts import BASE_LANGUAGE, languages
from openfesl.core.wallets import MOST_BALANCE
from openfesl.web.pages import whole_number
from openfesl.web.store_texts import StoreTexts

# The longest own name and description of an offer and the highest discount it takes in percent.
NAME_LONGEST = 64
ABOUT_LONGEST = 300
MOST_DISCOUNT = 90
# A day and time as the form's fields write them, in UTC.
CLOCK = "%Y-%m-%dT%H:%M"


@dataclass(frozen=True, slots=True)
class OfferDraft:
    """An admin offer in its editor as written.

    Attributes:
        items: The numbers of its products, in order.
        query: What the add panel searches for.
        pricing: price for a fixed price, discount for a share off the items' list prices.
        amount: The price or the share, as written.
        starts: When it starts, as written, in UTC.
        ends: When it ends, as written, in UTC.
        section: The key of the section that lists it.
        names: Its own name by language code.
        abouts: Its own description by language code.
    """

    items: tuple[int, ...]
    query: str
    pricing: str
    amount: str
    starts: str
    ends: str
    section: str
    names: dict[str, str]
    abouts: dict[str, str]


@dataclass(frozen=True, slots=True)
class OfferItem:
    """An item of an offer, as its editor lists it.

    Attributes:
        product: The product that the offer holds.
        name: The item's name.
        item: The catalogue's item.
    """

    product: Product
    name: str
    item: Item


@dataclass(frozen=True, slots=True)
class Candidate:
    """An item that the add panel's search found, with its prices in the deals' currency.

    Attributes:
        item: The item.
        name: Its name.
        products: Its products in the deals' currency, which the offer may hold.
    """

    item: Item
    name: str
    products: list[Product]


@dataclass(frozen=True, slots=True)
class OfferEditor:
    """An admin offer's editor: its fields as written and what they come to.

    Attributes:
        game: The game.
        rules: How the game's hot deals change, whose sections list the offer.
        number: The offer's bundle number; none for a new offer.
        state: scheduled, live, ended, or off while its section is switched off; empty for a new
            offer.
        name: The offer's name in the page's language, as it would be saved.
        draft: The fields as written.
        items: The offer's items.
        worth: The sum of its items' list prices.
        line: What the price comes to, in words; empty until it is known.
        sections: The keys of the sections that may list it.
        candidates: The items that the add panel's search found.
        refused: Why a field is refused, by its name.
        bundle: The offer as it is to be sold; none while a field is refused.
        languages: The languages of its own texts, English first, each with its name.
        icons: Whether the catalogue holds the items' icons.
    """

    game: Game
    rules: DealRules
    number: int | None
    state: str
    name: str
    draft: OfferDraft
    items: list[OfferItem]
    worth: int
    line: str
    sections: list[str]
    candidates: list[Candidate]
    refused: dict[str, str]
    bundle: Bundle | None
    languages: list[tuple[str, str]]
    icons: bool


def editor_of(
    texts: StoreTexts,
    rules: DealRules,
    catalogue: Catalogue,
    every: Mapping[int, Product],
    listed: ListedBundle | None,
    fields: ImmutableMultiDict[str, Any] | None,
) -> OfferEditor:
    """Return an admin offer's editor, with its fields as written where a form sent them.

    Args:
        texts: Writes the page's language for the offer's game.
        rules: How the game's hot deals change.
        catalogue: The game's imported catalogue in the page's language.
        every: Every product of the game's store by number, which the offer may hold.
        listed: The offer's bundle; none for a new offer.
        fields: The form's fields; none for the offer as it is saved.

    Raises:
        HTTPException: 400 for fields that no form of the editor sends.
    """
    game, language, now = texts.game, texts.language, datetime.now(UTC)
    sections = offer_sections(rules)
    draft = written(fields) if fields is not None else drafted(listed, sections, now)
    if draft.section not in sections or draft.pricing not in ("price", "discount"):
        raise HTTPException(400)
    items = []
    for number in draft.items:
        product = every.get(number)
        item = catalogue.item(product.item_id) if product else None
        if product is None or item is None or product.currency != rules.currency:
            raise HTTPException(400)
        items.append(OfferItem(product, catalogue.name(item.id) or str(item.id), item))
    worth = sum(found.product.price for found in items)
    refused: dict[str, str] = {}
    price, line = _priced(texts, rules, draft, worth, refused)
    if not items:
        refused["items"] = texts.text("web.admin.deals.offer.refused.items")
    if not draft.names.get(BASE_LANGUAGE):
        refused["name-en"] = texts.text("web.admin.deals.offer.refused.name")
    starts, ends = _moment(draft.starts), _moment(draft.ends)
    if starts is None:
        refused["starts"] = texts.text("web.admin.deals.offer.refused.when")
    if ends is None:
        refused["ends"] = texts.text("web.admin.deals.offer.refused.when")
    elif starts is not None and ends <= starts:
        refused["ends"] = texts.text(
            "web.admin.deals.offer.refused.dates",
            ends=texts.moment(ends),
            starts=texts.moment(starts),
        )
    found = fit(
        [found.item for found in items],
        list(game.heroes.factions) if game.heroes else [],
        list(game.heroes.classes) if game.heroes else [],
    )
    if items and found is None:
        whom = (
            texts.text(
                "web.admin.deals.offer.refused.fit.item",
                item=line.name,
                factions=texts.factions(line.item.factions),
                classes=texts.classes(line.item.classes),
            )
            for line in items
        )
        refused["items"] = texts.text("web.admin.deals.offer.refused.fit", items="; ".join(whom))
    bundle = None
    if not refused and price is not None and found is not None:
        english = draft.names[BASE_LANGUAGE]
        bundle = Bundle(
            name=english,
            description=draft.abouts.get(BASE_LANGUAGE) or None,
            currency=rules.currency,
            price=price,
            original_price=worth if worth > price else None,
            discount=whole_number(draft.amount, MOST_DISCOUNT)
            if draft.pricing == "discount"
            else None,
            faction=found[0],
            hero_class=found[1],
            starts=starts,
            ends=ends,
            members=draft.items,
            section=draft.section,
            source=ADMIN,
            texts={
                code: (draft.names.get(code) or english, draft.abouts.get(code) or None)
                for code in languages()[1:]
                if draft.names.get(code) or draft.abouts.get(code)
            },
        )
    locale = Locale.parse(language, sep="-")
    return OfferEditor(
        game,
        rules,
        listed.id if listed else None,
        offer_state(listed.bundle, now, rules) if listed else "",
        draft.names.get(language) or draft.names.get(BASE_LANGUAGE, ""),
        draft,
        items,
        worth,
        line,
        sections,
        _candidates(game, catalogue, every, rules, draft.query),
        refused,
        bundle,
        [(code, str(locale.languages.get(code, code))) for code in languages()],
        catalogue.contents.icons > 0,
    )


def drafted(listed: ListedBundle | None, sections: list[str], now: datetime) -> OfferDraft:
    """Return an offer's editor fields as it is saved, or those of a new offer.

    A new offer starts at once and runs a week.

    Args:
        listed: The offer's bundle; none for a new offer.
        sections: The keys of the sections that may list it.
        now: The moment.
    """
    if listed is None:
        start = now.replace(second=0, microsecond=0)
        week = start + timedelta(days=7)
        return OfferDraft(
            (),
            "",
            "discount",
            "",
            f"{start:{CLOCK}}",
            f"{week:{CLOCK}}",
            sections[0],
            {},
            {},
        )
    bundle = listed.bundle
    names = {BASE_LANGUAGE: bundle.name} | {code: own for code, (own, _) in bundle.texts.items()}
    abouts = {BASE_LANGUAGE: bundle.description or ""} | {
        code: about or "" for code, (_, about) in bundle.texts.items()
    }
    return OfferDraft(
        tuple(member.id for member in listed.members),
        "",
        "discount" if bundle.discount else "price",
        str(bundle.discount or bundle.price),
        f"{bundle.starts:{CLOCK}}" if bundle.starts else "",
        f"{bundle.ends:{CLOCK}}" if bundle.ends else "",
        bundle.section if bundle.section in sections else sections[0],
        names,
        abouts,
    )


def written(fields: ImmutableMultiDict[str, Any]) -> OfferDraft:
    """Return an offer's editor fields as a form wrote them, with an item dropped or added.

    Args:
        fields: The form's fields.

    Raises:
        HTTPException: 400 for an item that is no product number.
    """
    found = [str(value) for value in fields.getlist("item")]
    added, dropped = str(fields.get("add", "")), str(fields.get("drop", ""))
    if not all(value.isascii() and value.isdecimal() for value in [*found, added or "0"]):
        raise HTTPException(400)
    items = [int(value) for value in found]
    if dropped.isascii() and dropped.isdecimal() and int(dropped) < len(items):
        del items[int(dropped)]
    if added and int(added) not in items:
        items.append(int(added))

    def field(name: str) -> str:
        return str(fields.get(name, "")).strip()

    return OfferDraft(
        tuple(items),
        field("q"),
        field("pricing"),
        field("amount").removesuffix("%").strip(),
        field("starts"),
        field("ends"),
        field("section"),
        {code: field(f"name-{code}")[:NAME_LONGEST] for code in languages()},
        {code: field(f"about-{code}")[:ABOUT_LONGEST] for code in languages()},
    )


def offer_sections(rules: DealRules) -> list[str]:
    """Return the keys of the sections that may list an admin offer: those that sell items.

    Args:
        rules: How the game's hot deals change.
    """
    return [
        key
        for key, section in rules.sections.items()
        if isinstance(section, ItemSection) or not any(offer.box for offer in rules.offers(section))
    ]


def _moment(written: str) -> datetime | None:
    try:
        return datetime.strptime(written, CLOCK).replace(tzinfo=UTC)
    except ValueError:
        return None


def _priced(
    texts: StoreTexts, rules: DealRules, draft: OfferDraft, worth: int, refused: dict[str, str]
) -> tuple[int | None, str]:
    # The offer's price and what it comes to in words; a refused amount gives neither.
    currency = rules.currency
    if draft.pricing == "price":
        price = whole_number(draft.amount, MOST_BALANCE)
        if price is None:
            refused["amount"] = texts.text(
                "web.admin.deals.offer.refused.price", most=texts.amount(MOST_BALANCE)
            )
            return None, ""
        if not worth or price >= worth:
            return price, texts.priced(price, currency)
        line = texts.text(
            "web.admin.deals.offer.price.below",
            price=texts.priced(price, currency),
            share=texts.percent(1 - price / worth),
            sum=texts.amount(worth),
        )
        return price, line
    share = whole_number(draft.amount, MOST_DISCOUNT)
    if share is None:
        refused["amount"] = texts.text(
            "web.admin.deals.offer.refused.share", most=texts.amount(MOST_DISCOUNT)
        )
        return None, ""
    section = rules.sections[draft.section]
    if isinstance(section, OfferSection) and section.discount[1]:
        low, high = section.discount
        if not low <= share <= high:
            refused["amount"] = texts.text(
                "web.admin.deals.offer.refused.discount",
                section=texts.text(f"games.{texts.game.id}.deal_section.{draft.section}"),
                low=low,
                high=high,
                share=share,
            )
            return None, ""
    if not worth:
        return None, ""
    price = rules.rounded(worth * (100 - share) // 100)
    rounded = rules.price_ending is not None
    key = f"web.admin.deals.offer.discount.{'rounded' if rounded else 'result'}"
    line = texts.text(
        key,
        share=share,
        sum=texts.amount(worth),
        price=texts.priced(price, currency),
        ending=f"{rules.price_ending or 0:02}",
    )
    return price, line


def _candidates(
    game: Game,
    catalogue: Catalogue,
    every: Mapping[int, Product],
    rules: DealRules,
    query: str,
) -> list[Candidate]:
    # The items whose name holds the search, or whose number it is, with their products in the
    # deals' currency; an item without one is listed too, so the panel can say why.
    granted = game.heroes.granted if game.heroes else frozenset()
    found = catalogue.givable(query, granted)
    return [
        Candidate(
            item,
            name,
            [
                product
                for product in every.values()
                if product.item_id == item.id and product.currency == rules.currency
            ],
        )
        for item, name in found
    ]
