from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from openfesl.core.catalogue import CatalogueRules, Element
from openfesl.core.catalogues import Catalogues, Contents
from openfesl.core.games import Game
from openfesl.core.hero_models import (
    Camera,
    Colour,
    HeroModels,
    Rig,
    Template,
    Tint,
    Tints,
    Vector,
)
from openfesl.core.heroes import Look


@dataclass(frozen=True, slots=True)
class Layer:
    """A texture painted into a hero's texture.

    Attributes:
        texture: Its name among the catalogue's textures.
        at: Where its top left lands, in pixels from the top left of the hero's texture.
        tint: The colour that it is blended with by overlay before it is painted, if any.
    """

    texture: str
    at: tuple[int, int]
    tint: Colour | None


@dataclass(frozen=True, slots=True)
class Figure:
    """A hero as its game's menu draws it, from the models that the game's catalogue keeps.

    Models and textures are named as the catalogue's import keeps them.

    Attributes:
        revision: The import that keeps the models and textures.
        rig: The skeleton of the hero's faction, with its clips.
        parts: The meshes that the hero wears, by name among the models; each binds to the
            skeleton by its nodes' names.
        layers: The textures painted into the hero's texture, in order.
        atlas: The width and height of the hero's texture.
        warp: The name among the textures of the ramp that shades the models.
        light: The direction from which the light shades the models, in glTF's space.
        camera: The camera of the hero's portrait.
        pose: The clip and the time in seconds into it of the hero's still pose.
    """

    revision: str
    rig: Rig
    parts: tuple[str, ...]
    layers: tuple[Layer, ...]
    atlas: int
    warp: str
    light: Vector
    camera: Camera
    pose: tuple[str, float]


@dataclass(frozen=True, slots=True)
class _Part:
    name: str
    template: Template
    layer: int
    # The slot number of the item that adds it.
    slot: int | None


class Figures:
    """Assembles heroes from the models that the games' imported catalogues keep."""

    def __init__(self, catalogues: Catalogues) -> None:
        """Assemble heroes from the imported catalogues.

        Args:
            catalogues: The games' imported catalogues.
        """
        self._catalogues = catalogues

    def figure(self, game: Game, look: Look) -> Figure | None:
        """Return a hero as its game's menu draws it; none where the server keeps no models.

        The hero wears what every hero of its faction and class holds, its clothes, its hair
        style and its facial hair. Of the meshes of one type, only those of the highest layer
        stay; the game's rules then take parts off or swap them, and the textures are painted
        from the lowest layer up, tinted with the hero's colours.

        Args:
            game: The hero's game.
            look: The hero's faction, class, colours, hair style and facial hair.
        """
        rules, heroes = game.catalogue, game.heroes
        contents = self._catalogues.contents(game)
        models = contents.hero_models
        if (
            rules is None
            or rules.models is None
            or heroes is None
            or models is None
            or contents.revision is None
            or look.faction not in models.rigs
        ):
            return None
        appearance = heroes.loadouts[look.faction][look.hero_class].appearance
        parts = _parts(rules, contents, models, look, appearance)
        figure = rules.models.figures[look.faction]
        return Figure(
            revision=contents.revision,
            rig=models.rigs[look.faction],
            parts=tuple(part.name for part in parts if part.template.kind == "mesh"),
            layers=tuple(
                _layer(models, rules.models.tints, contents.tables, look, part)
                for part in sorted(parts, key=lambda part: part.layer)
                if part.template.kind == "texture"
            ),
            atlas=models.atlas,
            warp=models.warp,
            light=rules.models.light,
            camera=figure.camera,
            pose=figure.pose,
        )


def _parts(
    rules: CatalogueRules,
    contents: Contents,
    models: HeroModels,
    look: Look,
    appearance: Sequence[int],
) -> list[_Part]:
    held = sorted(
        item.id
        for item in contents.items.values()
        if item.base and look.faction in item.factions and look.hero_class in item.classes
    )
    chosen = (*appearance, look.hair_style, look.facial_hair)
    worn = [
        contents.items[number]
        for number in dict.fromkeys((*held, *chosen))
        if number in contents.items
    ]
    parts = [
        _Part(name, models.templates[name], layer, rules.slots.get(item.slot or ""))
        for item in worn
        for layer, name in models.layers.get(item.id, ())
        if name in models.templates
    ]
    highest: dict[str, int] = {}
    for part in parts:
        if part.template.kind == "mesh":
            highest[part.template.type] = max(
                highest.get(part.template.type, part.layer), part.layer
            )
    parts = [
        part
        for part in parts
        if part.template.kind == "texture" or part.layer == highest[part.template.type]
    ]
    names = {part.name for part in parts}
    slots = {rules.slots.get(item.slot or "") for item in worn if not item.base}
    for rule in models.rules:
        if names.isdisjoint(rule.worn) and slots.isdisjoint(rule.worn_slots):
            continue
        kept = []
        for part in parts:
            if part.name not in rule.targets and part.slot not in rule.slots:
                kept.append(part)
            elif rule.replacement in models.templates:
                template = models.templates[rule.replacement]
                kept.append(replace(part, name=rule.replacement, template=template))
        parts = kept
    unique: dict[str, _Part] = {}
    for part in parts:
        unique.setdefault(part.name, part)
    return list(unique.values())


def _layer(
    models: HeroModels, tints: Tints, tables: Mapping[str, Element], look: Look, part: _Part
) -> Layer:
    x, y, _, _ = models.regions[part.template.type]
    dx, dy = part.template.offset
    skin, hair = tints.skin, tints.hair
    tint = part.template.colour
    lowest = skin.highest_layer is None or part.layer <= skin.highest_layer
    if part.template.type in skin.types and lowest:
        tint = _colour(tables.get(tints.table), skin, look.skin_tone)
    elif part.template.type in hair.types:
        tint = _colour(tables.get(tints.table), hair, look.hair_colour)
    return Layer(part.name, (x + dx, y + dy), tint)


def _colour(table: Element | None, tint: Tint, number: int) -> Colour:
    groups = table.children if table else ()
    for group in (found for found in groups if found.tag == tint.group):
        for colour in group.children:
            values = [int(value) for value in colour.text.split() if value.isdecimal()]
            if colour.attributes.get("id") == str(number) and len(values) == len(tint.default):
                red, green, blue = values
                return red, green, blue
    return tint.default
