from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime

from openfesl.core.games import Scope
from openfesl.core.inventory import COUNTED, PERMANENT, RENTED, Held, fold
from openfesl.core.store_items import HeroChoice, PricePoint, StoreItem, Unfit
from openfesl.core.texts import translate
from openfesl.web.store_texts import StoreTexts


@dataclass(frozen=True, slots=True)
class Quote:
    """A purchase as a store page offers it: one hero, one price and what the hero gets.

    Attributes:
        sold: What the page sells.
        choice: The hero.
        price: The price.
        after: How the hero holds the item once it is bought; none when buying gains the hero
            nothing.
    """

    sold: StoreItem
    choice: HeroChoice
    price: PricePoint
    after: Held | None

    @property
    def balance(self) -> int:
        """What the hero has to pay the price with."""
        return self.choice.balances.get(self.price.currency, 0)

    @property
    def short(self) -> int:
        """How much of the price the hero lacks."""
        return max(self.price.price - self.balance, 0)

    @property
    def open(self) -> bool:
        """Whether the hero may have it at all: it fits and gains something."""
        return self.choice.unfit is None and not self.choice.owned

    @property
    def buyable(self) -> bool:
        """Whether the purchase may be sent: it is open and the hero can pay for it."""
        return self.open and not self.short


def quote(sold: StoreItem, choice: HeroChoice, price: PricePoint, now: datetime) -> Quote:
    """Return what a hero gets for one of the prices of a store page.

    Args:
        sold: What the page sells.
        choice: The hero.
        price: The price.
        now: The time the purchase would be made.
    """
    return Quote(sold, choice, price, fold(choice.held, price.ownership, now))


class QuoteTexts(StoreTexts):
    """The sentences a store page says about a purchase it offers.

    What the hero holds or why it cannot have it, what it gets, why the purchase cannot be sent,
    and the purchase's confirmation.
    """

    def note(self, offered: Quote, others: bool) -> tuple[str, str] | None:
        """Return the glyph and sentence that the buy panel says first about a hero, if any.

        Args:
            offered: The purchase offered.
            others: Whether another hero of the account may have it.
        """
        sold, choice = offered.sold, offered.choice
        hero, item, held = choice.hero, sold.name, choice.held
        name = hero.persona.name
        another = [self.text("web.store.note.others")] if others else []
        match choice.unfit:
            case Unfit.FACTION:
                line = self.text(
                    "web.store.note.faction",
                    item=item,
                    factions=self.factions(sold.requirement.factions),
                    hero=name,
                    faction=self.factions(frozenset({hero.faction or ""})),
                )
                return "warn", " ".join([line, *another])
            case Unfit.CLASS:
                line = self.text(
                    "web.store.note.class",
                    item=item,
                    classes=self.classes(sold.requirement.classes),
                    hero=name,
                    hero_class=self.classes(frozenset({hero.hero_class or ""})),
                )
                return "warn", " ".join([line, *another])
            case Unfit.LEVEL:
                line = self.text(
                    "web.store.note.level",
                    hero=name,
                    level=hero.level,
                    item=item,
                    needed=sold.requirement.level,
                )
                return "warn", " ".join([line, *another])
            case Unfit.TAKEN:
                line = self.text("web.store.note.taken", item=item, hero=name)
                return "warn", " ".join([line, *another])
            case Unfit.ORDER:
                return "warn", self.text("web.store.note.order", item=item, hero=name)
        if held is None:
            return None
        if held.type == PERMANENT:
            said = [self.text("web.store.note.permanent", hero=name, item=item)]
            return "infinity", " ".join([*said, *another])
        if held.expires:
            left = self.count("web.store.days_left", self.days_left(held.expires))
            said = [
                self.text(
                    "web.store.note.rental",
                    hero=name,
                    item=item,
                    until=self.moment(held.expires),
                    left=left,
                )
            ]
            if any(price.ownership and price.ownership.type == PERMANENT for price in sold.prices):
                said.append(self.text("web.store.note.replaces"))
            return "clock", " ".join(said)
        uses = self.count("web.store.price.uses", held.uses or 0)
        return "uses", self.text("web.store.note.uses", hero=name, uses=uses, item=item)

    def summary(self, offered: Quote) -> str:
        """Return what the hero gets for the price.

        Args:
            offered: The purchase offered.
        """
        sold, held, after = offered.sold, offered.choice.held, offered.after
        name, item = offered.choice.hero.persona.name, sold.name
        gained = offered.price.ownership
        if gained.type == RENTED and after and after.expires:
            time = self.count("web.store.price.days", gained.days or 0)
            key = "web.store.sum.longer" if held and held.expires else "web.store.sum.rental"
            return self.text(key, hero=name, item=item, time=time, until=self.moment(after.expires))
        if gained.type == COUNTED:
            uses = self.count("web.store.price.uses", gained.uses or 0)
            if held and held.uses is not None and after and after.uses is not None:
                return self.text(
                    "web.store.sum.uses_added",
                    hero=name,
                    uses=uses,
                    item=item,
                    left=self.amount(held.uses),
                    added=self.amount(gained.uses or 0),
                    total=self.amount(after.uses),
                )
            return self.text("web.store.sum.uses", hero=name, uses=uses, item=item)
        key = "web.store.sum.keeps" if held else "web.store.sum.permanent"
        return self.text(key, hero=name, item=item)

    def why(self, offered: Quote) -> str:
        """Return why the purchase cannot be sent, naming both amounts; empty when it can.

        Args:
            offered: The purchase offered.
        """
        if not offered.short:
            return ""
        code = offered.price.currency
        values = {
            "balance": self.amount(offered.balance),
            "currency": self.currency(code),
            "price": self.amount(offered.price.price),
            "hero": offered.choice.hero.persona.name,
        }
        shared = self.game.currencies[code].scope is Scope.ACCOUNT
        said = [self.text("web.store.why.account" if shared else "web.store.why.hero", **values)]
        # A game may say how its currencies are earned, where no link to its Funds page follows.
        heroes = self.game.heroes
        if not (heroes and code == heroes.currency and "funds" in self.game.pages):
            with suppress(KeyError):
                said.append(
                    translate(f"games.{self.game.id}.currency.{code}.earned", self.language)
                )
        return " ".join(said)

    def change(self, offered: Quote) -> tuple[str, str]:
        """Return the confirmation's row of what changes, by its label: time, uses or contents.

        Args:
            offered: The purchase offered.
        """
        gained, after = offered.price.ownership, offered.after
        held = offered.choice.held
        if gained.type == COUNTED:
            uses = self.ownership(gained)
            if held and held.uses is not None and after and after.uses is not None:
                uses = self.text(
                    "web.store.confirm.uses",
                    uses=uses,
                    left=self.amount(held.uses),
                    total=self.amount(after.uses),
                )
            return self.text("web.store.row.uses"), uses
        if gained.type == RENTED and after and after.expires:
            until = self.text(
                "web.store.confirm.until", time=self.ownership(gained), day=self.day(after.expires)
            )
            return self.text("web.store.row.time"), until
        return self.text("web.store.row.time"), self.ownership(gained)

    def confirm_note(self, offered: Quote) -> str:
        """Return what the confirmation says under its rows.

        Args:
            offered: The purchase offered.
        """
        held, gained = offered.choice.held, offered.price.ownership
        kept = self.text("web.store.confirm.kept")
        if gained and gained.type == RENTED and held and held.expires:
            return " ".join(
                [self.text("web.store.confirm.adds", day=self.moment(held.expires)), kept]
            )
        return kept
