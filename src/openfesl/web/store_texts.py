import math
from datetime import UTC, datetime, timedelta

from babel import Locale
from babel.dates import format_skeleton
from babel.lists import format_list
from babel.numbers import format_percent

from openfesl.core.catalogues import Catalogue
from openfesl.core.games import Game
from openfesl.core.inventory import COUNTED, PERMANENT, RENTED, Held, Ownership
from openfesl.core.reward_rules import Gift
from openfesl.core.store import discount
from openfesl.core.store_items import HeroChoice, Kind, PricePoint, Requirement, StoreItem, Unfit
from openfesl.core.texts import translate, translate_count
from openfesl.web.pages import moment, number

# The glyphs of the price tiles of a rental and of uses; one owned for good shows infinity.
_GLYPHS = {RENTED: "clock", COUNTED: "uses"}


class StoreTexts:
    """The sentences a store page says about what it sells and to whom, in a language."""

    def __init__(self, game: Game, language: str, now: datetime) -> None:
        """Write for a game's store in a language, at a moment.

        Args:
            game: The game, which has heroes.
            language: The language code.
            now: The moment that times left count from.
        """
        self.game = game
        self.language = language
        self._now = now

    def text(self, key: str, **values: object) -> str:
        """Return a text of the catalogues with its placeholders filled.

        Args:
            key: The text's id.
            values: Its placeholders.
        """
        return translate(key, self.language) % values if values else translate(key, self.language)

    def count(self, key: str, count: int, **values: object) -> str:
        """Return a text about a number of things in the number's plural form.

        Args:
            key: The texts' id without the plural category.
            count: The number, shown as %(count)s.
            values: Further placeholders.
        """
        text = translate_count(key, count, self.language)
        return text % {"count": self.amount(count), **values}

    def amount(self, value: int) -> str:
        """Return a number as the language writes it.

        Args:
            value: The number.
        """
        return number(value, self.language)

    def currency(self, code: str) -> str:
        """Return a currency's name.

        Args:
            code: The currency's code.
        """
        return translate(f"games.{self.game.id}.currency.{code}", self.language)

    def priced(self, price: int, code: str) -> str:
        """Return an amount with its currency's name, such as 499 Funds.

        Args:
            price: The amount.
            code: The currency's code.
        """
        return f"{self.amount(price)} {self.currency(code)}"

    def moment(self, value: datetime) -> str:
        """Return a day and time in UTC as the language writes them, with UTC after them.

        Args:
            value: The moment.
        """
        return self.text("web.store.utc", time=moment(value, self.language))

    def day(self, value: datetime) -> str:
        """Return a day in UTC as the language writes it, such as 26 Nov 2026.

        Args:
            value: The day.
        """
        return str(format_skeleton("yMMMd", value, tzinfo=UTC, locale=self._locale()))

    def countdown(self, ends: datetime) -> str:
        """Return the time left until a moment, as days and a clock, such as 1 d 23:22:40.

        Under a day the clock alone is left.

        Args:
            ends: The moment.
        """
        left = max(int((ends - self._now).total_seconds()), 0)
        days, rest = divmod(left, 86400)
        clock = f"{rest // 3600:02}:{rest % 3600 // 60:02}:{rest % 60:02}"
        return self.text("web.store.countdown", days=days, time=clock) if days else clock

    def percent(self, share: float) -> str:
        """Return a share as a percentage with at most one decimal, as the language writes it.

        Args:
            share: The share, from 0 to 1.
        """
        return str(format_percent(share, "#,##0.#%", locale=self._locale()))

    def off(self, price: PricePoint) -> str:
        """Return the share of its usual price that a sale takes off a price, in whole percent.

        Args:
            price: The price, which is on sale.
        """
        assert price.original_price
        return self.amount(discount(price.price, price.original_price))

    def ownership(self, ownership: Ownership, inline: bool = False) -> str:
        """Return how a hero owns an item: for good, a number of days or of uses.

        Args:
            ownership: The ownership.
            inline: Whether it goes inside a sentence, which writes it in lower case.
        """
        if ownership.type == RENTED:
            return self.count("web.store.price.days", ownership.days or 0)
        if ownership.type == COUNTED:
            return self.count("web.store.price.uses", ownership.uses or 0)
        return self.text("web.store.forever" if inline else "web.store.price.permanent")

    def gift_words(self, catalogue: Catalogue, gift: Gift) -> list[str]:
        """Return what a gift gives in words, such as M95 · 7 days or 1,350 Valor Points.

        Args:
            catalogue: The game's imported catalogue, which names the items.
            gift: The gift, of the game's rewards.
        """
        rules = self.game.rewards
        assert rules is not None
        words = [
            self.text(
                "web.deals.card.item",
                item=catalogue.name(item_id) or str(item_id),
                ownership=self.ownership(ownership, inline=True),
            )
            for item_id, ownership in gift.items
        ]
        amounts = [(gift.points, rules.points), (gift.funds, rules.currency)]
        words += [self.priced(value, code) for value, code in amounts if value and code]
        if gift.keys:
            words.append(self.count("web.rewards.gift.keys", gift.keys))
        return words

    def price_label(self, sold: StoreItem, price: PricePoint) -> str:
        """Return the title of a price's tile.

        Args:
            sold: What the page sells.
            price: The price.
        """
        if sold.kind is Kind.BOX:
            return self.text("web.store.price.box")
        if price.ownership is None:
            return self.text("web.store.price.bundle")
        return self.ownership(price.ownership)

    def chosen(self, sold: StoreItem) -> str:
        """Return what an item alone gives as its row says: the uses or time chosen, or for good.

        Args:
            sold: What the page sells, an item alone.
        """
        types = {price.ownership.type for price in sold.prices if price.ownership}
        if COUNTED in types:
            return self.text("web.store.chosen.uses")
        return self.text(
            "web.store.chosen.time" if RENTED in types else "web.store.price.permanent"
        )

    def glyph(self, sold: StoreItem, price: PricePoint) -> str:
        """Return the glyph of a price's tile: a box, a deal, a rental's clock, uses or for good.

        Args:
            sold: What the page sells.
            price: The price.
        """
        if sold.kind is not Kind.ITEM or price.ownership is None:
            return sold.kind.value
        return _GLYPHS.get(price.ownership.type, "infinity")

    def coin(self, code: str) -> str:
        """Return the glyph on a currency's coin, as the site's bar draws it.

        Args:
            code: The currency's code.
        """
        site = self.game.site
        shown = {wallet.currency: wallet.glyph for wallet in site.wallets} if site else {}
        return shown.get(code, "star")

    def factions(self, names: frozenset[str]) -> str:
        """Return the names of factions, joined with or.

        Args:
            names: The factions' names in the game data.
        """
        return self._either(f"games.{self.game.id}.faction.", names)

    def classes(self, names: frozenset[str]) -> str:
        """Return the names of classes, joined with or.

        Args:
            names: The classes' names in the game data.
        """
        return self._either(f"games.{self.game.id}.class.", names)

    def requires(self, requirement: Requirement) -> str:
        """Return which heroes may have something: faction, class and level.

        Args:
            requirement: The requirement.
        """
        parts = [
            self.factions(requirement.factions)
            if requirement.factions
            else self.text("web.store.requires.any_faction"),
            self.classes(requirement.classes)
            if requirement.classes
            else self.text("web.store.requires.any_class"),
            self.text("web.store.requires.level", level=requirement.level)
            if requirement.level > 1
            else self.text("web.store.requires.any_level"),
        ]
        return ", ".join(parts)

    def lead(self, sold: StoreItem) -> str:
        """Return the sentences under the page's title: how it is sold.

        Args:
            sold: What the page sells.
        """
        if sold.kind is Kind.BOX:
            return self.text("web.store.lead.box")
        if sold.kind is Kind.DEAL:
            said = [self.count("web.store.lead.deal", len(sold.contents))]
            [price] = sold.prices
            if price.original_price and price.original_price > price.price:
                said.append(self.text("web.store.lead.sale", off=self.off(price)))
            if sold.ends and not sold.ended:
                said.append(self.text("web.store.lead.until", until=self.moment(sold.ends)))
            return " ".join(said)
        types = {price.ownership.type for price in sold.prices if price.ownership}
        if COUNTED in types:
            return self.text("web.store.lead.uses")
        if RENTED in types:
            key = "web.store.lead.rental" if PERMANENT in types else "web.store.lead.rented"
            return self.text(key)
        return self.text("web.store.lead.permanent")

    def hint(self, sold: StoreItem) -> str:
        """Return the line that says what the price tiles are.

        Args:
            sold: What the page sells.
        """
        if sold.kind is Kind.BOX:
            return self.text("web.store.hint.box")
        if sold.kind is Kind.DEAL:
            return self.text("web.store.hint.deal" if sold.ends else "web.store.hint.bundle")
        codes = dict.fromkeys(price.currency for price in sold.prices)
        currencies = self.alternatives([self.currency(code) for code in codes])
        types = {price.ownership.type for price in sold.prices if price.ownership}
        if COUNTED in types:
            key = "web.store.hint.uses"
        elif RENTED in types:
            key = "web.store.hint.rental" if PERMANENT in types else "web.store.hint.rented"
        else:
            key = "web.store.hint.permanent"
        return self.text(key, currencies=currencies)

    def tile(self, sold: StoreItem, choice: HeroChoice) -> str:
        """Return the line of a hero's tile: why it cannot have it, or what it holds.

        Args:
            sold: What the page sells.
            choice: The hero.
        """
        requirement, held = sold.requirement, choice.held
        match choice.unfit:
            case Unfit.FACTION:
                return self.text(
                    "web.store.tile.faction", factions=self.factions(requirement.factions)
                )
            case Unfit.CLASS:
                return self.text("web.store.tile.class", classes=self.classes(requirement.classes))
            case Unfit.LEVEL:
                return self.text("web.store.tile.level", level=requirement.level)
            case Unfit.TAKEN:
                return self.text("web.store.tile.taken")
            case Unfit.ORDER:
                return self.text("web.store.tile.order")
        if sold.kind is not Kind.ITEM:
            return self.text("web.store.tile.can")
        if held is None:
            return self.text("web.store.tile.not_owned")
        if held.type == PERMANENT:
            return self.text("web.store.tile.permanent")
        if held.expires:
            return self.count("web.store.tile.days", self.days_left(held.expires))
        return self.count("web.store.tile.uses", held.uses or 0)

    def left(self, held: Held | None) -> str:
        """Return the days or uses a hero has left of an item, such as 9 days; empty for none.

        Args:
            held: How the hero holds the item; none when it holds it for good.
        """
        if held and held.expires:
            return self.count("web.store.price.days", self.days_left(held.expires))
        if held and held.type == COUNTED:
            return self.count("web.store.price.uses", held.uses or 0)
        return ""

    def days_left(self, ends: datetime) -> int:
        """Return the days left until a moment, counting a part of a day as a day.

        Args:
            ends: The moment.
        """
        return max(math.ceil((ends - self._now) / timedelta(days=1)), 0)

    def _either(self, prefix: str, names: frozenset[str]) -> str:
        return self.alternatives(
            [translate(prefix + name, self.language) for name in sorted(names)]
        )

    def alternatives(self, names: list[str]) -> str:
        """Return names joined with or, as the language joins them.

        Args:
            names: The names.
        """
        return str(format_list(names, style="or", locale=self._locale()))

    def _locale(self) -> Locale:
        return Locale.parse(translate("web.locale", self.language))
