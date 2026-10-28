import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from openfesl.core.games import Game
from openfesl.core.hero_rules import HeroRules
from openfesl.core.personas import Persona
from openfesl.core.stats import PERSONA, Owner, StatStore, bucket_of, partition_of
from openfesl.core.wallets import Wallet
from openfesl.protocol.rank import Period


class NameCheck(StrEnum):
    """What a wanted hero name meets: the naming rule broken, a name in use, or a free one."""

    FORMAT = "format"
    TAKEN = "taken"
    FREE = "free"


class Refusal(StrEnum):
    """Why a hero was not created or renamed."""

    FORMAT = "format"
    TAKEN = "taken"
    NO_SLOT = "no-slot"
    SAME = "same"
    FUNDS = "funds"


class HeroRefused(Exception):
    """Raised for a hero that was not created or renamed; nothing was stored or spent."""

    def __init__(self, refusal: Refusal) -> None:
        """Say why the hero was not created.

        Args:
            refusal: Why.
        """
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class Look:
    """The choices a hero is created with.

    Attributes:
        faction: The faction's name in the game data.
        hero_class: The class's name in the game data.
        skin_tone: The skin tone, from 1.
        hair_colour: The hair colour, from 1.
        hair_style: The hair style's item number, 0 for none.
        facial_hair: The facial hair's item number, 0 for none.
    """

    faction: str
    hero_class: str
    skin_tone: int
    hair_colour: int
    hair_style: int
    facial_hair: int


@dataclass(frozen=True, slots=True)
class Hero:
    """One of an account's heroes, as the site shows it.

    Attributes:
        persona: The persona the hero plays as.
        created: When it was created.
        faction: Its faction's name, when the game knows it.
        hero_class: Its class's name, when the game knows it.
        level: Its level.
        xp: The XP it gathered within its level.
    """

    persona: Persona
    created: datetime
    faction: str | None
    hero_class: str | None
    level: int
    xp: int

    @property
    def playable(self) -> bool:
        """Whether the game can play the hero: it has the faction and class it was created with."""
        return self.faction is not None and self.hero_class is not None


@dataclass(frozen=True, slots=True)
class Roster:
    """An account's heroes in a game, oldest first, each in the slot of its place.

    Attributes:
        heroes: The heroes.
        slots: The heroes the account may have.
        default_id: The persona of the hero the game is started as, once there is one.
    """

    heroes: list[Hero]
    slots: int
    default_id: int | None

    @property
    def default(self) -> Hero | None:
        """The hero the game is started as, if the account has one."""
        return next((hero for hero in self.heroes if hero.persona.id == self.default_id), None)

    @property
    def free(self) -> int:
        """How many more heroes the account may create."""
        return max(self.slots - len(self.heroes), 0)


@dataclass(frozen=True, slots=True)
class HeroRecords:
    """What the store keeps of an account's heroes in a game.

    Attributes:
        personas: The heroes' personas, oldest first, with when each was created.
        default_id: The persona of the default hero, once there is one.
    """

    personas: list[tuple[Persona, datetime]]
    default_id: int | None


@dataclass(frozen=True, slots=True)
class NewHero:
    """A hero to store, with all that it starts with.

    Attributes:
        account_id: The account it belongs to.
        game: The game's data-file name.
        namespace: The namespace its name is unique in.
        name: Its name.
        free_slots: The slots every account has in the game, besides those it bought.
        partition: Where the game's stats are kept.
        stats: The stats it starts with, by key: a number and a text.
        items: The items it is granted, by Magma tag, with their uses.
    """

    account_id: int
    game: str
    namespace: str
    name: str
    free_slots: int
    partition: str
    stats: Mapping[str, tuple[float, str]]
    items: Mapping[str, int]


class HeroStore(Protocol):
    """Where heroes and the accounts' default heroes are kept."""

    async def records(self, account_id: int, game: str, namespace: str) -> HeroRecords:
        """Return what is kept of an account's heroes in a game.

        Args:
            account_id: The account.
            game: The game's data-file name.
            namespace: The game's persona namespace.
        """

    async def taken(self, namespace: str, name: str) -> bool:
        """Tell whether a persona has a name, ignoring capitals, those taken out of use too.

        Args:
            namespace: The namespace.
            name: The name.
        """

    async def create(self, hero: NewHero) -> Persona:
        """Store a hero with its stats and items; the account's first becomes its default.

        Everything is stored at once or nothing is.

        Args:
            hero: The hero.

        Raises:
            HeroRefused: When the account has no free slot or the name is taken, also by a
                request at the same time.
        """

    async def rename(self, persona: Persona, name: str, price: Wallet, amount: int) -> None:
        """Give a hero a new name and take its price from a wallet, both or neither.

        Args:
            persona: The hero's persona.
            name: The new name.
            price: The wallet the price is taken from.
            amount: The price.

        Raises:
            HeroRefused: When the name is taken, also by a request at the same time, or the
                wallet holds less than the price.
        """

    async def set_default(
        self, account_id: int, game: str, namespace: str, persona_id: int
    ) -> bool:
        """Make one of an account's heroes its default hero.

        Args:
            account_id: The account.
            game: The game's data-file name.
            namespace: The game's persona namespace.
            persona_id: The hero's persona.

        Returns:
            Whether the hero is one of the account's.
        """


class Heroes:
    """The heroes of games whose heroes are made on their site."""

    def __init__(self, store: HeroStore, stats: StatStore) -> None:
        """Keep heroes in a store, with their stats.

        Args:
            store: Where heroes are kept.
            stats: Where their stats are kept.
        """
        self._store = store
        self._stats = stats

    async def roster(self, game: Game, account_id: int) -> Roster:
        """Return an account's heroes in a game, with what the site shows about each.

        Args:
            game: The game, which has hero rules.
            account_id: The account.
        """
        rules = _rules(game)
        records = await self._store.records(account_id, game.id, game.namespace)
        owners = [Owner(PERSONA, persona.id) for persona, _ in records.personas]
        keys = [rules.stats.faction, rules.stats.hero_class, rules.stats.level, rules.stats.xp]
        bucket = bucket_of(Period.ALL_TIME, 0)
        values = await self._stats.values(partition_of(game), owners, keys, bucket)

        def stat(owner: Owner, key: str) -> int | None:
            found = values.get((owner, key))
            return int(found.value) if found else None

        heroes = []
        for (persona, created), owner in zip(records.personas, owners, strict=True):
            team = stat(owner, rules.stats.faction)
            kit = stat(owner, rules.stats.hero_class)
            heroes.append(
                Hero(
                    persona=persona,
                    created=created,
                    faction=next((k for k, f in rules.factions.items() if f.team == team), None),
                    hero_class=next((k for k, c in rules.classes.items() if c.kit == kit), None),
                    level=max(stat(owner, rules.stats.level) or 1, 1),
                    xp=stat(owner, rules.stats.xp) or 0,
                )
            )
        return Roster(heroes, rules.slots, records.default_id)

    async def check(self, game: Game, name: str) -> NameCheck:
        """Tell whether a name may be given to a new hero.

        Args:
            game: The game, which has hero rules.
            name: The wanted name.
        """
        if not re.fullmatch(_rules(game).name, name):
            return NameCheck.FORMAT
        if await self._store.taken(game.namespace, name):
            return NameCheck.TAKEN
        return NameCheck.FREE

    async def make_default(self, game: Game, account_id: int, persona_id: int) -> bool:
        """Make one of an account's heroes the one the game is started as.

        Args:
            game: The game, which has hero rules.
            account_id: The account.
            persona_id: The hero's persona.

        Returns:
            Whether the hero is one of the account's and one the game can play.
        """
        roster = await self.roster(game, account_id)
        if not any(hero.persona.id == persona_id and hero.playable for hero in roster.heroes):
            return False
        return await self._store.set_default(account_id, game.id, game.namespace, persona_id)

    async def rename(self, game: Game, persona: Persona, name: str) -> None:
        """Give one of an account's heroes a new name for the price the game's data sets.

        The game shows the new name from its next login.

        Args:
            game: The game, which has hero rules.
            persona: The hero's persona.
            name: The new name.

        Raises:
            HeroRefused: When the name breaks the rule, is the hero's own or is taken, or the
                account has too little to pay.
        """
        rules = _rules(game)
        if not re.fullmatch(rules.name, name):
            raise HeroRefused(Refusal.FORMAT)
        if name.casefold() == persona.name.casefold():
            raise HeroRefused(Refusal.SAME)
        wallet = Wallet(
            game.id,
            rules.currency,
            persona.account_id,
            None,
            game.currencies[rules.currency].allowance,
        )
        await self._store.rename(persona, name, wallet, rules.rename_price)

    async def create(self, game: Game, account_id: int, look: Look, name: str) -> Persona:
        """Create a hero with its starting stats and items.

        Args:
            game: The game, which has hero rules.
            account_id: The account it belongs to.
            look: Its choices, which match the game's rules.
            name: Its name.

        Raises:
            HeroRefused: When the name breaks the rule or is taken, or the account has no free
                slot.
        """
        rules = _rules(game)
        if not re.fullmatch(rules.name, name):
            raise HeroRefused(Refusal.FORMAT)
        keys = rules.stats
        loadout = rules.loadouts[look.faction][look.hero_class]
        numbers = {
            keys.faction: rules.factions[look.faction].team,
            keys.hero_class: rules.classes[look.hero_class].kit,
            keys.skin_tone: look.skin_tone,
            keys.hair_colour: look.hair_colour,
            keys.hair_style: look.hair_style,
            keys.facial_hair: look.facial_hair,
        }
        lists = {
            keys.appearance: loadout.appearance,
            keys.equipment: loadout.equipment,
            keys.emotes: loadout.emotes,
        }
        stats: dict[str, tuple[float, str]] = {
            key: (value, "") for key, value in rules.starting_stats.items()
        }
        stats |= {key: (float(value), "") for key, value in numbers.items()}
        stats |= {key: (0.0, ";".join(map(str, items))) for key, items in lists.items()}
        return await self._store.create(
            NewHero(
                account_id=account_id,
                game=game.id,
                namespace=game.namespace,
                name=name,
                free_slots=rules.slots,
                partition=partition_of(game),
                stats=stats,
                items=rules.starting_items,
            )
        )


def random_look(rules: HeroRules) -> Look:
    """Return a complete hero of random faction, class and look, as the creator opens with.

    Args:
        rules: The game's hero rules.
    """
    faction = secrets.choice(list(rules.factions))
    chosen = rules.factions[faction]
    return Look(
        faction=faction,
        hero_class=secrets.choice(list(rules.classes)),
        skin_tone=secrets.choice(rules.skin_order),
        hair_colour=secrets.choice(rules.hair_order),
        hair_style=secrets.choice([0, *chosen.hair_styles]),
        facial_hair=secrets.choice([0, *chosen.facial_hair]),
    )


def read_look(rules: HeroRules, chosen: Mapping[str, str]) -> Look | None:
    """Return the look a creator's form chose, or None when it is not one the rules allow.

    A hair style or facial hair of the other faction's list takes the same place in the chosen
    faction's list, as the form without scripts shows both lists.

    Args:
        rules: The game's hero rules.
        chosen: The form's fields: faction, class, skin_tone, hair_colour, hair_style and
            facial_hair.
    """
    faction = rules.factions.get(chosen.get("faction", ""))
    hero_class = chosen.get("class", "")
    numbers = [_number(chosen.get(field)) for field in _LOOK_FIELDS]
    if faction is None or hero_class not in rules.classes or None in numbers:
        return None
    skin, hair_colour, hair_style, facial_hair = (number or 0 for number in numbers)
    styles = _placed(
        hair_style, faction.hair_styles, [f.hair_styles for f in rules.factions.values()]
    )
    beards = _placed(
        facial_hair, faction.facial_hair, [f.facial_hair for f in rules.factions.values()]
    )
    if not (1 <= skin <= len(rules.skin_tones) and 1 <= hair_colour <= len(rules.hair_colours)):
        return None
    if styles is None or beards is None:
        return None
    return Look(chosen["faction"], hero_class, skin, hair_colour, styles, beards)


_LOOK_FIELDS = ("skin_tone", "hair_colour", "hair_style", "facial_hair")


def _placed(item: int, own: list[int], lists: list[list[int]]) -> int | None:
    # The item at the same place of the faction's own list, 0 staying none.
    if item == 0 or item in own:
        return item
    for other in lists:
        if item in other and other.index(item) < len(own):
            return own[other.index(item)]
    return None


def _number(text: str | None) -> int | None:
    return int(text) if text is not None and text.isascii() and text.isdecimal() else None


def _rules(game: Game) -> HeroRules:
    if game.heroes is None:
        raise ValueError(f"{game.id} makes no heroes on its site")
    return game.heroes
