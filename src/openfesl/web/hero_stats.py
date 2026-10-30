from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from openfesl.core.catalogues import Catalogue
from openfesl.core.games import Game
from openfesl.core.hero_rules import HeroRules
from openfesl.core.heroes import Hero
from openfesl.core.stats import Value
from openfesl.core.texts import translate
from openfesl.web.catalogue_files import icon_url
from openfesl.web.pages import number
from openfesl.web.store_front import kind_glyphs


@dataclass(frozen=True, slots=True)
class Fact:
    """A row of the stats that a hero's profile lists.

    Attributes:
        name: What it shows, the end of its label's key.
        value: The value as the page's language writes it, or the day it names; none for a stat
            of rounds while the hero has played none.
    """

    name: str
    value: str | datetime | None


@dataclass(frozen=True, slots=True)
class Equipped:
    """An item that a hero wears or has on its equipment bar.

    Attributes:
        id: The item's number.
        name: Its name; none without a catalogue, or for a withheld item.
        icon: The address of its icon, where the page shows the game's art.
        glyph: The glyph of its kind, drawn where its icon is not; none for an item the
            catalogue does not know.
        level: Its level, for an ability of several levels.
    """

    id: int
    name: str | None
    icon: str | None
    glyph: str | None
    level: int | None


def number_of(values: Mapping[str, Value], key: str) -> float:
    """Return the number that a stat holds, 0 for one never stored.

    Args:
        values: An owner's stats by key.
        key: The stat's key.
    """
    found = values.get(key)
    return found.value if found else 0.0


def equipped(
    game: Game, values: Mapping[str, Value], catalogue: Catalogue, art: bool
) -> list[Equipped]:
    """Return what a hero wears, then its equipment bar, as its game saved them last.

    The base items that every hero of a faction holds, its body under the clothes, are left out,
    as the 2012 site left them out.

    Args:
        game: The game, which has hero rules.
        values: The hero's stats by key.
        catalogue: Names the items, with their icons, once imported.
        art: Whether the page shows the game's art to its viewer.
    """
    rules = game.heroes
    assert rules is not None
    # Both factions' base items: a hero's stats hold only its own faction's, and a hero made
    # before the creator has no faction to choose by.
    base = {item for faction in rules.factions.values() for item in faction.base_items}
    glyphs = kind_glyphs(game)
    found = []
    for key in (rules.stats.appearance, rules.stats.equipment):
        stat, value = rules.written_stat(key), values.get(key)
        numbers = stat.numbers(value.value, value.text) if stat and value else None
        for item_id in numbers or []:
            if item_id == 0 or item_id in base:
                continue
            item = catalogue.item(item_id)
            levels = item.levels if item else ()
            found.append(
                Equipped(
                    id=item_id,
                    name=None if item and item.withheld else catalogue.name(item_id),
                    icon=icon_url(game.id, catalogue, item_id, art),
                    glyph=glyphs.get(item.kind) if item else None,
                    level=levels.index(item_id) + 1 if len(levels) > 1 else None,
                )
            )
    return found


def facts(
    rules: HeroRules,
    hero: Hero,
    values: Mapping[str, Value],
    catalogue: Catalogue,
    language: str,
) -> list[Fact]:
    """Return the rows of a hero's Soldier Info in the 2012 site's order.

    The stats of rounds stay empty until the hero's first round.

    Args:
        rules: The game's hero rules, which have profile rules.
        hero: The hero.
        values: The hero's stats by key.
        catalogue: Tells the XP of each level, once imported.
        language: The page's language.
    """
    profile = rules.profile
    assert profile is not None
    played = number_of(values, profile.time) > 0

    def count(key: str) -> str | None:
        return number(number_of(values, key), language) if played else None

    def pair(first: str, second: str) -> str | None:
        shown = {"first": count(first), "second": count(second)}
        return translate("web.hero.pair", language) % shown if played else None

    def ratio(over: str, under: str, places: int, alone: bool = True) -> str | None:
        # Without deaths or losses the kills or wins stand alone, as leaderboards rank them; no
        # shot fired hits none.
        above, below = number_of(values, over), number_of(values, under)
        shown = above / below if below else (above if alone else 0.0)
        return number(shown, language, places) if played else None

    needed = catalogue.xp_to_next(hero.level)
    xp = number(hero.xp, language)
    if needed:
        xp = translate("web.hero.of", language) % {"xp": xp, "needed": number(needed, language)}
    seconds = int(number_of(values, profile.time))
    hours, minutes = number(seconds // 3600, language), seconds // 60 % 60
    time = translate("web.hero.time", language) % {"hours": hours, "minutes": minutes}
    return [
        Fact("rating", count(profile.rating)),
        Fact("kills_deaths", pair(profile.kills, profile.deaths)),
        Fact("kills_ratio", ratio(profile.kills, profile.deaths, 2)),
        Fact("accuracy", ratio(profile.hits, profile.shots, 3, alone=False)),
        Fact("suicides", count(profile.suicides)),
        Fact("experience", xp),
        Fact("score", count(profile.score)),
        Fact("wins_losses", pair(profile.wins, profile.losses)),
        Fact("wins_ratio", ratio(profile.wins, profile.losses, 2)),
        Fact("captures", count(profile.captures)),
        Fact("rocket_captures", count(profile.rocket_captures)),
        Fact("time", time if played else None),
        Fact("enlisted", hero.created),
    ]
