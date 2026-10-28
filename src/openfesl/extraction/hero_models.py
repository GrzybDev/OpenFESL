from collections.abc import Collection, Mapping, Sequence
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from openfesl.core.catalogue import CatalogueItem, CatalogueRules
from openfesl.core.hero_models import FigureRules, HeroModels, ModelRules, Rig, Template
from openfesl.extraction.copies import ExtractionError, archive, member, members
from openfesl.formats.definitions import read_children
from openfesl.formats.gltf import Region, mesh_glb, skeleton_glb
from openfesl.formats.icons import webp
from openfesl.formats.refractor import (
    Animation,
    Skeleton,
    read_animation,
    read_skeleton,
    read_skinned_mesh,
)
from openfesl.formats.refractor_scripts import (
    MESH_ITEM,
    TEXTURE_ITEM,
    AnimationSystem,
    GeometryTemplate,
    read_animation_system,
    read_mesh_rules,
    read_regions,
    read_templates,
)

type Files = dict[str, bytes]
type Layers = dict[int, tuple[tuple[int, str], ...]]


def extract_models(
    rules: CatalogueRules, folder: Path, items: Sequence[CatalogueItem], dressed: Collection[int]
) -> tuple[HeroModels, Files, Files]:
    """Read the models that a game's menu draws its heroes with from a copy of the game.

    Only the parts that the dressed items name, or that rules put in their place, are read:
    each mesh skinned to the skeleton of the first faction whose items wear it, and each
    texture as a lossy WebP picture.

    Args:
        rules: The rules of the game's catalogue, which say where the models lie.
        folder: The folder of the copy's executable.
        items: The catalogue's items, whose factions pick the skeletons of their meshes and
            whose definitions may hold the layer of their parts.
        dressed: The numbers of the items whose parts are read.

    Returns:
        What the catalogue keeps of the models, with the models as binary glTF files and the
        textures as WebP pictures, each by name.

    Raises:
        ExtractionError: When the copy lacks a file of the models or a file is not as the
            game's rules describe it.
    """
    models = rules.models
    if models is None:
        raise ExtractionError("the catalogue carries no hero models")
    files: Files = {}
    rigs: dict[str, Rig] = {}
    skeletons: dict[str, Skeleton] = {}
    with archive(folder, models.animations) as animations:
        for faction, figure in models.figures.items():
            rigs[faction], skeletons[faction], model = _rig(models, figure, animations)
            files[rigs[faction].model] = model
    with archive(folder, rules.definitions) as definitions:
        element = next(found.element for found in rules.items if found.file == models.items)
        try:
            data = member(definitions, rules.definitions, models.items)
            children = read_children(data, element, models.template)
            worn = {item: named for item, named in children.items() if item in dressed}
            layers = _layers(models, worn, items)
            regions = read_regions(member(definitions, rules.definitions, models.regions))
            mesh_rules = read_mesh_rules(member(definitions, rules.definitions, models.mesh_rules))
            scripts = _scripts(definitions)
        except ValueError as error:
            raise ExtractionError(f"{rules.definitions}: {error}") from None
    used = {name for named in layers.values() for _, name in named}
    slots = {
        int(slot)
        for item in items
        if item.id in layers and (slot := item.fields.get(rules.fields.slot, "")).isdecimal()
    }
    # Only the rules that the dressed parts may start take parts off the heroes that are drawn.
    mesh_rules = [
        rule
        for rule in mesh_rules
        if not used.isdisjoint(rule.worn) or not slots.isdisjoint(rule.worn_slots)
    ]
    used |= {rule.replacement for rule in mesh_rules if rule.replacement}
    owners = _owners(rules, items, layers)
    templates: dict[str, Template] = {}
    textures: Files = {}
    with archive(folder, models.files) as parts:
        found = members(parts)
        for name in sorted(name for name in used if not models.left_out.search(name)):
            template, at = scripts.get(name, (None, ""))
            item_type = template.item_type.lower() if template else ""
            region = regions.get(item_type)
            # The game draws no part whose script, region or file the copy lacks: its atlas has
            # no place for a type without a region.
            if template is None or region is None:
                continue
            try:
                if template.kind == MESH_ITEM:
                    path = models.mesh.format(folder=at, name=template.name).lower()
                    if path in found:
                        lod = read_skinned_mesh(parts.read(found[path])).geometries[0][0]
                        area = Region(*(value / models.atlas for value in region))
                        skeleton = skeletons[owners.get(name, next(iter(skeletons)))]
                        files[name] = mesh_glb(name, lod, skeleton, area)
                        templates[name] = Template(type=item_type, kind="mesh")
                elif template.kind == TEXTURE_ITEM:
                    path = _unmounted(template.file, models.mount)
                    if path in found:
                        textures[name] = webp(parts.read(found[path]), models.quality)
                        templates[name] = Template.model_validate(
                            {
                                "type": item_type,
                                "kind": "texture",
                                "offset": template.coordinate,
                                "colour": template.colour,
                            }
                        )
            except ValueError as error:
                raise ExtractionError(f"{models.files}: {template.name}: {error}") from None
    warp_archive, warp_file = models.warp
    warp = PurePosixPath(warp_file).stem.lower()
    with archive(folder, warp_archive) as opened:
        try:
            textures[warp] = webp(member(opened, warp_archive, warp_file))
        except ValueError as error:
            raise ExtractionError(f"{warp_archive}: {warp_file}: {error}") from None
    document = HeroModels(
        atlas=models.atlas,
        regions=regions,
        templates=templates,
        layers=layers,
        rules=tuple(mesh_rules),
        rigs=rigs,
        warp=warp,
    )
    return document, files, textures


def _rig(
    models: ModelRules, figure: FigureRules, animations: ZipFile
) -> tuple[Rig, Skeleton, bytes]:
    found = members(animations)
    try:
        skeleton = read_skeleton(member(animations, models.animations, figure.skeleton))
        systems = [
            read_animation_system(member(animations, models.animations, system))
            for system in figure.systems
        ]
        played: dict[str, Animation] = {}
        roles: dict[str, tuple[str, ...]] = {}
        for role, names in models.clips.items():
            started = [bundle for name in names for bundle in _started(systems, name)]
            for name, animation in started:
                path = _unmounted(animation, models.animation_mount)
                info = found.get(figure.relocated.get(path, path))
                if info is None:
                    raise ValueError(f"the copy lacks {animation}")
                played[name] = read_animation(animations.read(info))
            roles[role] = tuple(name for name, _ in started)
        model = skeleton_glb(skeleton, played, models.frame_rate)
    except ValueError as error:
        raise ExtractionError(f"{models.animations}: {figure.skeleton}: {error}") from None
    name = PurePosixPath(figure.skeleton).stem.lower()
    return Rig(model=name, roles=roles), skeleton, model


def _started(systems: list[AnimationSystem], name: str) -> list[tuple[str, str]]:
    # The bundles that the first system to know a name starts by it, a trigger's or the bundle
    # of that name, each with its animation.
    key = name.lower()
    for system in systems:
        trigger = system.triggers.get(key)
        if trigger is None and key not in system.bundles:
            continue
        started = []
        for bundle_name in trigger.bundles if trigger else (key,):
            bundle = system.bundles[bundle_name]
            if len(bundle.animations) != 1:
                raise ValueError(f"the bundle {bundle_name} plays no single animation")
            started.append((bundle_name, bundle.animations[0]))
        return started
    raise ValueError(f"no animation system has {name}")


def _layers(
    models: ModelRules,
    children: Mapping[int, list[tuple[str, dict[str, str]]]],
    items: Sequence[CatalogueItem],
) -> Layers:
    fields = {item.id: item.fields for item in items}
    layers = {}
    for item, named in children.items():
        # Older copies set one layer for all of an item's templates.
        shared = fields.get(item, {}).get(models.layer, "")
        kept = tuple(
            (int(attributes.get(models.layer, shared)), name.lower())
            for name, attributes in named
            if name and not models.left_out.search(name.lower())
        )
        if kept:
            layers[item] = kept
    return layers


def _scripts(definitions: ZipFile) -> dict[str, tuple[GeometryTemplate, str]]:
    # Every template that the scripts of the items' folders create, with its script's folder.
    # A later script of a name replaces an earlier one: the copies' British folder also defines
    # some National parts, laid out for the other faction's meshes.
    scripts: dict[str, tuple[GeometryTemplate, str]] = {}
    for name, info in sorted(members(definitions).items()):
        folder = PurePosixPath(info.filename).parent
        if name.endswith(".con") and folder.parts:
            for template in read_templates(definitions.read(info)):
                scripts[template.name.lower()] = (template, str(folder))
    return scripts


def _owners(
    rules: CatalogueRules, items: Sequence[CatalogueItem], layers: Layers
) -> dict[str, str]:
    # The first faction with a skeleton whose items wear each template.
    figures = rules.models.figures if rules.models else {}
    owners: dict[str, str] = {}
    for item in items:
        mask = item.fields.get(rules.fields.factions, "")
        fitting = [
            name for name in figures if not mask.isdecimal() or int(mask) & rules.factions[name]
        ]
        for _, name in layers.get(item.id, ()) if fitting else ():
            owners.setdefault(name, fitting[0])
    return owners


def _unmounted(path: str, mount: str) -> str:
    # A path that the engine reads, as the lower-cased name of a file in the archive that it
    # mounts at a folder.
    relative = path.replace("\\", "/").lower()
    return relative.removeprefix(mount.lower().rstrip("/") + "/")
