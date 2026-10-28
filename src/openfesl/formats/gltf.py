import json
import math
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from openfesl.formats.refractor import (
    Animation,
    Lod,
    Material,
    Quaternion,
    Skeleton,
    Vector3,
    Vertex,
)

_MAGIC, _JSON, _BIN = 0x46546C67, 0x4E4F534A, 0x004E4942
_COMPONENTS = {"b": 5120, "B": 5121, "h": 5122, "H": 5123, "I": 5125, "f": 5126}
_VERTICES, _INDICES = 34962, 34963
# Normals and uv coordinates stored as normalized integers, which core glTF does not allow.
_QUANTIZATION = "KHR_mesh_quantization"
_IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


@dataclass(frozen=True, slots=True)
class Region:
    """A rectangle of a texture, in fractions of its width and height from its top left.

    Attributes:
        left: Where it starts across.
        top: Where it starts down.
        width: How wide it is.
        height: How high it is.
    """

    left: float
    top: float
    width: float
    height: float


WHOLE = Region(0.0, 0.0, 1.0, 1.0)


def glb(document: Mapping[str, Any], binary: bytes) -> bytes:
    """Return a binary glTF file of a document and the one buffer it reads from.

    Args:
        document: The glTF document without its buffers, which this adds for the binary.
        binary: The buffer's bytes; none for a document that reads none.
    """
    if binary:
        document = {**document, "buffers": [{"byteLength": len(binary)}]}
    text = json.dumps(document, separators=(",", ":")).encode()
    chunks = _chunk(_JSON, text + b" " * (-len(text) % 4))
    if binary:
        chunks += _chunk(_BIN, binary + bytes(-len(binary) % 4))
    return struct.pack("<3I", _MAGIC, 2, 12 + len(chunks)) + chunks


def skeleton_glb(skeleton: Skeleton, clips: Mapping[str, Animation], rate: float) -> bytes:
    """Return a skeleton in its rest pose with animation clips as a binary glTF file.

    A rotation or position that stays the same over a whole clip keeps one key. Tracks of
    nodes that the skeleton lacks are left out.

    Args:
        skeleton: The skeleton.
        clips: The animations to add, by name.
        rate: How many frames the animations play each second.

    Raises:
        ValueError: When a clip has no frames or moves none of the skeleton's nodes.
    """
    builder = _Builder()
    nodes = builder.nodes(skeleton, range(len(skeleton.nodes)))
    for name, clip in clips.items():
        if not clip.frames:
            raise ValueError(f"clip {name} has no frames")
        times = _float32([frame / rate for frame in range(clip.frames)])
        keys: dict[int, int] = {}
        channels: list[dict[str, Any]] = []
        samplers: list[dict[str, Any]] = []
        for track in clip.tracks:
            if track.node not in nodes:
                continue
            rotations = [_quantized(_rotation(rotation), 32767) for rotation in track.rotations]
            positions = [_mirrored(position) for position in track.positions]
            paths: tuple[tuple[str, Sequence[Sequence[float]], str, str], ...] = (
                ("rotation", rotations, "VEC4", "h"),
                ("translation", positions, "VEC3", "f"),
            )
            for path, values, kind, code in paths:
                kept = values[:1] if all(value == values[0] for value in values) else values
                if len(kept) not in keys:
                    keys[len(kept)] = builder.times(times[: len(kept)])
                output = builder.accessor(kind, code, kept, normalized=code == "h")
                target = {"node": nodes[track.node], "path": path}
                channels.append({"sampler": len(samplers), "target": target})
                samplers.append({"input": keys[len(kept)], "output": output})
        if not channels:
            raise ValueError(f"clip {name} moves none of the skeleton's nodes")
        builder.add("animations", {"name": name, "channels": channels, "samplers": samplers})
    return builder.glb()


def mesh_glb(name: str, lod: Lod, skeleton: Skeleton, region: Region = WHOLE) -> bytes:
    """Return a level of detail of a skinned mesh as a binary glTF file, skinned to a skeleton.

    The engine's left-handed space becomes glTF's right-handed one by mirroring X, which keeps
    a hero facing +Z with the same left and right; each triangle's corners turn
    counter-clockwise, so that it still faces out. Vertices that no triangle uses are left
    out. The skin's joints are only the nodes that the rigs name, under their ancestors, so a
    page can move the mesh with the whole skeleton by the nodes' names. Vertices of a material
    without bones follow the skeleton's first node, in whose space they lie.

    Args:
        name: The mesh's name in the file.
        lod: The level of detail.
        skeleton: The skeleton whose nodes the rigs name.
        region: Where on the texture the mesh's uv coordinates land.

    Raises:
        ValueError: When the level has no triangles or a rig names a node that the skeleton
            lacks.
    """
    drawn = [material for material in lod if material.triangles]
    if not drawn:
        raise ValueError("the mesh has no triangles")
    inverse_binds: dict[int, tuple[float, ...]] = {}
    for material in drawn:
        for bone in material.rig:
            if bone.node >= len(skeleton.nodes):
                raise ValueError(f"the mesh names node {bone.node}, which the skeleton lacks")
            inverse_binds.setdefault(bone.node, _inverse_bind(bone.matrix))
        if not material.rig:
            inverse_binds.setdefault(0, _IDENTITY)
    joints = sorted(inverse_binds)
    needed: set[int] = set()
    for joint in joints:
        index: int | None = joint
        while index is not None and index not in needed:
            needed.add(index)
            index = skeleton.nodes[index].parent
    builder = _Builder()
    nodes = builder.nodes(skeleton, sorted(needed))
    primitives = [
        builder.primitive(
            material,
            [joints.index(bone.node) for bone in material.rig] or [joints.index(0)],
            region,
        )
        for material in drawn
    ]
    matrices = builder.accessor("MAT4", "f", [inverse_binds[joint] for joint in joints])
    skin = builder.add(
        "skins", {"joints": [nodes[joint] for joint in joints], "inverseBindMatrices": matrices}
    )
    mesh = builder.add("meshes", {"name": name, "primitives": primitives})
    builder.scene.append(builder.add("nodes", {"name": name, "mesh": mesh, "skin": skin}))
    builder.document["extensionsUsed"] = builder.document["extensionsRequired"] = [_QUANTIZATION]
    return builder.glb()


class _Builder:
    def __init__(self) -> None:
        self.scene: list[int] = []
        self.document: dict[str, Any] = {
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": self.scene}],
        }
        self._binary = bytearray()

    def add(self, key: str, item: dict[str, Any]) -> int:
        items: list[dict[str, Any]] = self.document.setdefault(key, [])
        items.append(item)
        return len(items) - 1

    def accessor(
        self,
        kind: str,
        code: str,
        elements: Sequence[Sequence[float]],
        target: int | None = None,
        *,
        normalized: bool = False,
        bounds: bool = False,
    ) -> int:
        element = struct.Struct(f"<{len(elements[0])}{code}")
        # Each element of a vertex attribute starts on a multiple of four bytes.
        padding = -element.size % 4 if target == _VERTICES else 0
        view: dict[str, Any] = {
            "buffer": 0,
            "byteOffset": len(self._binary),
            "byteLength": len(elements) * (element.size + padding),
        }
        if target is not None:
            view["target"] = target
        if padding:
            view["byteStride"] = element.size + padding
        self._binary += b"".join(element.pack(*values) + bytes(padding) for values in elements)
        self._binary += bytes(-len(self._binary) % 4)
        accessor: dict[str, Any] = {
            "bufferView": self.add("bufferViews", view),
            "componentType": _COMPONENTS[code],
            "count": len(elements),
            "type": kind,
        }
        if normalized:
            accessor["normalized"] = True
        if bounds:
            accessor["min"] = [min(column) for column in zip(*elements, strict=True)]
            accessor["max"] = [max(column) for column in zip(*elements, strict=True)]
        return self.add("accessors", accessor)

    def times(self, times: list[float]) -> int:
        return self.accessor("SCALAR", "f", [(time,) for time in times], bounds=True)

    def nodes(self, skeleton: Skeleton, indices: Iterable[int]) -> dict[int, int]:
        # The nodes at the given indices, each after its parent, by their skeleton's index.
        added: dict[int, int] = {}
        for index in indices:
            node = skeleton.nodes[index]
            added[index] = self.add(
                "nodes",
                {
                    "name": node.name,
                    "rotation": list(_rotation(node.rotation)),
                    "translation": list(_mirrored(node.position)),
                },
            )
            if node.parent is None:
                self.scene.append(added[index])
            else:
                parent = self.document["nodes"][added[node.parent]]
                parent.setdefault("children", []).append(added[index])
        return added

    def primitive(self, material: Material, joints: list[int], region: Region) -> dict[str, Any]:
        # The material's triangles with the vertices they use; joints holds the skin's joint
        # of each bone in its rig.
        kept = sorted({corner for triangle in material.triangles for corner in triangle})
        places = {old: new for new, old in enumerate(kept)}
        vertices = [material.vertices[index] for index in kept]
        uvs = [
            (region.left + u * region.width, region.top + v * region.height)
            for u, v in (vertex.uv for vertex in vertices)
        ]
        skinning = [_skinning(vertex, joints) for vertex in vertices]
        # A few meshes reach past their texture; their coordinates stay floats.
        if all(0.0 <= value <= 1.0 for uv in uvs for value in uv):
            uv_accessor = self.accessor(
                "VEC2", "H", [_quantized(uv, 65535) for uv in uvs], _VERTICES, normalized=True
            )
        else:
            uv_accessor = self.accessor("VEC2", "f", uvs, _VERTICES)
        positions = [_mirrored(vertex.position) for vertex in vertices]
        normals = [_normal(vertex.normal) for vertex in vertices]
        indices = [(places[corner],) for a, b, c in material.triangles for corner in (a, c, b)]
        return {
            "attributes": {
                "POSITION": self.accessor("VEC3", "f", positions, _VERTICES, bounds=True),
                "NORMAL": self.accessor("VEC3", "b", normals, _VERTICES, normalized=True),
                "TEXCOORD_0": uv_accessor,
                "JOINTS_0": self.accessor(
                    "VEC4", "B" if max(joints) < 256 else "H", [s[0] for s in skinning], _VERTICES
                ),
                "WEIGHTS_0": self.accessor(
                    "VEC4", "B", [s[1] for s in skinning], _VERTICES, normalized=True
                ),
            },
            # WebGL 2 takes the largest index of a type to restart primitives; glTF forbids it.
            "indices": self.accessor(
                "SCALAR", "H" if len(kept) < 65535 else "I", indices, _INDICES
            ),
        }

    def glb(self) -> bytes:
        return glb(self.document, bytes(self._binary))


def _chunk(kind: int, data: bytes) -> bytes:
    return struct.pack("<2I", len(data), kind) + data


def _mirrored(vector: Vector3) -> Vector3:
    x, y, z = vector
    return -x, y, z


def _rotation(rotation: Quaternion) -> Quaternion:
    # The stored quaternion is the inverse of glTF's rotation, and mirroring X flips the Y and Z
    # of its axis, so only X changes sign.
    x, y, z, w = rotation
    length = math.hypot(x, y, z, w)
    return -x / length, y / length, z / length, w / length


def _quantized(values: Sequence[float], most: int) -> tuple[int, ...]:
    return tuple(round(value * most) for value in values)


def _normal(normal: Vector3) -> tuple[int, ...]:
    x, y, z = _mirrored(normal)
    length = math.hypot(x, y, z) or 1.0
    return _quantized((x / length, y / length, z / length), 127)


def _inverse_bind(matrix: tuple[float, ...]) -> tuple[float, ...]:
    # The rig's matrix multiplies row vectors in the engine's space; glTF's multiplies column
    # vectors in mirrored space, stored column by column, so the same order of values holds
    # its transpose, with X mirrored on both sides. Exporters left the values that make the
    # last row slightly off zero and one.
    values = []
    for index, value in enumerate(matrix):
        row, column = index % 4, index // 4
        if row == 3:
            values.append(1.0 if column == 3 else 0.0)
        else:
            values.append(-value if (row == 0) != (column == 0) else value)
    return tuple(values)


def _skinning(vertex: Vertex, joints: list[int]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    # The vertex's two joints and their weights out of 255: a joint named twice once, and a
    # joint without weight as joint 0, as glTF expects.
    first, second = (joints[bone] for bone in vertex.bones)
    if first == second:
        return (first, 0, 0, 0), (255, 0, 0, 0)
    weight = round(min(max(vertex.weight, 0.0), 1.0) * 255)
    joints_used = (first if weight else 0, second if weight < 255 else 0)
    return (*joints_used, 0, 0), (weight, 255 - weight, 0, 0)


def _float32(values: list[float]) -> list[float]:
    # Bounds must equal the values as stored.
    return list(struct.unpack(f"<{len(values)}f", struct.pack(f"<{len(values)}f", *values)))
