import re
from dataclasses import dataclass, field, replace

type Command = tuple[str, tuple[str, ...]]
"""A script's command, its name lower-cased, and its arguments."""

# An argument: a quoted text, which keeps its spaces, or a run of other characters.
_ARGUMENT = re.compile(r'"([^"]*)"|(\S+)')
# One of the game's scripts holds stray DEL bytes inside a value, which the engine reads past.
_DELETE = "\x7f"
_MESH_RULE = "customization."
# The kinds of the templates of a soldier's meshes and of its textures, lower-cased.
MESH_ITEM = "soldiermeshitem"
TEXTURE_ITEM = "soldiertextureitem"


@dataclass(frozen=True, slots=True)
class GeometryTemplate:
    """A part that a soldier wears, as a script of the game's items creates it.

    Attributes:
        kind: Its type lower-cased, such as MESH_ITEM or TEXTURE_ITEM.
        name: Its name.
        item_type: The type of item it belongs to, which picks its region of the soldier's
            texture.
        file: The texture it paints, by the engine's path, for a texture.
        coordinate: Where it paints the texture in its region, from the region's top left.
        colour: The colour it tints the texture with, if it names one.
    """

    kind: str
    name: str
    item_type: str = ""
    file: str = ""
    coordinate: tuple[int, ...] = (0, 0)
    colour: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class MeshRule:
    """A rule that takes parts off a soldier, or swaps them, while others are worn.

    Names are lower-cased, as the engine compares them regardless of case.

    Attributes:
        targets: The templates it takes off.
        slots: The slots whose parts it takes off.
        replacement: The template it puts in their place; none when it only takes them off.
        worn: The templates of which any one worn starts the rule.
        worn_slots: The slots of which any one worn starts the rule.
    """

    targets: tuple[str, ...] = ()
    slots: tuple[int, ...] = ()
    replacement: str | None = None
    worn: tuple[str, ...] = ()
    worn_slots: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Bundle:
    """Animations that an animation system plays together.

    Attributes:
        animations: The animations' files, by the engine's paths.
    """

    animations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Trigger:
    """What starts bundles in an animation system.

    Attributes:
        bundles: The names of the bundles it starts, lower-cased.
    """

    bundles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnimationSystem:
    """A soldier's animation system, which names the animations it plays.

    Attributes:
        bundles: Its bundles, by their names lower-cased.
        triggers: Its triggers, likewise.
    """

    bundles: dict[str, Bundle] = field(default_factory=dict)
    triggers: dict[str, Trigger] = field(default_factory=dict)


def read_script(data: bytes) -> list[Command]:
    """Read a script of the Refractor engine, a .con or .inc file, as its commands in order.

    Each line holds a command and its arguments. Blank lines and rem lines are left out.

    Args:
        data: The script.
    """
    commands = []
    for line in data.decode("latin-1").replace(_DELETE, "").splitlines():
        words = [quoted or bare for quoted, bare in _ARGUMENT.findall(line)]
        if words and words[0].lower() != "rem":
            commands.append((words[0].lower(), tuple(words[1:])))
    return commands


def read_templates(data: bytes) -> list[GeometryTemplate]:
    """Read the geometry templates that a script creates, with what it sets of each.

    Args:
        data: The script.

    Raises:
        ValueError: When a coordinate or colour is no numbers joined by slashes.
    """
    templates: list[GeometryTemplate] = []
    for command, arguments in read_script(data):
        if command == "geometrytemplate.create" and len(arguments) == 2:
            templates.append(GeometryTemplate(arguments[0].lower(), arguments[1]))
        elif not templates or not arguments:
            continue
        elif command == "geometrytemplate.itemtype":
            templates[-1] = replace(templates[-1], item_type=arguments[0])
        elif command == "geometrytemplate.filename":
            templates[-1] = replace(templates[-1], file=arguments[0])
        elif command == "geometrytemplate.setcoordinate":
            templates[-1] = replace(templates[-1], coordinate=_numbers(arguments[0], 2))
        elif command == "geometrytemplate.setcolor":
            templates[-1] = replace(templates[-1], colour=_numbers(arguments[0], 3))
    return templates


def read_regions(data: bytes) -> dict[str, tuple[int, int, int, int]]:
    """Read where each type of item lies in a soldier's texture, as its x, y, width and height.

    Args:
        data: The script that sets the regions; types are lower-cased.

    Raises:
        ValueError: When a region is not four numbers joined by slashes.
    """
    regions = {}
    for command, arguments in read_script(data):
        if (
            command == "skinnedmeshrenderer.setcustomtexturecoordoffsetandscale"
            and len(arguments) == 2
        ):
            x, y, width, height = _numbers(arguments[1], 4)
            regions[arguments[0].lower()] = (x, y, width, height)
    return regions


def read_mesh_rules(data: bytes) -> list[MeshRule]:
    """Read the rules of a script of mesh rules, in order.

    Each remove or replacement group of a script's rule becomes a rule of its own, which takes
    the script rule's targets off, or swaps them, only while its own dependencies are worn.

    Args:
        data: The script.

    Raises:
        ValueError: When a slot is no number.
    """
    rules: list[MeshRule] = []
    for command, arguments in read_script(data):
        name = command.removeprefix(_MESH_RULE)
        if name == "createmeshrule":
            rules.append(MeshRule())
        elif name in ("addremovegroup", "addreplacementgroup"):
            if rules and (rules[-1].replacement or rules[-1].worn or rules[-1].worn_slots):
                rules.append(MeshRule(rules[-1].targets, rules[-1].slots))
        elif rules and arguments:
            rule, value = rules[-1], arguments[0].lower()
            match name:
                case "addinputmesh":
                    rules[-1] = replace(rule, targets=(*rule.targets, value))
                case "addinputslot":
                    rules[-1] = replace(rule, slots=(*rule.slots, int(value)))
                case "setreplacementmesh":
                    rules[-1] = replace(rule, replacement=value)
                case "addgeomdependency":
                    rules[-1] = replace(rule, worn=(*rule.worn, value))
                case "addslotdependency":
                    rules[-1] = replace(rule, worn_slots=(*rule.worn_slots, int(value)))
    return rules


def read_animation_system(data: bytes) -> AnimationSystem:
    """Read an animation system's bundles and triggers.

    Args:
        data: The script of the animation system.
    """
    system = AnimationSystem()
    bundle = trigger = ""
    for command, arguments in read_script(data):
        if not arguments:
            continue
        value = arguments[-1]
        match command:
            case "animationsystem.createbundle":
                bundle = value.lower()
                system.bundles[bundle] = Bundle()
            case "animationsystem.createtrigger":
                trigger = value.lower()
                system.triggers[trigger] = Trigger()
            case "animationbundle.addanimation" if bundle:
                found = system.bundles[bundle]
                system.bundles[bundle] = replace(found, animations=(*found.animations, value))
            case "animationtrigger.addbundle" if trigger:
                found_trigger = system.triggers[trigger]
                system.triggers[trigger] = replace(
                    found_trigger, bundles=(*found_trigger.bundles, value.lower())
                )
    return system


def _numbers(value: str, count: int) -> tuple[int, ...]:
    numbers = tuple(int(number) for number in value.split("/"))
    if len(numbers) != count:
        raise ValueError(f"{value!r} is not {count} numbers joined by slashes")
    return numbers
