import struct
from dataclasses import dataclass
from typing import Any

type Vector3 = tuple[float, float, float]
type Quaternion = tuple[float, float, float, float]

_SKINNED_MESH_VERSIONS = (4, 11)
# Version 4 stores a pivot after each level's bounds, which later versions dropped.
_PIVOT_VERSION = 4
# The vertex declaration of every skinned mesh of the game, as Direct3D 9's flag, offset, type and
# usage of each element: position, normal, blend weight, blend indices, uv and tangent, then the
# declaration's end.
_DECLARATION = (
    (0, 0, 2, 0),
    (0, 12, 2, 3),
    (0, 24, 0, 1),
    (0, 28, 4, 2),
    (0, 32, 1, 5),
    (0, 40, 2, 6),
    (255, 0, 17, 0),
)
# Such a vertex without its tangent; one weight blends two bones, so two blend index bytes are read.
_VERTEX = struct.Struct("<3f3ffBB2x2f12x")
# The value every mesh of the game stores before its vertices' size.
_VERTEX_FORMAT = 4
_SKELETON_VERSION = 2
_ANIMATION_VERSION = 4
# An animation's channels per node: a rotation's x, y, z and w, then a position's x, y and z.
_CHANNELS = 7
_ROTATION_CHANNELS = 4
_REPEAT = 0x80


@dataclass(frozen=True, slots=True)
class Vertex:
    """A point of a skinned mesh, in the engine's left-handed space with Y up.

    Attributes:
        position: Where it lies, in metres.
        normal: The direction its surface faces.
        uv: Where it lies on its texture, in fractions of the texture from its top left.
        weight: How much its first bone moves it; its second bone moves it by the rest.
        bones: Its first and second bone, each as an index into its material's rig.
    """

    position: Vector3
    normal: Vector3
    uv: tuple[float, float]
    weight: float
    bones: tuple[int, int]


@dataclass(frozen=True, slots=True)
class RigBone:
    """A skeleton node that moves a material's vertices.

    Attributes:
        node: The node's index in the skeleton.
        matrix: The matrix that brings the mesh's space into the node's at rest: 16 values,
            row by row, for row vectors multiplied on its left.
    """

    node: int
    matrix: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Material:
    """A part of a mesh that the engine draws in one call.

    Attributes:
        effect: The shader effect's file.
        technique: The effect's technique.
        textures: The textures' files.
        rig: The bones that its vertices name by index; none when it has no bones.
        vertices: Its vertices, also those that no triangle uses.
        triangles: Its triangles as indices into its vertices, each clockwise seen from the
            side it faces.
    """

    effect: str
    technique: str
    textures: tuple[str, ...]
    rig: tuple[RigBone, ...]
    vertices: tuple[Vertex, ...]
    triangles: tuple[tuple[int, int, int], ...]


type Lod = tuple[Material, ...]
"""A level of detail of a geometry: the materials drawn together."""


@dataclass(frozen=True, slots=True)
class SkinnedMesh:
    """A .skinnedmesh file.

    Attributes:
        geometries: Its geometries, each with its levels of detail from the finest.
    """

    geometries: tuple[tuple[Lod, ...], ...]


@dataclass(frozen=True, slots=True)
class Node:
    """A node of a skeleton in its rest pose, relative to its parent.

    Attributes:
        name: Its name.
        parent: Its parent's index in the skeleton, or None for a root.
        rotation: Its rotation as a quaternion x, y, z and w, as stored.
        position: Its position.
    """

    name: str
    parent: int | None
    rotation: Quaternion
    position: Vector3


@dataclass(frozen=True, slots=True)
class Skeleton:
    """A .ske file.

    Attributes:
        nodes: Its nodes, each after its parent.
    """

    nodes: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class Track:
    """The motion of one skeleton node, frame by frame, relative to the node's parent.

    Attributes:
        node: The node's index in the skeleton.
        rotations: Its rotation as a quaternion x, y, z and w at each frame.
        positions: Its position at each frame.
    """

    node: int
    rotations: tuple[Quaternion, ...]
    positions: tuple[Vector3, ...]


@dataclass(frozen=True, slots=True)
class Animation:
    """A .baf file.

    Attributes:
        frames: How many frames it has.
        tracks: The motion of each node it moves.
    """

    frames: int
    tracks: tuple[Track, ...]


class _Reader:
    def __init__(self, data: bytes, kind: str) -> None:
        self._data = data
        self._at = 0
        self._kind = kind

    def unpack(self, form: str) -> tuple[Any, ...]:
        end = self._at + struct.calcsize(form)
        if end > len(self._data):
            raise ValueError(f"the {self._kind} ends before byte {end}")
        values = struct.unpack_from(form, self._data, self._at)
        self._at = end
        return values

    def number(self, form: str = "<I") -> int:
        return int(self.unpack(form)[0])

    def take(self, size: int) -> bytes:
        return bytes(self.unpack(f"{size}s")[0])

    def text(self) -> str:
        return self.take(self.number()).decode("latin-1")

    def finish(self) -> None:
        if self._at != len(self._data):
            raise ValueError(
                f"the {self._kind} has {len(self._data) - self._at} bytes after its end"
            )


def read_skinned_mesh(data: bytes) -> SkinnedMesh:
    """Read a .skinnedmesh file of version 11, or of version 4 as the oldest meshes are.

    Args:
        data: The file.

    Raises:
        ValueError: When the file is no skinned mesh of those versions, its vertices are laid
            out in another way, or its parts do not fit together.
    """
    reader = _Reader(data, "skinned mesh")
    # Of the header's five numbers only the version varies; newer meshes set the flag byte after
    # it, which changes nothing that follows.
    _, version, _, _, _, _ = reader.unpack("<5IB")
    if version not in _SKINNED_MESH_VERSIONS:
        raise ValueError(f"the skinned mesh has version {version}")
    lod_counts = [reader.number() for _ in range(reader.number())]
    declaration = tuple(reader.unpack("<4H") for _ in range(reader.number()))
    vertex_format, stride, count = reader.unpack("<3I")
    if declaration != _DECLARATION or (vertex_format, stride) != (_VERTEX_FORMAT, _VERTEX.size):
        raise ValueError("the skinned mesh's vertices are laid out in another way")
    vertices = [
        Vertex((x, y, z), (nx, ny, nz), (u, v), weight, (first, second))
        for x, y, z, nx, ny, nz, weight, first, second, u, v in _VERTEX.iter_unpack(
            reader.take(stride * count)
        )
    ]
    indices = reader.unpack(f"<{reader.number()}H")
    rigs = [[_rigs(reader, version) for _ in range(lods)] for lods in lod_counts]
    geometries = tuple(
        tuple(_lod(reader, lod_rigs, vertices, indices) for lod_rigs in geometry)
        for geometry in rigs
    )
    reader.finish()
    return SkinnedMesh(geometries)


def _rigs(reader: _Reader, version: int) -> list[tuple[RigBone, ...]]:
    reader.unpack("<6f" if version > _PIVOT_VERSION else "<9f")
    return [
        tuple(RigBone(reader.number(), reader.unpack("<16f")) for _ in range(reader.number()))
        for _ in range(reader.number())
    ]


def _lod(
    reader: _Reader,
    rigs: list[tuple[RigBone, ...]],
    vertices: list[Vertex],
    indices: tuple[int, ...],
) -> Lod:
    materials = []
    count = reader.number()
    # Each material has its own rig, except in version 4, where they share one.
    if len(rigs) != count and len(rigs) != 1:
        raise ValueError(f"the skinned mesh has {count} materials with {len(rigs)} rigs")
    for number in range(count):
        effect, technique = reader.text(), reader.text()
        textures = tuple(reader.text() for _ in range(reader.number()))
        # Two numbers of no known use follow the ranges; some meshes hold arbitrary values there.
        first_vertex, first_index, index_count, vertex_count, _, _ = reader.unpack("<6I")
        rig = rigs[min(number, len(rigs) - 1)]
        own = vertices[first_vertex : first_vertex + vertex_count]
        used = indices[first_index : first_index + index_count]
        if len(own) != vertex_count or len(used) != index_count or index_count % 3:
            raise ValueError(f"material {number} of the skinned mesh lies outside its buffers")
        if any(index >= vertex_count for index in used):
            raise ValueError(f"material {number} of the skinned mesh names a vertex it lacks")
        # A material without bones names only the first, which it lacks.
        if any(bone >= max(len(rig), 1) for vertex in own for bone in vertex.bones):
            raise ValueError(f"material {number} of the skinned mesh names a bone its rig lacks")
        triangles = tuple(zip(used[::3], used[1::3], used[2::3], strict=True))
        materials.append(Material(effect, technique, textures, rig, tuple(own), triangles))
    return tuple(materials)


def read_skeleton(data: bytes) -> Skeleton:
    """Read a .ske file of version 2.

    Args:
        data: The file.

    Raises:
        ValueError: When the file is no skeleton of that version, or a node comes before its
            parent.
    """
    reader = _Reader(data, "skeleton")
    if (version := reader.number()) != _SKELETON_VERSION:
        raise ValueError(f"the skeleton has version {version}")
    nodes = []
    for index in range(reader.number()):
        name = reader.take(reader.number("<H")).rstrip(b"\0").decode("ascii")
        parent = reader.number("<h")
        if not -1 <= parent < index:
            raise ValueError(f"node {name} of the skeleton comes before its parent")
        rotation, position = reader.unpack("<4f"), reader.unpack("<3f")
        nodes.append(Node(name, None if parent < 0 else parent, rotation, position))
    reader.finish()
    return Skeleton(tuple(nodes))


def read_animation(data: bytes) -> Animation:
    """Read a .baf file of version 4.

    Each node states the size of its channels in 16-bit words, and each channel its own. A
    channel is runs of 16-bit fixed-point values, each run either stored whole or one value
    repeated, after a byte with its frame count and whether it repeats and one with its size.

    Args:
        data: The file.

    Raises:
        ValueError: When the file is no animation of that version, or a channel's runs do not
            fill its frames or its stated size.
    """
    reader = _Reader(data, "animation")
    if (version := reader.number()) != _ANIMATION_VERSION:
        raise ValueError(f"the animation has version {version}")
    nodes = reader.unpack(f"<{reader.number('<H')}H")
    frames, precision = reader.unpack("<IB")
    tracks = []
    for node in nodes:
        size = reader.number("<H")
        channels = []
        for channel in range(_CHANNELS):
            left = reader.number("<H")
            size -= left
            # Rotations use all 15 bits for their fraction, positions the precision's.
            factor = 2 ** (15 - (15 if channel < _ROTATION_CHANNELS else precision))
            values: list[float] = []
            while left > 0 and len(values) < frames:
                head, step = reader.unpack("<2B")
                run = head & ~_REPEAT
                stored = reader.unpack(f"<{1 if head & _REPEAT else run}h")
                values += [value * factor / 32767 for value in stored] * (
                    run if head & _REPEAT else 1
                )
                left -= step
            if len(values) != frames or left:
                raise ValueError(f"channel {channel} of node {node} of the animation is broken")
            channels.append(values)
        if size:
            raise ValueError(f"node {node} of the animation is not as long as it states")
        x, y, z, w, px, py, pz = channels
        tracks.append(
            Track(
                node,
                tuple(zip(x, y, z, w, strict=True)),
                tuple(zip(px, py, pz, strict=True)),
            )
        )
    reader.finish()
    return Animation(frames, tuple(tracks))
