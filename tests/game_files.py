import struct

# Where the invented executables' sections load.
BASE = 0x400000
CODE = BASE + 0x1000
DATA = BASE + 0x8000
_ALIGNMENT = 0x200


def windows_executable(code: bytes, data: bytes) -> bytes:
    """Return a 32-bit Windows executable with a code section at CODE and a data one at DATA."""
    header = 0x40
    optional = bytearray(0xE0)
    struct.pack_into("<H", optional, 0, 0x10B)
    struct.pack_into("<I", optional, 28, BASE)
    code_at = _ALIGNMENT
    data_at = code_at + _aligned(len(code))
    sections = b"".join(
        struct.pack("<8s6I2HI", name, len(body), address - BASE, len(body), at, 0, 0, 0, 0, flags)
        for name, body, address, at, flags in (
            (b".text", code, CODE, code_at, 0x60000020),
            (b".data", data, DATA, data_at, 0xC0000040),
        )
    )
    image = bytearray(b"MZ" + bytes(header - 2))
    struct.pack_into("<I", image, 0x3C, header)
    image += b"PE\0\0" + struct.pack("<2H3I2H", 0x14C, 2, 0, 0, 0, len(optional), 0x102)
    image += optional + sections
    image += bytes(code_at - len(image)) + code
    image += bytes(data_at - len(image)) + data
    return bytes(image + bytes(_aligned(len(image)) - len(image)))


def push(value: int) -> bytes:
    """Return the instruction that pushes a constant, in its short form when it fits."""
    if -128 <= value < 128:
        return b"\x6a" + struct.pack("<b", value)
    return b"\x68" + struct.pack("<i", value)


def method_call(site: int, target: int) -> bytes:
    """Return a call of a method on the object in esi, made at an address of the code."""
    return b"\x8b\xce\xe8" + struct.pack("<i", target - (site + 7))


def level_table_code(experience: list[int], start: int = CODE) -> bytes:
    """Return code that sets each level's XP with one call, the level of 2 pushed from ebx."""
    code = bytearray(b"\x90" * 16)
    for level, xp in enumerate(experience):
        code += push(xp) + (b"\x53" if level == 2 else push(level))
        code += method_call(start + len(code), start)
    return bytes(code + b"\xc3")


def table_read_code(string: int, table: int) -> bytes:
    """Return code that pushes a string's address, then reads a table at index esi times four."""
    return (
        b"\x68"
        + struct.pack("<I", string)
        + b"\x90" * 9
        + b"\x8b\x04\xb5"
        + struct.pack("<I", table)
    )


def _aligned(size: int) -> int:
    return -(-size // _ALIGNMENT) * _ALIGNMENT
