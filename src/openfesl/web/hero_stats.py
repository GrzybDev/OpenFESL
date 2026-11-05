from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from openfesl.core.catalogues import Catalogue
from openfesl.core.games import Game
from openfesl.core.hero_rules import HeroRules
from openfesl.core.heroes import Hero
from openfesl.core.profiles import Breakdown
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


@dataclass(frozen=True, slots=True)
class Played:
    """A weapon, map, vehicle, heroic moment or mission in the lists of a hero's profile.

    Attributes:
        id: Its number.
        name: Its name, or its number where the server has no name for it.
        icon: The address of its icon, for an item where the page shows the game's art.
        stats: Its stats, each a label and a value as the page's language writes them.
    """

    id: int
    name: str
    icon: str | None
    stats: list[tuple[str, str]]


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
    time = duration(number_of(values, profile.time), language)
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


def lists(
    game: Game, values: Mapping[str, Value], catalogue: Catalogue, art: bool, language: str
) -> dict[str, Any]:
    """Return the lists of a hero's profile as the page's values: lists, moments and missions.

    The lists are the weapons the hero used and those that killed it, its maps and its vehicles,
    as the 2012 site listed them; then come its heroic moments and its current and completed
    missions.

    Args:
        game: The game, which has profile rules.
        values: The hero's stats by key.
        catalogue: Names the items, maps and moments, once imported.
        art: Whether the page shows the game's art to its viewer.
        language: The page's language.
    """
    rules = game.heroes
    assert rules is not None and rules.profile is not None
    profile = rules.profile
    numbers = {key: found.value for key, found in values.items()}

    def text(key: str, **filled: object) -> str:
        return translate(key, language) % filled if filled else translate(key, language)

    def count(value: float) -> str:
        return number(value, language)

    def item(item_id: int, stats: list[tuple[str, str]]) -> Played:
        name = catalogue.name(item_id) or text("web.hero.item", number=item_id)
        return Played(item_id, name, icon_url(game.id, catalogue, item_id, art), stats)

    weapons = []
    for item_id, stats in sorted(
        profile.weapons.read(numbers), key=lambda found: -found[1]["time"]
    ):
        shown = [
            (text("web.hero.stat.time"), duration(stats["time"], language)),
            (text("web.hero.stat.kills"), count(stats["kills"])),
            (text("web.hero.stat.deaths_using"), count(stats["deaths"])),
        ]
        if stats["shots"]:
            accuracy = number(stats["hits"] / stats["shots"], language, 3)
            shown.append((text("web.hero.stat.accuracy"), accuracy))
        weapons.append(item(item_id, shown))
    foes = [
        item(item_id, [(text("web.hero.stat.deaths"), count(stats["deaths"]))])
        for item_id, stats in sorted(
            profile.foes.read(numbers), key=lambda found: -found[1]["deaths"]
        )
    ]
    maps = []
    for map_id, stats in sorted(profile.maps.read(numbers), key=lambda found: -found[1]["time"]):
        named = profile.maps.names.get(map_id)
        losses = stats["losses"]
        ratio = stats["wins"] / losses if losses else stats["wins"]
        name = (catalogue.text(named) if named else None) or (
            text("web.hero.other_maps")
            if map_id == profile.maps.other
            else text("web.hero.map", number=map_id)
        )
        shown = [
            (text("web.hero.stat.time"), duration(stats["time"], language)),
            (text("web.hero.stat.wins"), count(stats["wins"])),
            (text("web.hero.stat.losses"), count(losses)),
            (text("web.hero.stat.ratio"), number(ratio, language, 2)),
        ]
        maps.append(Played(map_id, name, None, shown))
    vehicles = []
    for vehicle, stats in profile.vehicles.read(numbers):
        word = profile.vehicles.names.get(vehicle)
        # The guns that servers count too, which the original profiles did not list.
        if word is None:
            continue
        named = f"games.{game.id}.vehicle.{word}"
        name = text(named)
        shown = [
            (text("web.hero.stat.time"), duration(stats["time"], language)),
            (text("web.hero.stat.kills"), count(stats["kills"])),
            (text(f"{named}.collisions"), count(stats["collisions"])),
            (text("web.hero.stat.destroyed"), count(stats["destroyed"])),
            (text("web.hero.stat.deaths_by"), count(stats["deaths"])),
            (text("web.hero.stat.deaths_inside"), count(stats["inside"])),
        ]
        vehicles.append(Played(vehicle, name, None, shown))
    moments = []
    for moment, stats in sorted(
        profile.moments.read(numbers), key=lambda found: -found[1]["count"]
    ):
        named = profile.moments.names.get(moment)
        name = (catalogue.text(named) if named else None) or text("web.hero.moment", number=moment)
        if stats["count"] > 0:
            moments.append(Played(moment, name, None, [("", count(stats["count"]))]))
    missions = []
    for key in profile.current:
        if mission := int(numbers.get(key, 0)):
            name = catalogue.name(mission) or text("web.hero.mission", number=mission)
            icon = icon_url(game.id, catalogue, mission, art)
            missions.append(Played(mission, name, icon, [("", text("web.hero.current"))]))
    done = Breakdown(keys={"count": rules.stats.completed})
    for mission, stats in done.read(numbers):
        if times := int(stats["count"]):
            name = catalogue.name(mission) or text("web.hero.mission", number=mission)
            icon = icon_url(game.id, catalogue, mission, art)
            state = text("web.hero.completed.times", count=count(times))
            state = text("web.hero.completed") if times == 1 else state
            missions.append(Played(mission, name, icon, [("", state)]))
    lists = {"weapons": weapons, "foes": foes, "maps": maps, "vehicles": vehicles}
    return {"lists": lists, "moments": moments, "missions": missions}


def duration(seconds: float, language: str) -> str:
    """Return a time in hours and minutes as a language writes it.

    Args:
        seconds: The time in seconds.
        language: The language's code.
    """
    hours, minutes = number(int(seconds) // 3600, language), int(seconds) // 60 % 60
    return translate("web.hero.time", language) % {"hours": hours, "minutes": minutes}
