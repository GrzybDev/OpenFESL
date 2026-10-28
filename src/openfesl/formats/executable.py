import re
import struct
from collections import defaultdict
from dataclasses import dataclass

_PE32 = 0x10B
_CODE = 0x20
# How far after the code that names a string the instruction reading the table may sit.
_TABLE_READ_DISTANCE = 0x100
# mov eax, [index * 4 + table]: opcode, ModRM and a SIB byte with any index register but esp.
_TABLE_READ = re.compile(rb"\x8b\x04[\x85\x8d\x95\x9d\xad\xb5\xbd](.{4})", re.DOTALL)
# mov ecx, esi; call rel32: a method called on the object in esi.
_METHOD_CALL = b"\x8b\xce\xe8"
# The most bytes between two calls of one run: their two pushes and the call itself.
_RUN_GAP = 16


@dataclass(frozen=True, slots=True)
class _Section:
    address: int
    offset: int
    stored: int
    code: bool


class Executable:
    """A 32-bit Windows executable, read as it lies in memory once loaded."""

    def __init__(self, data: bytes) -> None:
        """Read the sections of an executable.

        Args:
            data: The executable's file.

        Raises:
            ValueError: When the file is no 32-bit Windows executable.
        """
        self._data = data
        self._sections = []
        try:
            header = struct.unpack_from("<I", data, 0x3C)[0]
            signature, sections, optional_size = struct.unpack_from("<4s2xH12xH", data, header)
            magic = struct.unpack_from("<H", data, header + 24)[0]
            base = struct.unpack_from("<I", data, header + 52)[0]
            if data[:2] != b"MZ" or signature != b"PE\0\0" or magic != _PE32:
                raise ValueError("the file is no 32-bit Windows executable")
            table = header + 24 + optional_size
            for index in range(sections):
                entry = table + index * 40
                address, stored, offset = struct.unpack_from("<3I", data, entry + 12)
                flags = struct.unpack_from("<I", data, entry + 36)[0]
                self._sections.append(_Section(base + address, offset, stored, bool(flags & _CODE)))
        except struct.error:
            raise ValueError("the file is no Windows executable") from None

    def int32s(self, address: int, count: int) -> list[int]:
        """Return the signed 32-bit numbers that lie in a row at an address.

        Args:
            address: The address of the first.
            count: How many.

        Raises:
            ValueError: When the file does not hold them all.
        """
        offset = self._offset(address)
        if offset + 4 * count > len(self._data):
            raise ValueError(f"the file ends before {count} numbers at {address:#x}")
        return list(struct.unpack_from(f"<{count}i", self._data, offset))

    def table_read_after(self, text: str) -> int:
        """Return the address of the table that the code naming a string reads right after.

        Such code pushes the string's address, as an assertion that checks the table's index
        does, and a few bytes on reads the table at that index times four.

        Args:
            text: The string, which the file holds as ASCII ending in a zero byte.

        Raises:
            ValueError: When the file holds no such string, no code pushes it or no table read
                follows.
        """
        stored = self._data.find(text.encode("ascii") + b"\0")
        if stored < 0:
            raise ValueError(f"the file holds no string {text!r}")
        push = self._data.find(b"\x68" + struct.pack("<I", self._address(stored)))
        if push < 0:
            raise ValueError(f"no code names the string {text!r}")
        read = _TABLE_READ.search(self._data, push, push + _TABLE_READ_DISTANCE)
        if read is None:
            raise ValueError(f"no table is read after the string {text!r}")
        return int(struct.unpack("<I", read.group(1))[0])

    def longest_call_run(self) -> list[tuple[int | None, int | None]]:
        """Return the arguments of the longest run of calls of one method, each with two pushed.

        A run is calls of one method on the object in esi, a few bytes apart, such as code
        that fills a table one entry per call.

        Returns:
            Each call's two arguments, the one pushed last first, in the order of the calls;
            None for an argument pushed from a register.
        """
        runs: dict[int, list[list[int]]] = defaultdict(list)
        for section in self._sections:
            if not section.code:
                continue
            start, end = section.offset, section.offset + section.stored
            site = self._data.find(_METHOD_CALL, start, end)
            while site >= 0:
                (relative,) = struct.unpack_from("<i", self._data, site + 3)
                calls = runs[site + 7 + relative]
                if calls and site - calls[-1][-1] <= _RUN_GAP:
                    calls[-1].append(site)
                else:
                    calls.append([site])
                site = self._data.find(_METHOD_CALL, site + 1, end)
        longest = max((run for calls in runs.values() for run in calls), key=len, default=[])
        arguments = []
        for site in longest:
            first, before = self._pushed(site)
            second, _ = self._pushed(before)
            arguments.append((first, second))
        return arguments

    def _pushed(self, end: int) -> tuple[int | None, int]:
        # The push that ends where the next instruction starts, and where it starts.
        data = self._data
        if data[end - 5] == 0x68:
            return struct.unpack_from("<i", data, end - 4)[0], end - 5
        if data[end - 2] == 0x6A:
            return struct.unpack_from("<b", data, end - 1)[0], end - 2
        if 0x50 <= data[end - 1] <= 0x57:
            return None, end - 1
        raise ValueError(f"no push ends at offset {end:#x}")

    def _offset(self, address: int) -> int:
        for section in self._sections:
            inside = address - section.address
            if 0 <= inside < section.stored:
                return section.offset + inside
        raise ValueError(f"the file holds nothing at {address:#x}")

    def _address(self, offset: int) -> int:
        for section in self._sections:
            inside = offset - section.offset
            if 0 <= inside < section.stored:
                return section.address + inside
        raise ValueError(f"offset {offset:#x} lies in no section")
