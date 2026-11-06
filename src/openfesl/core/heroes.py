import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from openfesl.core.account_rules import account_rules
from openfesl.core.games import Game
from openfesl.core.hero_rules import HeroRules
from openfesl.core.personas import Persona
from openfesl.core.policies import SettingStore
from openfesl.core.stats import PERSONA, Owner, StatStore, bucket_of, partition_of
from openfesl.core.wallets import Wallet, account_wallet
from openfesl.protocol.rank import Period


class NameCheck(StrEnum):
    """What a wanted hero name meets: the naming rule broken, a name in use, or a free one."""

    FORMAT = "format"
    TAKEN = "taken"
    FREE = "free"


class Refusal(StrEnum):
    """Why a hero was not created or renamed, or a hero slot not bought."""

    FORMAT = "format"
    TAKEN = "taken"
    NO_SLOT = "no-slot"
    SAME = "same"
    FUNDS = "funds"


class HeroRefused(Exception):
    """Raised for a refused hero creation, rename or slot purchase; nothing was stored or spent."""

    def __init__(self, refusal: Refusal) -> None:
        """Say why the request was refused.

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
        look: The choices it was created with; none for a hero made before the creator.
        portrait: The version of the portrait that its game rendered of it and uploaded last;
            none before the first.
        disabled: Whether the admin took it out of play, which other accounts are not shown.
    """

    persona: Persona
    created: datetime
    faction: str | None
    hero_class: str | None
    level: int
    xp: int
    look: Look | None
    portrait: int | None
    disabled: bool = False

    @property
    def playable(self) -> bool:
        """Whether the game may play the hero: it has its faction and class and is not disabled."""
        return self.faction is not None and self.hero_class is not None and not self.disabled


@dataclass(frozen=True, slots=True)
class Roster:
    """An account's heroes in a game, oldest first, each in the slot of its place.

    Attributes:
        heroes: The heroes.
        slots: The heroes the account may have.
        default_id: The persona of the hero the game is started as, once there is one.
        bought: The slots the account bought on top of the game's free ones.
        granted: The slots the admin granted the account on top of those, free.
    """

    heroes: list[Hero]
    slots: int
    default_id: int | None
    bought: int = 0
    granted: int = 0

    @property
    def default(self) -> Hero | None:
        """The hero the game is started as, if the account has one."""
        return next((hero for hero in self.heroes if hero.persona.id == self.default_id), None)

    @property
    def free(self) -> int:
        """How many more heroes the account may create."""
        return max(self.slots - len(self.heroes), 0)

    @property
    def playing(self) -> Hero | None:
        """The hero the game is started to play as: the default one, or else the oldest playable."""
        usable = [hero for hero in self.heroes if hero.playable]
        return self.default if self.default in usable else next(iter(usable), None)


@dataclass(frozen=True, slots=True)
class HeroRecords:
    """What the store keeps of an account's heroes in a game.

    Attributes:
        personas: The heroes' personas, oldest first, with when each was created.
        default_id: The persona of the default hero, once there is one.
        bought: The slots the account bought on top of the game's free ones.
        portraits: The version of each hero's last portrait from its game, by persona.
        disabled: The personas of the heroes that the admin took out of play.
        granted: The slots the admin granted the account on top of those, free.
    """

    personas: list[tuple[Persona, datetime]]
    default_id: int | None
    bought: int
    portraits: Mapping[int, int]
    disabled: frozenset[int] = frozenset()
    granted: int = 0


@dataclass(frozen=True, slots=True)
class NewHero:
    """A hero to store, with all that it starts with.

    Attributes:
        account_id: The account it belongs to.
        game: The game's data-file name.
        namespace: The namespace its name is unique in.
        name: Its name.
        free_slots: The slots every account has in the game, to which those it bought and was
            granted add.
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

    async def defaults(self, account_ids: list[int], game: str, namespace: str) -> HeroRecords:
        """Return what is kept of accounts' default heroes in a game that other accounts see.

        Heroes that the admin took out of play are left out, and no slots are counted.

        Args:
            account_ids: The accounts.
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

    async def buy_slot(self, account_id: int, game: str, price: Wallet, amount: int) -> int:
        """Add a hero slot to an account and take its price from a wallet, both or neither.

        Args:
            account_id: The account.
            game: The game's data-file name.
            price: The wallet the price is taken from.
            amount: The price.

        Returns:
            The slots the account bought in all.

        Raises:
            HeroRefused: When the wallet holds less than the price, also after a spending at
                the same time.
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

    def __init__(self, store: HeroStore, stats: StatStore, settings: SettingStore) -> None:
        """Keep heroes in a store, with their stats.

        Args:
            store: Where heroes are kept.
            stats: Where their stats are kept.
            settings: The instance's settings, which override the prices of the games' data.
        """
        self._store = store
        self._stats = stats
        self._settings = settings

    async def rules(self, game: Game) -> HeroRules:
        """Return how a game's heroes are made and kept, at the prices of this moment.

        Those are the prices the instance sets, less the sales that run.

        Args:
            game: The game, which has hero rules.
        """
        return _rules(await account_rules(game, self._settings)).at(datetime.now(UTC))

    async def roster(self, game: Game, account_id: int) -> Roster:
        """Return an account's heroes in a game, with what the site shows about each.

        Args:
            game: The game, which has hero rules.
            account_id: The account.
        """
        records = await self._store.records(account_id, game.id, game.namespace)
        heroes = await self._heroes(game, records)
        slots = _rules(game).slots + records.bought + records.granted
        return Roster(heroes, slots, records.default_id, records.bought, records.granted)

    async def defaults(self, game: Game, account_ids: list[int]) -> dict[int, Hero]:
        """Return the default heroes of accounts in a game that other accounts see, by account.

        Args:
            game: The game, which has hero rules.
            account_ids: The accounts.
        """
        records = await self._store.defaults(account_ids, game.id, game.namespace)
        return {hero.persona.account_id: hero for hero in await self._heroes(game, records)}

    async def _heroes(self, game: Game, records: HeroRecords) -> list[Hero]:
        # The heroes of the kept personas, with the stats of all of them read at once.
        rules = _rules(game)
        owners = [Owner(PERSONA, persona.id) for persona, _ in records.personas]
        stats = rules.stats
        chosen = [stats.faction, stats.hero_class, stats.skin_tone, stats.hair_colour]
        chosen += [stats.hair_style, stats.facial_hair]
        keys = [*chosen, stats.level, stats.xp]
        bucket = bucket_of(Period.ALL_TIME, 0)
        values = await self._stats.values(partition_of(game), owners, keys, bucket)

        def stat(owner: Owner, key: str) -> int | None:
            found = values.get((owner, key))
            return int(found.value) if found else None

        heroes = []
        for (persona, created), owner in zip(records.personas, owners, strict=True):
            team, kit, *look = (stat(owner, key) for key in chosen)
            faction, hero_class = rules.faction_of(team), rules.class_of(kit)
            heroes.append(
                Hero(
                    persona=persona,
                    created=created,
                    faction=faction,
                    hero_class=hero_class,
                    level=max(stat(owner, stats.level) or 1, 1),
                    xp=stat(owner, stats.xp) or 0,
                    look=_created_look(faction, hero_class, look),
                    portrait=records.portraits.get(persona.id),
                    disabled=persona.id in records.disabled,
                )
            )
        return heroes

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

    async def buy_slot(self, game: Game, account_id: int, price: int) -> int:
        """Add a hero slot to an account at a price.

        Args:
            game: The game, which has hero rules.
            account_id: The account.
            price: What it costs, as the page that sold it was shown.

        Returns:
            The number of the new slot.

        Raises:
            HeroRefused: When the account has too little to pay.
        """
        game = await account_rules(game, self._settings)
        rules = _rules(game)
        wallet = account_wallet(game, account_id, rules.currency)
        bought = await self._store.buy_slot(account_id, game.id, wallet, price)
        return rules.slots + bought

    async def rename(self, game: Game, persona: Persona, name: str, price: int) -> None:
        """Give one of an account's heroes a new name at a price.

        The game shows the new name from its next login.

        Args:
            game: The game, which has hero rules.
            persona: The hero's persona.
            name: The new name.
            price: What it costs, as the page that sold it was shown.

        Raises:
            HeroRefused: When the name breaks the rule, is the hero's own or is taken, or the
                account has too little to pay.
        """
        game = await account_rules(game, self._settings)
        rules = _rules(game)
        if not re.fullmatch(rules.name, name):
            raise HeroRefused(Refusal.FORMAT)
        if name.casefold() == persona.name.casefold():
            raise HeroRefused(Refusal.SAME)
        wallet = account_wallet(game, persona.account_id, rules.currency)
        await self._store.rename(persona, name, wallet, price)

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

    async def worn(self, game: Game, persona_id: int) -> set[int]:
        """Return the numbers of the items a hero wears, has on its equipment bar or as emotes.

        Args:
            game: The game, which has hero rules.
            persona_id: The hero's persona.
        """
        rules = _rules(game)
        owner = Owner(PERSONA, persona_id)
        keys = [rules.stats.appearance, rules.stats.equipment, rules.stats.emotes]
        bucket = bucket_of(Period.ALL_TIME, 0)
        values = await self._stats.values(partition_of(game), [owner], keys, bucket)
        worn: set[int] = set()
        for key in keys:
            found, stat = values.get((owner, key)), rules.written_stat(key)
            if found and stat:
                worn.update(stat.numbers(found.value, found.text) or ())
        return worn - {0}


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


def _created_look(
    faction: str | None, hero_class: str | None, chosen: list[int | None]
) -> Look | None:
    # A hero made before the creator lacks the stats of its choices.
    if faction is None or hero_class is None or None in chosen:
        return None
    skin, hair_colour, hair_style, facial_hair = (number or 0 for number in chosen)
    return Look(faction, hero_class, skin, hair_colour, hair_style, facial_hair)


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
