import math
from datetime import datetime, timedelta

from babel import Locale
from babel.lists import format_list

from openfesl.core.games import Game
from openfesl.core.inventory import COUNTED, PERMANENT, RENTED, Ownership
from openfesl.core.store_items import HeroChoice, PricePoint, Requirement, StoreItem, Unfit
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

    def ownership(self, ownership: Ownership) -> str:
        """Return how a hero owns an item: for good, a number of days or of uses.

        Args:
            ownership: The ownership.
        """
        if ownership.type == RENTED:
            return self.count("web.store.price.days", ownership.days or 0)
        if ownership.type == COUNTED:
            return self.count("web.store.price.uses", ownership.uses or 0)
        return self.text("web.store.price.permanent")

    def chosen(self, sold: StoreItem) -> str:
        """Return what an item alone gives as its row says: the uses or time chosen, or for good.

        Args:
            sold: What the page sells, an item alone.
        """
        types = {price.ownership.type for price in sold.prices}
        if COUNTED in types:
            return self.text("web.store.chosen.uses")
        return self.text(
            "web.store.chosen.time" if RENTED in types else "web.store.price.permanent"
        )

    def glyph(self, price: PricePoint) -> str:
        """Return the glyph of a price's tile: a rental's clock, uses or for good.

        Args:
            price: The price.
        """
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
        types = {price.ownership.type for price in sold.prices}
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
        codes = dict.fromkeys(price.currency for price in sold.prices)
        currencies = self._or([self.currency(code) for code in codes])
        types = {price.ownership.type for price in sold.prices}
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
            case Unfit.ORDER:
                return self.text("web.store.tile.order")
        if held is None:
            return self.text("web.store.tile.not_owned")
        if held.type == PERMANENT:
            return self.text("web.store.tile.permanent")
        if held.expires:
            return self.count("web.store.tile.days", self.days_left(held.expires))
        return self.count("web.store.tile.uses", held.uses or 0)

    def days_left(self, ends: datetime) -> int:
        """Return the days left until a moment, counting a part of a day as a day.

        Args:
            ends: The moment.
        """
        return max(math.ceil((ends - self._now) / timedelta(days=1)), 0)

    def _either(self, prefix: str, names: frozenset[str]) -> str:
        return self._or([translate(prefix + name, self.language) for name in sorted(names)])

    def _or(self, names: list[str]) -> str:
        return str(format_list(names, style="or", locale=self._locale()))

    def _locale(self) -> Locale:
        return Locale.parse(translate("web.locale", self.language))
