import json
import math
import struct
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest
from game_files import COPY, NEEDS_COPY

from openfesl.formats.gltf import WHOLE, Region, glb, mesh_glb, skeleton_glb
from openfesl.formats.refractor import (
    Animation,
    Material,
    Node,
    Quaternion,
    RigBone,
    Skeleton,
    Track,
    Vector3,
    Vertex,
    read_animation,
    read_skeleton,
    read_skinned_mesh,
)

type Matrix = list[list[float]]

FACING = (0.0, 0.0, -1.0)
SKELETON = Skeleton(
    (
        Node("root", None, (0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0)),
        Node("hips", 0, (0.1, 0.2, 0.3, math.sqrt(0.86)), (0.1, 0.9, 0.05)),
        Node("arm", 1, (0.0, 0.6, 0.0, 0.8), (0.3, 0.2, -0.1)),
        Node("leg", 1, (0.0, 0.0, 0.6, 0.8), (0.0, -0.4, 0.0)),
        Node("spare", 0, (0.0, 0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    )
)


def parse(data: bytes) -> tuple[dict[str, Any], bytes]:
    magic, version, length, text_length, kind = struct.unpack_from("<4s4I", data)
    assert (magic, version, length, kind) == (b"glTF", 2, len(data), 0x4E4F534A)
    return json.loads(data[20 : 20 + text_length]), data[28 + text_length :]


def values(gltf: tuple[dict[str, Any], bytes], index: int) -> list[tuple[float, ...]]:
    document, binary = gltf
    accessor = document["accessors"][index]
    view = document["bufferViews"][accessor["bufferView"]]
    code = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
    size = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[accessor["type"]]
    element = struct.Struct(f"<{size}{code[accessor['componentType']]}")
    stride = view.get("byteStride", element.size)
    return [
        element.unpack_from(binary, view["byteOffset"] + number * stride)
        for number in range(accessor["count"])
    ]


def rotation(quaternion: Quaternion | list[float]) -> Matrix:
    x, y, z, w = quaternion
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def product(a: Matrix, b: Matrix) -> Matrix:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def engine_rig(skeleton: Skeleton, node: int) -> tuple[float, ...]:
    # What the game's exporter stores: the inverse of the node's rest pose for row vectors,
    # each node's matrix holding its stored quaternion's rotation with its position below.
    world = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    index: int | None = node
    while index is not None:
        rest = skeleton.nodes[index]
        local = [[*row, 0.0] for row in rotation(rest.rotation)] + [[*rest.position, 1.0]]
        world = product(world, local)
        index = rest.parent
    turn = [row[:3] for row in world[:3]]
    moved = [-sum(world[3][k] * turn[j][k] for k in range(3)) for j in range(3)]
    inverse = [[turn[j][i] for j in range(3)] + [0.0] for i in range(3)] + [[*moved, 1.0]]
    return tuple(value for row in inverse for value in row)


def gltf_worlds(document: dict[str, Any]) -> list[Matrix]:
    nodes = document["nodes"]
    parents = {
        child: index for index, node in enumerate(nodes) for child in node.get("children", [])
    }
    worlds: list[Matrix] = []
    for index, node in enumerate(nodes):
        x, y, z = node.get("translation", (0.0, 0.0, 0.0))
        turn = rotation(node.get("rotation", (0.0, 0.0, 0.0, 1.0)))
        local = [turn[0] + [x], turn[1] + [y], turn[2] + [z], [0.0, 0.0, 0.0, 1.0]]
        worlds.append(local if index not in parents else product(worlds[parents[index]], local))
    return worlds


def furthest_from_rest(gltf: tuple[dict[str, Any], bytes]) -> float:
    # How far each joint's world matrix times its inverse bind matrix is from the identity.
    document = gltf[0]
    worlds = gltf_worlds(document)
    skin = document["skins"][0]
    furthest = 0.0
    for joint, stored in zip(
        skin["joints"], values(gltf, skin["inverseBindMatrices"]), strict=True
    ):
        inverse = [[stored[column * 4 + row] for column in range(4)] for row in range(4)]
        rest = product(worlds[joint], inverse)
        furthest = max(furthest, *(abs(rest[i][j] - (i == j)) for i in range(4) for j in range(4)))
    return furthest


def facing_share(gltf: tuple[dict[str, Any], bytes]) -> float:
    # The share of triangles whose corners turn counter-clockwise seen from where their
    # normals point.
    facing = total = 0
    for primitive in gltf[0]["meshes"][0]["primitives"]:
        positions = values(gltf, primitive["attributes"]["POSITION"])
        normals = values(gltf, primitive["attributes"]["NORMAL"])
        corners = [int(index) for (index,) in values(gltf, primitive["indices"])]
        for start in range(0, len(corners), 3):
            a, b, c = (positions[index] for index in corners[start : start + 3])
            u, v = [b[k] - a[k] for k in range(3)], [c[k] - a[k] for k in range(3)]
            face = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            normal = [sum(normals[i][k] for i in corners[start : start + 3]) for k in range(3)]
            facing += sum(f * n for f, n in zip(face, normal, strict=True)) > 0
            total += 1
    return facing / total


def vertex(
    position: Vector3, uv: tuple[float, float], weight: float, bones: tuple[int, int]
) -> Vertex:
    return Vertex(position, FACING, uv, weight, bones)


def test_a_glb_pads_its_document_with_spaces_and_its_binary_with_zeros() -> None:
    document = {"asset": {"version": "2.0"}}

    assert glb(document, b"\1\2\3") == (
        b"glTF\2\0\0\0\x58\0\0\0"
        + b'\x38\0\0\0JSON{"asset":{"version":"2.0"},"buffers":[{"byteLength":3}]}'
        + b"\4\0\0\0BIN\0\1\2\3\0"
    )
    assert glb(document, b"") == b'glTF\2\0\0\0\x30\0\0\0\x1c\0\0\0JSON{"asset":{"version":"2.0"}} '


def test_a_mesh_keeps_the_vertices_and_joints_it_uses_facing_where_its_normals_point() -> None:
    # Seen from -Z, where they face, the triangles' corners turn clockwise, as the game's do.
    skinned = Material(
        "SoldierItem.fx",
        "Invented",
        (),
        (RigBone(2, engine_rig(SKELETON, 2)), RigBone(1, engine_rig(SKELETON, 1))),
        (
            vertex((9.0, 9.0, 9.0), (0.5, 0.5), 1.0, (1, 1)),
            vertex((0.0, 1.0, 0.0), (0.0, 0.0), 1.0, (0, 0)),
            vertex((0.0, 2.0, 0.0), (1.0, 0.0), 0.25, (0, 1)),
            vertex((1.0, 1.0, 0.0), (0.0, 1.0), 1.0, (1, 0)),
        ),
        ((1, 2, 3),),
    )
    boneless = Material(
        "SoldierItem.fx",
        "Invented",
        (),
        (),
        tuple(vertex(p, (0.0, 0.0), 1.0, (0, 0)) for p in ((0, 0, 0), (0, 1, 0), (1, 0, 0))),
        ((0, 1, 2),),
    )
    empty = Material("SoldierItem.fx", "Invented", (), (), (), ())

    gltf = parse(
        mesh_glb("bag", (skinned, empty, boneless), SKELETON, Region(0.5, 0.25, 0.25, 0.5))
    )

    document = gltf[0]
    assert document["extensionsRequired"] == ["KHR_mesh_quantization"]
    names = [node["name"] for node in document["nodes"]]
    assert names == ["root", "hips", "arm", "bag"]
    assert [names[joint] for joint in document["skins"][0]["joints"]] == ["root", "hips", "arm"]
    first, second = document["meshes"][0]["primitives"]
    attributes = first["attributes"]
    assert values(gltf, attributes["POSITION"]) == [(0, 1, 0), (0, 2, 0), (-1, 1, 0)]
    assert values(gltf, attributes["NORMAL"]) == [(0, 0, -127)] * 3
    uvs = [value / 65535 for uv in values(gltf, attributes["TEXCOORD_0"]) for value in uv]
    assert uvs == pytest.approx([0.5, 0.25, 0.75, 0.25, 0.5, 0.75], abs=1e-5)
    assert values(gltf, attributes["JOINTS_0"]) == [(2, 0, 0, 0), (2, 1, 0, 0), (1, 0, 0, 0)]
    assert values(gltf, attributes["WEIGHTS_0"]) == [
        (255, 0, 0, 0),
        (64, 191, 0, 0),
        (255, 0, 0, 0),
    ]
    assert values(gltf, second["attributes"]["JOINTS_0"]) == [(0, 0, 0, 0)] * 3
    assert facing_share(gltf) == 1.0


def test_a_mesh_at_rest_lies_where_the_skeleton_at_rest_puts_it() -> None:
    rig = tuple(RigBone(node, engine_rig(SKELETON, node)) for node in (1, 2, 3))
    points = tuple(vertex((0.0, float(n), 0.0), (0.0, 0.0), 1.0, (n, n)) for n in range(3))
    lod = (Material("SoldierItem.fx", "Invented", (), rig, points, ((0, 1, 2),)),)

    assert furthest_from_rest(parse(mesh_glb("body", lod, SKELETON))) < 1e-5


def test_a_skeleton_carries_its_clips_with_one_key_for_what_stays_still() -> None:
    turning = tuple((0.0, s, 0.0, math.sqrt(1 - s * s)) for s in (0.0, 0.5, 1.0))
    clip = Animation(
        3,
        (
            Track(0, ((0.0, 0.0, 0.0, 1.0),) * 3, ((0.5, 0.0, 0.0),) * 3),
            Track(2, turning, ((0.0, 1.0, 0.0),) * 3),
            # A node past the skeleton's, which only some of the game's clips move.
            Track(9, turning, ((0.0, 1.0, 0.0),) * 3),
        ),
    )

    gltf = parse(skeleton_glb(SKELETON, {"wave": clip}, 30.0))

    document = gltf[0]
    assert [node["name"] for node in document["nodes"]] == [node.name for node in SKELETON.nodes]
    assert document["nodes"][1]["translation"] == [-0.1, 0.9, 0.05]
    assert document["nodes"][2]["rotation"] == [-0.0, 0.6, 0.0, 0.8]
    (animation,) = document["animations"]
    assert animation["name"] == "wave"
    keys = {
        (channel["target"]["node"], channel["target"]["path"]): animation["samplers"][
            channel["sampler"]
        ]
        for channel in animation["channels"]
    }
    assert sorted(keys) == [
        (0, "rotation"),
        (0, "translation"),
        (2, "rotation"),
        (2, "translation"),
    ]
    times = [time for (time,) in values(gltf, keys[2, "rotation"]["input"])]
    assert times == pytest.approx([0.0, 1 / 30, 2 / 30])
    assert values(gltf, keys[0, "translation"]["input"]) == [(0.0,)]
    assert values(gltf, keys[0, "translation"]["output"]) == [(-0.5, 0.0, 0.0)]
    # Rotations as 16-bit fractions, with only X of the axis mirrored.
    assert values(gltf, keys[2, "rotation"]["output"]) == [
        (0, 0, 0, 32767),
        (0, 16384, 0, 28377),
        (0, 32767, 0, 0),
    ]


@NEEDS_COPY
def test_the_copys_hero_items_lie_on_their_skeleton_facing_out() -> None:
    assert COPY
    objects = Path(COPY) / "mods" / "bfheroes" / "Objects"
    skeletons = {}
    with ZipFile(objects / "Soldiers_server.zip") as soldiers:
        files = {name.lower(): name for name in soldiers.namelist()}
        for faction in ("british", "german"):
            folder = f"{faction}_male_soldier"
            skeleton = read_skeleton(soldiers.read(files[f"{folder}/{folder}.ske"]))
            idle = read_animation(
                soldiers.read(files[f"{folder}/animations/common/frontend_idle.baf"])
            )
            assert parse(skeleton_glb(skeleton, {"idle": idle}, 30.0))[0]["animations"]
            skeletons[faction] = skeleton
    at_rest, meshes, facing = 0, 0, []
    with ZipFile(objects / "Items_client.zip") as items:
        for info in items.infolist():
            faction = info.filename.split("/")[0].lower()
            if not info.filename.lower().endswith(".skinnedmesh") or faction not in skeletons:
                continue
            lod = read_skinned_mesh(items.read(info)).geometries[0][0]
            gltf = parse(mesh_glb(info.filename, lod, skeletons[faction], WHOLE))
            meshes += 1
            at_rest += furthest_from_rest(gltf) < 1e-3
            facing.append(facing_share(gltf))
    # A few items were bound to a skeleton slightly unlike the game's, and a few triangles of
    # some face the other way than their normals.
    assert at_rest / meshes > 0.98
    assert sum(facing) / meshes > 0.99
