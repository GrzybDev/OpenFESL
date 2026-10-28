import struct
from pathlib import Path
from zipfile import ZipFile

import pytest
from game_files import COPY, NEEDS_COPY
from model_files import (
    DECLARATION,
    IDENTITY,
    SHIFTED,
    VERTICES,
    channel,
    material,
    rigs,
    skeleton,
    skinned_mesh,
    u32,
)

from openfesl.formats.refractor import (
    Animation,
    Material,
    Node,
    RigBone,
    SkinnedMesh,
    Track,
    read_animation,
    read_skeleton,
    read_skinned_mesh,
)


def test_a_mesh_gives_each_geometrys_materials_their_rig_vertices_and_triangles() -> None:
    # A newer mesh: the flag byte set, two geometries, all rigs before all materials, and a
    # first vertex that no triangle uses. The second geometry has no bones and starts at
    # vertex 1.
    body = rigs([(3, IDENTITY), (5, SHIFTED)]) + rigs([])
    body += u32(1) + material(["invented.dds"], (0, 0, 3, 4))
    body += u32(1) + material([], (1, 3, 3, 3))

    mesh = read_skinned_mesh(skinned_mesh(11, 1, [1, 1], [1, 3, 2, 2, 1, 0], body))

    first = Material(
        "SoldierItem.fx",
        "Invented",
        ("invented.dds",),
        (RigBone(3, IDENTITY), RigBone(5, SHIFTED)),
        tuple(VERTICES),
        ((1, 3, 2),),
    )
    second = Material("SoldierItem.fx", "Invented", (), (), tuple(VERTICES[1:]), ((2, 1, 0),))
    assert mesh == SkinnedMesh((((first,),), ((second,),)))


def test_the_oldest_meshes_keep_a_pivot_and_share_one_rig_among_their_materials() -> None:
    body = rigs([(0, IDENTITY), (1, SHIFTED)], pivot=True) + u32(2)
    body += material(["a.dds"], (0, 0, 3, 3)) + material(["b.dds"], (1, 3, 3, 3))

    (lod,) = read_skinned_mesh(skinned_mesh(4, 0, [1], [0, 1, 2, 2, 1, 0], body)).geometries[0]

    assert [m.textures for m in lod] == [("a.dds",), ("b.dds",)]
    assert lod[0].rig == lod[1].rig == (RigBone(0, IDENTITY), RigBone(1, SHIFTED))


def test_a_mesh_whose_parts_do_not_fit_together_is_refused() -> None:
    def mesh(
        indices: list[int], ranges: tuple[int, int, int, int], version: int = 11, extra: bytes = b""
    ) -> bytes:
        body = rigs([(0, IDENTITY), (1, IDENTITY)]) + u32(1) + material([], ranges)
        return skinned_mesh(version, 0, [1], indices, body + extra)

    assert read_skinned_mesh(mesh([0, 1, 2], (0, 0, 3, 3)))
    with pytest.raises(ValueError, match="version 6"):
        read_skinned_mesh(mesh([0, 1, 2], (0, 0, 3, 3), version=6))
    with pytest.raises(ValueError, match="bytes after its end"):
        read_skinned_mesh(mesh([0, 1, 2], (0, 0, 3, 3), extra=b"\0"))
    with pytest.raises(ValueError, match="ends before"):
        read_skinned_mesh(mesh([0, 1, 2], (0, 0, 3, 3))[:-1])
    with pytest.raises(ValueError, match="names a vertex it lacks"):
        read_skinned_mesh(mesh([0, 1, 3], (0, 0, 3, 3)))
    with pytest.raises(ValueError, match="outside its buffers"):
        read_skinned_mesh(mesh([0, 1, 2], (2, 0, 3, 3)))
    with pytest.raises(ValueError, match="names a bone its rig lacks"):
        body = rigs([(0, IDENTITY)]) + u32(1) + material([], (0, 0, 3, 3))
        read_skinned_mesh(skinned_mesh(11, 0, [1], [0, 1, 2], body))
    with pytest.raises(ValueError, match="laid out in another way"):
        body = rigs([]) + u32(1) + material([], (0, 0, 3, 3))
        read_skinned_mesh(skinned_mesh(11, 0, [1], [0, 1, 2], body, DECLARATION[1:]))


def test_a_skeleton_names_each_nodes_parent_and_rest_transform() -> None:
    nodes = read_skeleton(skeleton([("root", -1), ("hips", 0), ("arm", 1), ("leg", 1)])).nodes

    assert nodes[0] == Node("root", None, (0.0, 0.0, 0.0, 1.0), (0.0, 0.5, 0.0))
    assert [(node.name, node.parent) for node in nodes[1:]] == [("hips", 0), ("arm", 1), ("leg", 1)]
    assert nodes[3].position == (3.0, 0.5, 0.0)
    with pytest.raises(ValueError, match="comes before its parent"):
        read_skeleton(skeleton([("root", -1), ("hips", 1)]))
    with pytest.raises(ValueError, match="version 3"):
        read_skeleton(b"\3\0\0\0" + skeleton([])[4:])


def test_an_animation_spreads_repeated_and_whole_runs_over_each_nodes_frames() -> None:
    # Two nodes over three frames at a precision of 10 bits, so positions reach 32 metres.
    frames = 3
    still = [channel((True, [v]), frames=frames) for v in (0, 0, 0, 32767, 0, 0, 0)]
    moving = [
        channel((False, [0, 16384]), (False, [-32767]), frames=frames),
        *(channel((True, [0]), frames=frames) for _ in range(2)),
        channel((True, [32767]), frames=frames),
        channel((False, [32767, -32767, 0]), frames=frames),
        *(channel((True, [0]), frames=frames) for _ in range(2)),
    ]
    nodes = b""
    for channels in (still, moving):
        size = sum(struct.unpack_from("<H", c)[0] for c in channels)
        nodes += struct.pack("<H", size) + b"".join(channels)
    data = u32(4) + struct.pack("<3H", 2, 0, 7) + struct.pack("<IB", frames, 10) + nodes

    assert read_animation(data) == Animation(
        frames,
        (
            Track(0, ((0.0, 0.0, 0.0, 1.0),) * frames, ((0.0, 0.0, 0.0),) * frames),
            Track(
                7,
                tuple((x, 0.0, 0.0, 1.0) for x in (0.0, 16384 / 32767, -1.0)),
                tuple((x, 0.0, 0.0) for x in (32.0, -32.0, 0.0)),
            ),
        ),
    )
    # The first run covers a frame too few.
    with pytest.raises(ValueError, match="channel 0 of node 0"):
        read_animation(data.replace(bytes([0x83, 2]), bytes([0x82, 2]), 1))
    with pytest.raises(ValueError, match="bytes after its end"):
        read_animation(data + b"\0")


@NEEDS_COPY
def test_every_model_file_of_the_copy_reads_to_its_exact_end() -> None:
    assert COPY
    readers = {".skinnedmesh": read_skinned_mesh, ".ske": read_skeleton, ".baf": read_animation}
    read: dict[str, int] = dict.fromkeys(readers, 0)
    for archive in sorted((Path(COPY) / "mods" / "bfheroes").rglob("*.zip")):
        with ZipFile(archive) as files:
            for info in files.infolist():
                suffix = Path(info.filename).suffix.lower()
                if suffix in readers:
                    readers[suffix](files.read(info))
                    read[suffix] += 1
    assert all(read.values())
