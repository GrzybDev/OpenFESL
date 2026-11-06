from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from openfesl.core.account_rules import account_rules
from openfesl.core.admins import Admin
from openfesl.core.catalogues import Catalogues
from openfesl.core.chests import key_tag
from openfesl.core.games import Game
from openfesl.core.heroes import Hero, Heroes
from openfesl.core.inventory import Held, Ownership
from openfesl.core.policies import SettingStore
from openfesl.core.reward_rules import Gift
from openfesl.core.rewards import Rewards
from openfesl.core.store_items import requirement
from openfesl.core.wallets import Wallet

# The most personal codes one batch makes.
MOST_BATCH = 1000


class CodeRefusal(StrEnum):
    """Why a code gives an account nothing."""

    UNKNOWN = "unknown"
    """No code of the game is written so, or it is disabled, which players are not told."""
    EXPIRED = "expired"
    USED_UP = "used_up"
    """As many accounts as it allows redeemed it."""
    REDEEMED = "redeemed"
    """The account redeemed it before, which each account may once."""
    NO_HERO = "no_hero"
    """Its items go to a hero, and the account has none."""
    NO_FIT = "no_fit"
    """No hero of the account may have all of its items."""
    BALANCE = "balance"
    """Its Funds would take the account's wallet past the most a wallet holds."""


class CodeState(StrEnum):
    """Whether a code gives its gift now, as the admin panel names it."""

    ENABLED = "enabled"
    USED_UP = "used_up"
    """As many accounts as it allows redeemed it."""
    EXPIRED = "expired"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class GiftCode:
    """A code of a game's site that gives a gift, or a batch of personal codes made alike.

    The codes of a batch share their gift, labels, end and state, and each works for one account.

    Attributes:
        id: The number of its key, or of the batch's first key, which names the batch.
        code: The code; the first code of a batch.
        game: The id of the game whose site redeems it.
        gift: What it gives: items to one hero, Funds and keys to the account.
        labels: What players see it called, by language.
        max_uses: How many accounts may redeem it; None for any number, one each for a batch's.
        expires: When it can no longer be redeemed; never when None.
        enabled: Whether it may be redeemed.
        uses: How many accounts redeemed it, or a batch's codes.
        codes: Its codes, in the order they were made; the one code where it is not a whole batch.
        batch: Whether it is a batch.
    """

    id: int
    code: str
    game: str
    gift: Gift
    labels: Mapping[str, str]
    max_uses: int | None
    expires: datetime | None
    enabled: bool
    uses: int
    codes: tuple[str, ...]
    batch: bool = False

    def state(self, now: datetime) -> CodeState:
        """Return whether it gives its gift at a moment, a disabled one above all.

        Args:
            now: The moment.
        """
        if not self.enabled:
            return CodeState.DISABLED
        if self.expires is not None and self.expires <= now:
            return CodeState.EXPIRED
        most = self.count if self.batch else self.max_uses
        return CodeState.USED_UP if most is not None and self.uses >= most else CodeState.ENABLED

    def label(self, language: str) -> str:
        """Return what players see it called in a language, else in English.

        Args:
            language: The language's code.
        """
        return self.labels.get(language) or self.labels.get("en", "")

    @property
    def count(self) -> int:
        """How many codes it has; 1 for a code of its own."""
        return len(self.codes)


@dataclass(frozen=True, slots=True)
class Made:
    """When a code or batch was made, by whom and why.

    Attributes:
        at: When.
        by: The admin's name; none for the command line.
        note: Why, as the admin wrote it.
    """

    at: datetime
    by: str | None
    note: str


@dataclass(frozen=True, slots=True)
class CodeTerms:
    """What a code gives and on what terms, as the admin makes or changes it.

    Attributes:
        gift: What it gives.
        labels: What players see it called, by language, English among them.
        max_uses: How many accounts may redeem it; None for any number.
        expires: When it can no longer be redeemed; never when None.
    """

    gift: Gift
    labels: Mapping[str, str]
    max_uses: int | None
    expires: datetime | None


@dataclass(frozen=True, slots=True)
class CodeChange:
    """Who changes a code and why, with what the audit log keeps of it.

    Attributes:
        admin: The admin.
        note: Why, as they wrote it.
        details: What changed, as the audit log keeps it besides the note.
    """

    admin: Admin
    note: str
    details: Mapping[str, Any] = field(default_factory=dict)


class GiftCodeStore(Protocol):
    """Where the codes that give gifts are kept."""

    async def gift_codes(self, game: str) -> list[GiftCode]:
        """Return a game's codes, a batch as one, the newest first.

        Args:
            game: The game's id.
        """

    async def gift_code(self, code: str, whole: bool = True) -> GiftCode | None:
        """Return a code, or the batch it belongs to, whether it gives anything or not.

        Args:
            code: The code, in any case.
            whole: Whether a code of a batch stands for the whole batch; otherwise for itself.
        """

    async def redemption(self, code: str, account_id: int) -> Redemption | None:
        """Return an account's redemption of a code, if it redeemed it.

        Args:
            code: The code as stored.
            account_id: The account.
        """

    async def redemptions(self, entry: GiftCode) -> list[Redemption]:
        """Return the redemptions of a code or of a batch's codes, the newest first.

        Args:
            entry: The code or batch.
        """

    async def redeemed_by(self, game: str, account_id: int) -> list[Redemption]:
        """Return the codes of a game that an account redeemed, the newest first.

        Args:
            game: The game's id.
            account_id: The account.
        """

    async def redeem(self, grant: CodeGrant) -> Redemption:
        """Redeem a code for an account and give what it gives, all of it or none.

        The code is checked again under its lock, so that accounts redeeming it at once count
        its uses in turn and an account redeems it once.

        Args:
            grant: What the redemption gives.

        Raises:
            CodeRefused: When the code is unknown or disabled, ended or used up by now, the
                account redeemed it already, also at the same time, or its Funds would pass the
                most a wallet holds.
        """

    async def made(self, code: str) -> Made | None:
        """Return when a code or batch was made, as the admin's actions record it.

        Args:
            code: The code, or a batch's first code, as stored.
        """

    async def make(
        self, game: str, codes: Sequence[str], terms: CodeTerms, change: CodeChange
    ) -> bool:
        """Make a code, or a batch of personal codes, and record it among the admin's actions.

        Args:
            game: The game's id.
            codes: The code, or the batch's codes, each new.
            terms: What each gives and on what terms; a batch's codes work for one account each.
            change: Who makes them and why.

        Returns:
            Whether they were made, which they are not when a key has one of the codes already.
        """

    async def change(self, entry: GiftCode, terms: CodeTerms, change: CodeChange) -> None:
        """Change what a code or every code of a batch gives from now on, and record it.

        Args:
            entry: The code or the batch.
            terms: What it gives and on what terms from now on.
            change: Who changes it and why, with what changed.
        """

    async def switch(self, entry: GiftCode, enabled: bool, change: CodeChange) -> bool:
        """Enable or disable a code or every code of a batch, and record it.

        Args:
            entry: The code or the batch.
            enabled: Whether it may be redeemed from now on.
            change: Who switches it and why.

        Returns:
            Whether it changed, which it does not when it was so already.
        """


@dataclass(frozen=True, slots=True)
class GivenItem:
    """An item that a redemption gave a hero.

    Attributes:
        item_id: The item's number.
        ownership: How it was given.
        held: How the hero held it after; none for an item it owned for good.
        fallback: Whether it is the rewards' fallback, given for an item the hero owned for good.
    """

    item_id: int
    ownership: Ownership
    held: Held | None
    fallback: bool = False


@dataclass(frozen=True, slots=True)
class Redemption:
    """An account's redemption of a code, with what it gave.

    Attributes:
        code: The code.
        labels: What players see the code called, by language.
        account_id: The account.
        account: The account's login name.
        hero: The name of the hero its items went to; none for none, or a hero since gone.
        at: When it was redeemed.
        items: The items the hero got.
        funds: The Funds the account got, and its balance after; none for none.
        keys: The treasure chest keys the account got, and how many it held after; none for none.
    """

    code: str
    labels: Mapping[str, str]
    account_id: int
    account: str
    hero: str | None
    at: datetime
    items: tuple[GivenItem, ...]
    funds: tuple[int, int] | None
    keys: tuple[int, int] | None


@dataclass(frozen=True, slots=True)
class CodeGrant:
    """What redeeming a code gives at once, all of it or none.

    Attributes:
        code: The code as stored.
        game: The id of the game whose code it is.
        account_id: The account.
        hero_id: The persona of the hero its items go to; none for a code without items.
        items: The items the hero gets, each with its ownership.
        fallback: What the hero gets instead of an item it owns for good.
        funds: The account's wallet the Funds go to, and how many; none for none.
        keys: The tag of the account's keys, and how many it gets; none for none.
    """

    code: str
    game: str
    account_id: int
    hero_id: int | None
    items: tuple[tuple[int, Ownership], ...]
    fallback: tuple[int, Ownership]
    funds: tuple[Wallet, int] | None
    keys: tuple[str, int] | None


class CodeRefused(Exception):
    """Raised for a code that gave nothing; nothing was given."""

    def __init__(self, refusal: CodeRefusal) -> None:
        """Say why the code gave nothing.

        Args:
            refusal: Why.
        """
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class Look:
    """A code as an account sees it before redeeming it.

    Attributes:
        code: The code in its own form, or as typed where it has none.
        entry: The code; none for no code of the game, or a disabled one.
        refusal: Why it gives the account nothing; none when it may redeem it.
        earlier: The account's redemption of it before, for a code it redeemed already.
        heroes: The account's heroes, oldest first.
        fitting: The personas of the heroes that may have every item it gives.
        fallback: What a hero gets instead of an item it owns for good, as the rewards give it.
    """

    code: str
    entry: GiftCode | None
    refusal: CodeRefusal | None
    earlier: Redemption | None = None
    heroes: tuple[Hero, ...] = ()
    fitting: tuple[int, ...] = ()
    fallback: tuple[int, Ownership] | None = None


class GiftCodes:
    """The codes that a game's site redeems for gifts: items to a hero, Funds and keys."""

    def __init__(
        self,
        store: GiftCodeStore,
        rewards: Rewards,
        heroes: Heroes,
        catalogues: Catalogues,
        settings: SettingStore,
    ) -> None:
        """Redeem codes from a store, giving what the rewards give.

        Args:
            store: Where the codes are kept, and redeemed.
            rewards: Tell the Funds and the fallback that codes give, as the rewards do.
            heroes: The accounts' heroes, with their factions, classes and levels.
            catalogues: The games' imported catalogues, which tell what fits a hero.
            settings: The instance's settings, which set the wallets' allowance.
        """
        self._store = store
        self._rewards = rewards
        self._heroes = heroes
        self._catalogues = catalogues
        self._settings = settings

    async def look(self, game: Game, account_id: int, typed: str) -> Look:
        """Return a code as an account sees it: what it gives and to which heroes, or why not.

        Args:
            game: The game, whose site redeems codes.
            account_id: The account.
            typed: The code as typed or linked, in any case, with or without spaces and dashes.
        """
        assert game.codes is not None
        code = game.codes.read(typed)
        entry = await self._store.gift_code(code, whole=False) if code else None
        if entry is None or entry.game != game.id or not entry.enabled:
            return Look(code or typed.strip(), None, CodeRefusal.UNKNOWN)
        assert code is not None
        roster = (await self._heroes.roster(game, account_id)).heroes
        contents = self._catalogues.contents(game)
        known = [contents.items[item] for item, _ in entry.gift.items if item in contents.items]
        fitting = tuple(
            hero.persona.id
            for hero in roster
            if hero.playable and all(requirement(game, item).unfit(hero) is None for item in known)
        )
        rules = await self._rewards.rules(game)
        fallback = rules.fallback if rules else None
        found = Look(code, entry, None, None, tuple(roster), fitting, fallback)
        if entry.expires is not None and entry.expires <= datetime.now(UTC):
            return _refused(found, CodeRefusal.EXPIRED)
        if earlier := await self._store.redemption(code, account_id):
            return _refused(found, CodeRefusal.REDEEMED, earlier)
        if entry.max_uses is not None and entry.uses >= entry.max_uses:
            return _refused(found, CodeRefusal.USED_UP)
        if entry.gift.items and not roster:
            return _refused(found, CodeRefusal.NO_HERO)
        if entry.gift.items and not fitting:
            return _refused(found, CodeRefusal.NO_FIT)
        return found

    async def redeem(
        self, game: Game, account_id: int, typed: str, hero_id: int | None
    ) -> Redemption:
        """Redeem a code for an account, its items going to one of its heroes.

        Everything is checked again at the moment of redeeming, and the gift is given at once:
        items to the hero, Funds and keys to the account.

        Args:
            game: The game, whose site redeems codes.
            account_id: The account.
            typed: The code as typed or linked.
            hero_id: The persona of the hero its items go to; none for a code without items.

        Raises:
            CodeRefused: When the code gives the account nothing, or the hero may not have its
                items.
        """
        found = await self.look(game, account_id, typed)
        if found.refusal is not None or found.entry is None:
            raise CodeRefused(found.refusal or CodeRefusal.UNKNOWN)
        gift = found.entry.gift
        if gift.items and hero_id not in found.fitting:
            raise CodeRefused(CodeRefusal.NO_FIT)
        rules = await self._rewards.rules(game)
        assert rules is not None
        funds = None
        if gift.funds:
            currencies = (await account_rules(game, self._settings)).currencies
            allowance = currencies[rules.currency].allowance
            funds = Wallet(game.id, rules.currency, account_id, None, allowance), gift.funds
        keys = (key_tag(game.chests), gift.keys) if gift.keys and game.chests else None
        grant = CodeGrant(
            found.code,
            game.id,
            account_id,
            hero_id if gift.items else None,
            gift.items,
            rules.fallback,
            funds,
            keys,
        )
        return await self._store.redeem(grant)


def _refused(found: Look, refusal: CodeRefusal, earlier: Redemption | None = None) -> Look:
    return replace(found, refusal=refusal, earlier=earlier)
