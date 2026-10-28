import struct

from openfesl.formats.refractor import Vertex

DECLARATION = (
    (0, 0, 2, 0),
    (0, 12, 2, 3),
    (0, 24, 0, 1),
    (0, 28, 4, 2),
    (0, 32, 1, 5),
    (0, 40, 2, 6),
    (255, 0, 17, 0),
)
IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
SHIFTED = (*IDENTITY[:12], 0.5, -1.0, 0.25, 1.0)
VERTICES = [
    Vertex((9.0, 9.0, 9.0), (1.0, 0.0, 0.0), (1.0, 1.0), 0.75, (1, 0)),
    Vertex((0.0, 1.0, 0.0), (0.0, 0.0, -1.0), (0.0, 0.5), 1.0, (0, 0)),
    Vertex((0.0, 2.0, 0.0), (0.0, 0.0, -1.0), (0.0, 0.0), 0.5, (0, 0)),
    Vertex((1.0, 1.0, 0.0), (0.0, 0.0, -1.0), (0.5, 0.5), 1.0, (0, 0)),
]


def u32(*values: int) -> bytes:
    return struct.pack(f"<{len(values)}I", *values)


def text(value: str) -> bytes:
    return u32(len(value)) + value.encode()


def vertices(points: list[Vertex]) -> bytes:
    # Each with its two unread blend index bytes and its tangent.
    return b"".join(
        struct.pack("<3f3ff4B2f", *p.position, *p.normal, p.weight, *p.bones, 0, 0, *p.uv)
        + struct.pack("<3f", 0.0, 1.0, 0.0)
        for p in points
    )


def rigs(*bones: list[tuple[int, tuple[float, ...]]], pivot: bool = False) -> bytes:
    bounds = struct.pack(f"<{9 if pivot else 6}f", *range(9 if pivot else 6))
    return (
        bounds
        + u32(len(bones))
        + b"".join(
            u32(len(rig)) + b"".join(u32(node) + struct.pack("<16f", *m) for node, m in rig)
            for rig in bones
        )
    )


def material(textures: list[str], ranges: tuple[int, int, int, int]) -> bytes:
    # Two numbers of no known use, which some meshes fill with arbitrary values.
    junk = u32(0xDEADBEEF, 0x0BADF00D)
    names = u32(len(textures)) + b"".join(text(name) for name in textures)
    return text("SoldierItem.fx") + text("Invented") + names + u32(*ranges) + junk


def skinned_mesh(
    version: int,
    flag: int,
    lods: list[int],
    indices: list[int],
    body: bytes,
    declaration: tuple[tuple[int, ...], ...] = DECLARATION,
) -> bytes:
    header = u32(0, version, 0, 0, 0) + bytes([flag]) + u32(len(lods), *lods)
    layout = u32(len(declaration)) + b"".join(struct.pack("<4H", *e) for e in declaration)
    points = u32(4, 52, len(VERTICES)) + vertices(VERTICES)
    triangles = u32(len(indices)) + struct.pack(f"<{len(indices)}H", *indices)
    return header + layout + points + triangles + body


def skeleton(nodes: list[tuple[str, int]]) -> bytes:
    data = u32(2, len(nodes))
    for number, (name, parent) in enumerate(nodes):
        raw = name.encode() + b"\0"
        data += struct.pack("<H", len(raw)) + raw + struct.pack("<h", parent)
        data += struct.pack("<7f", 0.0, 0.0, 0.0, 1.0, float(number), 0.5, 0.0)
    return data


def channel(*runs: tuple[bool, list[int]], frames: int) -> bytes:
    # Each run: whether it repeats its one value over the frames, and its values.
    data = b""
    for repeat, values in runs:
        count = frames if repeat else len(values)
        data += bytes([count | (0x80 if repeat else 0), 1 + len(values)])
        data += struct.pack(f"<{len(values)}h", *values)
    return struct.pack("<H", len(data) // 2) + data
