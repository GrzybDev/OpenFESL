import pytest

from openfesl.formats.refractor_scripts import (
    MESH_ITEM,
    TEXTURE_ITEM,
    AnimationSystem,
    Bundle,
    GeometryTemplate,
    MeshRule,
    Trigger,
    read_animation_system,
    read_mesh_rules,
    read_regions,
    read_templates,
)


def test_templates_keep_what_their_scripts_set_whatever_the_case_quotes_or_stray_bytes() -> None:
    # As the game's own scripts: CRLF lines, a commented colour, a quoted file name, DEL bytes
    # inside a coordinate and an object template after each geometry template.
    script = (
        b"rem geometryTemplate.setColor 0/0/0\r\n"
        b"GeometryTemplate.create SoldierTextureItem Invented_Hair\r\n"
        b"geometryTemplate.itemType CIT_Headhair\r\n"
        b'geometryTemplate.FileName "Objects/Items/Invented Folder/hair.tga"\r\n'
        b"geometryTemplate.setCoordinate 2\x7f\x7f\x7f/3\r\n"
        b"\tgeometryTemplate.setColor 30/15/10\r\n"
        b"\r\n"
        b"ObjectTemplate.create KitPart Invented_Hair\r\n"
        b"GEOMETRYTEMPLATE.CREATE SoldierMeshItem invented_hat\r\n"
        b"geometryTemplate.itemType CIT_Hat\r\n"
    )

    assert read_templates(script) == [
        GeometryTemplate(
            TEXTURE_ITEM,
            "Invented_Hair",
            "CIT_Headhair",
            "Objects/Items/Invented Folder/hair.tga",
            (2, 3),
            (30, 15, 10),
        ),
        GeometryTemplate(MESH_ITEM, "invented_hat", "CIT_Hat"),
    ]
    with pytest.raises(ValueError, match="not 2 numbers"):
        read_templates(script.replace(b"2\x7f\x7f\x7f/3", b"2/3/4"))


def test_regions_and_mesh_rules_are_read_in_order_with_their_names_lower_cased() -> None:
    settings = (
        b"rem ** regions **\n"
        b"skinnedMeshRenderer.setCustomTextureCoordOffsetAndScale CIT_Hat\t0/808/317/216\n"
        b"skinnedMeshRenderer.setCustomTextureCoordOffsetAndScale\tCIT_HeadHair\t\t369/512/463/296\n"
        b"skinnedMeshRenderer.setSaveCustomizedTextureAtlasToDisk 0\n"
    )
    rules = (
        b'Customization.createMeshRule "Replace the hat"\n'
        b'Customization.addInputMesh "Invented_Hat"\n'
        b"Customization.addReplacementGroup\n"
        b'Customization.setReplacementMesh "invented_cap"\n'
        b'Customization.addGeomDependency "INVENTED_HAIR"\n'
        b'Customization.addGeomDependency ""\n'
        b'Customization.createMeshRule "Remove or swap the tail"\n'
        b'Customization.addInputMesh "invented_tail"\n'
        b"Customization.addRemoveGroup\n"
        b'Customization.addGeomDependency "invented_cape"\n'
        b"Customization.addReplacementGroup\n"
        b'Customization.setReplacementMesh "invented_short_tail"\n'
        b'Customization.addGeomDependency "invented_coat"\n'
        b"Customization.addReplacementGroup\n"
        b'Customization.setReplacementMesh "invented_bare_tail"\n'
        b'Customization.addGeomDependency "invented_vest"\n'
        b'Customization.createMeshRule "Remove the hair"\n'
        b"Customization.addInputSlot 11\n"
        b"Customization.addRemoveGroup\n"
        b"Customization.addSlotDependency 0\n"
        b"Customization.finalizeMeshRules\n"
    )

    assert read_regions(settings) == {
        "cit_hat": (0, 808, 317, 216),
        "cit_headhair": (369, 512, 463, 296),
    }
    # Each group of a rule takes the rule's targets off, or swaps them, on its own dependencies.
    assert read_mesh_rules(rules) == [
        MeshRule(("invented_hat",), (), "invented_cap", ("invented_hair", "")),
        MeshRule(("invented_tail",), (), None, ("invented_cape",)),
        MeshRule(("invented_tail",), (), "invented_short_tail", ("invented_coat",)),
        MeshRule(("invented_tail",), (), "invented_bare_tail", ("invented_vest",)),
        MeshRule((), (11,), None, (), (0,)),
    ]
    with pytest.raises(ValueError, match="not 4 numbers"):
        read_regions(settings.replace(b"0/808/317/216", b"0/808"))


def test_an_animation_system_names_the_bundles_that_its_triggers_start() -> None:
    system = (
        b"rem *** Bundles ***\n"
        b"animationSystem.createBundle idle\n"
        b"animationBundle.addAnimation Objects/soldiers/invented/Animations/idle.baf\n"
        b"animationSystem.createBundle Wave\n"
        b"animationBundle.isLooping 0\n"
        b"animationBundle.fadeOutTime 0.3\n"
        b"animationBundle.addAnimation Objects/soldiers/invented/Animations/wave.baf\n"
        b"animationManager.ignoreMotherOrientation 12\n"
        b"animationSystem.createTrigger RandomTrigger face_rnd_idle\n"
        b"animationTrigger.addBundle IDLE\n"
        b"animationTrigger.addBundle wave\n"
        b"animationTrigger.fadeInTime 0.5\n"
    )

    assert read_animation_system(system) == AnimationSystem(
        {
            "idle": Bundle(("Objects/soldiers/invented/Animations/idle.baf",)),
            "wave": Bundle(("Objects/soldiers/invented/Animations/wave.baf",)),
        },
        {"face_rnd_idle": Trigger(("idle", "wave"))},
    )
