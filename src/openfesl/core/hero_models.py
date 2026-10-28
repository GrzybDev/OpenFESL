import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, PositiveFloat, PositiveInt

from openfesl.formats.refractor_scripts import MeshRule

type Colour = tuple[int, int, int]
type Vector = tuple[float, float, float]


class Camera(BaseModel):
    """A camera that circles a hero standing at the origin and facing +Z, in glTF's space.

    Attributes:
        look_at: The point it looks at, in metres.
        distance: How far it stands from that point, in metres.
        azimuth: How far around the hero it stands, in degrees; 180 faces the hero.
        zenith: How far from straight above the point it stands, in degrees; 90 is level.
        fov: Its vertical field of view, in radians.
        near: The nearest distance it draws, in metres.
        far: The farthest distance it draws, in metres.
    """

    model_config = ConfigDict(frozen=True)

    look_at: Vector
    distance: PositiveFloat
    azimuth: float
    zenith: float
    fov: PositiveFloat
    near: PositiveFloat
    far: PositiveFloat


class FigureRules(BaseModel):
    """Where a copy of a game keeps the skeleton of a faction's heroes, and how its menu shows them.

    Paths are in the archive of animations.

    Attributes:
        skeleton: The skeleton.
        systems: The animation systems whose bundles and triggers name the clips, searched in
            order.
        relocated: Where the copy keeps animations that the systems name where it lacks them,
            by the paths that the systems name, both lower-cased.
        camera: The camera of the hero's portrait.
        pose: The clip and the time in seconds into it of the hero's still pose.
    """

    model_config = ConfigDict(frozen=True)

    skeleton: str
    systems: list[str]
    relocated: dict[str, str] = {}
    camera: Camera
    pose: tuple[str, float]


class Tint(BaseModel):
    """The colour of a hero's that tints its textures of some types before they are painted.

    Attributes:
        group: The group of the colours in the table of colours, whose numbers a hero names.
        types: The item types of the textures it tints, lower-cased.
        highest_layer: The highest layer of a texture it tints, if it tints only the lower ones.
        default: The colour of a number that the table lacks.
    """

    model_config = ConfigDict(frozen=True)

    group: str
    types: frozenset[str]
    highest_layer: int | None = None
    default: Colour


class Tints(BaseModel):
    """The colours of a hero that tint its textures, from a table of the catalogue.

    Attributes:
        table: The catalogue's table of the colours.
        skin: The skin tone's tint.
        hair: The hair colour's tint, which a texture takes when the skin tone's does not.
    """

    model_config = ConfigDict(frozen=True)

    table: str
    skin: Tint
    hair: Tint


class ModelRules(BaseModel):
    """Where a copy of a game keeps the models that its menu draws heroes with, and how it does.

    The templates of the parts that heroes wear are scripts in the archive of definitions,
    beside the item definitions. Paths of archives are relative to the folder of the game's
    executable.

    Attributes:
        files: The archive of the parts' meshes and textures.
        mount: The folder at which the game mounts the archives of definitions and of files,
            which the textures' paths start with.
        mesh: Where a template's mesh lies in the archive of files, with {folder} for the
            folder of the template's script and {name} for the template's name.
        items: The file of item definitions that names the templates of each item.
        template: The name of the elements of an item that name its templates.
        layer: The name of the attribute of such an element that holds the template's layer,
            or of the item's value that holds the layer of all its templates in older copies.
        regions: The script that sets each item type's region of a hero's texture.
        mesh_rules: The script of the rules that take parts off a hero, or swap them.
        left_out: What the names of templates that no hero wears match.
        atlas: The width and height of the texture into which a hero's textures are painted.
        quality: The quality of the textures as lossy WebP pictures, from 0 to 100.
        animations: The archive of the skeletons and their animations.
        animation_mount: The folder at which the game mounts the archive of animations, which
            the animation systems' paths start with.
        frame_rate: How many frames the animations play each second.
        warp: The archive and the file in it of the ramp that shades the menu's models.
        clips: The bundles that the site plays in each role, by role; a trigger stands for
            every bundle it starts.
        figures: The skeleton and animations of each faction's heroes, by faction.
        light: The direction from which the menu's light shades the models, in glTF's space.
        tints: The colours of a hero that tint its textures.
    """

    model_config = ConfigDict(frozen=True)

    files: str
    mount: str
    mesh: str
    items: str
    template: str
    layer: str
    regions: str
    mesh_rules: str
    left_out: re.Pattern[str]
    atlas: PositiveInt
    quality: int
    animations: str
    animation_mount: str
    frame_rate: PositiveFloat
    warp: tuple[str, str]
    clips: dict[str, list[str]]
    figures: dict[str, FigureRules]
    light: Vector
    tints: Tints


class Template(BaseModel):
    """A part that heroes wear: a mesh, or a texture painted into a hero's texture.

    Attributes:
        type: The type of item it belongs to, lower-cased, which names its region of a hero's
            texture.
        kind: Whether it is a mesh among the catalogue's models or a texture among its
            textures, either named after it.
        offset: Where a texture lies in its region, from the region's top left.
        colour: The colour that its template tints a texture with, if it names one.
    """

    model_config = ConfigDict(frozen=True)

    type: str
    kind: Literal["mesh", "texture"]
    offset: tuple[int, int] = (0, 0)
    colour: Colour | None = None


class Rig(BaseModel):
    """The skeleton of a faction's heroes and its animations.

    Attributes:
        model: The name of its model among the catalogue's models, which holds its clips.
        roles: The names of the clips that the site plays in each role, by role.
    """

    model_config = ConfigDict(frozen=True)

    model: str
    roles: dict[str, tuple[str, ...]]


class HeroModels(BaseModel):
    """The models and textures that a game draws its heroes with, as its catalogue holds them.

    Attributes:
        atlas: The width and height of a hero's texture, into which its textures are painted.
        regions: Where each type of item lies in a hero's texture, as x, y, width and height,
            by the type lower-cased.
        templates: The parts that items name, by their names lower-cased.
        layers: The templates that each item adds to a hero, each with its layer, in order, by
            the item's number.
        rules: The rules that take parts off a hero, or swap them, in order.
        rigs: The skeleton of each faction's heroes, by faction.
        warp: The name among the textures of the ramp that shades the menu's models.
    """

    model_config = ConfigDict(frozen=True)

    atlas: int
    regions: dict[str, tuple[int, int, int, int]]
    templates: dict[str, Template]
    layers: dict[int, tuple[tuple[int, str], ...]]
    rules: tuple[MeshRule, ...] = ()
    rigs: dict[str, Rig]
    warp: str
