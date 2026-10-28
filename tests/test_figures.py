from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.catalogue import FORMAT, CatalogueDocument, CatalogueFile, CatalogueItem, Element
from openfesl.core.catalogues import Catalogues
from openfesl.core.figures import Figures, Layer
from openfesl.core.games import Game, load_games
from openfesl.core.hero_models import HeroModels, Rig, Template
from openfesl.core.heroes import Look
from openfesl.formats.refractor_scripts import MeshRule
from openfesl.storage.catalogues import CatalogueFolder, SqlCatalogueStore

pytestmark = pytest.mark.anyio

# Invented parts of the National commando's starting outfit, numbered as the game data's
# loadout names them, with the head of a gunner, a Royal head, a hair style and a beard.
WORN = {
    56: (3, True, 7, [(0, "bare_torso"), (0, "skin")]),
    113: (6, True, 7, [(1, "underwear")]),
    251: (10, True, 1, [(0, "head"), (0, "face")]),
    250: (10, True, 4, [(0, "gunner_head")]),
    10: (0, False, 7, [(60, "hat")]),
    981: (6, False, 7, [(125, "trousers")]),
    979: (4, False, 7, [(125, "jacket"), (125, "jacket_cloth")]),
    120: (11, False, 7, [(55, "hair")]),
    126: (12, False, 7, [(45, "beard")]),
}
TEMPLATES = {
    "bare_torso": Template(type="cit_body", kind="mesh"),
    "jacket": Template(type="cit_body", kind="mesh"),
    "head": Template(type="cit_head", kind="mesh"),
    "bald_head": Template(type="cit_head", kind="mesh"),
    "gunner_head": Template(type="cit_head", kind="mesh"),
    "hat": Template(type="cit_hat", kind="mesh"),
    "skin": Template(type="cit_body", kind="texture"),
    "face": Template(type="cit_head", kind="texture", offset=(2, 3)),
    "underwear": Template(type="cit_legs", kind="texture"),
    "trousers": Template(type="cit_legs", kind="texture", offset=(4, 4), colour=(1, 2, 3)),
    "jacket_cloth": Template(type="cit_body", kind="texture"),
    "hair": Template(type="cit_headhair", kind="texture", colour=(30, 15, 10)),
    "beard": Template(type="cit_headhair", kind="texture"),
}
RULES = (
    # A hat takes the beard off, and any hat that is not a hero's own swaps its head.
    MeshRule(slots=(12,), worn=("hat",)),
    MeshRule(targets=("head",), replacement="bald_head", worn_slots=(0,)),
    MeshRule(targets=("skin",), worn=("invented_cape",)),
)
COLOURS = Element(
    tag="colors",
    children=(
        Element(tag="skin", children=(Element(tag="c", attributes={"id": "7"}, text="15 11 8"),)),
        Element(tag="hair", children=(Element(tag="c", attributes={"id": "3"}, text="13 6 4"),)),
    ),
)


def catalogue() -> CatalogueFile:
    items = tuple(
        CatalogueItem(
            id=number,
            kind="appearance",
            fields={"faction": "2", "kits": str(kits), "slot": str(slot), "default": str(base)},
        )
        for number, (slot, base, kits, _) in WORN.items()
    )
    royal_head = CatalogueItem(
        id=4, kind="appearance", fields={"faction": "1", "slot": "10", "default": "True"}
    )
    models = HeroModels(
        atlas=1024,
        regions={
            "cit_body": (0, 0, 1024, 512),
            "cit_head": (369, 512, 463, 296),
            "cit_headhair": (369, 512, 463, 296),
            "cit_legs": (0, 512, 369, 296),
            "cit_hat": (0, 808, 317, 216),
        },
        templates=TEMPLATES,
        layers={number: tuple(layers) for number, (*_, layers) in WORN.items()}
        | {4: ((0, "head"),)},
        rules=RULES,
        rigs={"national": Rig(model="skeleton", roles={"idle": ("idle",)})},
        warp="ramp",
    )
    document = CatalogueDocument(
        format=FORMAT,
        game="bfheroes",
        items=(*items, royal_head),
        tables={"Colors.xml": COLOURS},
        models=models,
    )
    meshes = {name: b"glTF" for name, found in TEMPLATES.items() if found.kind == "mesh"}
    textures = {name: b"RIFF" for name, found in TEMPLATES.items() if found.kind == "texture"}
    return CatalogueFile(document, {"en": {}}, {}, meshes | {"skeleton": b"glTF"}, textures)


async def test_a_hero_wears_its_parts_by_layer_and_rule_with_its_colours(
    catalogue_sessions: async_sessionmaker[AsyncSession], tmp_path: Path, bfheroes: Game
) -> None:
    catalogues = Catalogues(
        SqlCatalogueStore(catalogue_sessions), CatalogueFolder(tmp_path), load_games()
    )
    await catalogues.import_file(catalogue().write(), admin=None)
    revision = catalogues.contents(bfheroes).revision
    figures = Figures(catalogues)

    # A new National commando in its starting outfit, with a hair style and a beard.
    figure = figures.figure(bfheroes, Look("national", "commando", 7, 3, 120, 126))

    assert figure is not None and revision
    rules = bfheroes.catalogue
    assert rules and rules.models
    national = rules.models.figures["national"]
    assert (figure.revision, figure.rig.model, figure.warp, figure.atlas) == (
        revision,
        "skeleton",
        "ramp",
        1024,
    )
    assert (figure.camera, figure.pose, figure.light) == (
        national.camera,
        national.pose,
        rules.models.light,
    )
    # The jacket hides the bare torso of a lower layer; the hat takes the beard off and swaps
    # the head; neither the gunner's head nor the Royal one is worn.
    assert figure.parts == ("bald_head", "hat", "jacket")
    # From the lowest layer up: the skin's textures up to layer 0 take the skin tone, the hair
    # takes the hair colour over its own, and the others keep theirs.
    assert figure.layers == (
        Layer("skin", (0, 0), (15, 11, 8)),
        Layer("face", (371, 515), (15, 11, 8)),
        Layer("underwear", (0, 512), None),
        Layer("hair", (369, 512), (13, 6, 4)),
        Layer("trousers", (4, 516), (1, 2, 3)),
        Layer("jacket_cloth", (0, 0), None),
    )

    # A hair colour that the table lacks: the hair takes the colour that the game starts with.
    unknown = figures.figure(bfheroes, Look("national", "commando", 7, 5, 120, 126))
    assert unknown and unknown.layers[3] == Layer("hair", (369, 512), (100, 75, 55))
    # A faction without a skeleton has no figure.
    assert figures.figure(bfheroes, Look("royal", "commando", 7, 3, 0, 0)) is None


async def test_a_catalogue_whose_models_the_server_keeps_no_files_of_has_no_figures(
    catalogue_sessions: async_sessionmaker[AsyncSession],
    bfheroes: Game,
) -> None:
    catalogues = Catalogues(SqlCatalogueStore(catalogue_sessions), None, load_games())
    await catalogues.import_file(catalogue().write(), admin=None)

    assert catalogues.contents(bfheroes).models == 0
    assert Figures(catalogues).figure(bfheroes, Look("national", "commando", 7, 3, 0, 0)) is None
