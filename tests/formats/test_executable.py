import struct

import pytest
from game_files import CODE, DATA, level_table_code, table_read_code, windows_executable

from openfesl.formats.executable import Executable

# An invented check and table: the code pushes the check's text, then reads the table.
CHECK = b"index < 4\0"
TABLE = [70, -1, 72, 73]


def test_a_table_is_found_by_the_check_whose_text_the_code_pushes_before_reading_it() -> None:
    padding = b"\0" * 6
    data = CHECK + padding + struct.pack("<4i", *TABLE)
    code = table_read_code(DATA, DATA + len(CHECK) + len(padding))
    executable = Executable(windows_executable(code, data))

    assert executable.int32s(executable.table_read_after("index < 4"), 4) == TABLE
    with pytest.raises(ValueError, match="no string"):
        executable.table_read_after("index < 5")
    # The text alone, with no code that pushes it.
    unread = Executable(windows_executable(b"\xc3", data))
    with pytest.raises(ValueError, match="no code"):
        unread.table_read_after("index < 4")


def test_the_longest_run_of_calls_gives_each_calls_two_pushed_arguments() -> None:
    experience = [0, 600, 1_200, 70_000, 286_400]
    shorter = level_table_code([5, 6], CODE)
    code = shorter + level_table_code(experience, CODE + len(shorter))

    calls = Executable(windows_executable(code, b"\0" * 4)).longest_call_run()

    # The third call pushes its level from a register.
    assert calls == [(0, 0), (1, 600), (None, 1_200), (3, 70_000), (4, 286_400)]


def test_only_a_32_bit_windows_executable_is_read() -> None:
    image = bytearray(windows_executable(b"\xc3", b"\0"))
    with pytest.raises(ValueError, match="no Windows executable"):
        Executable(b"MZ")
    image[0x40 + 24] = 0x0B
    image[0x40 + 25] = 0x02
    with pytest.raises(ValueError, match="32-bit"):
        Executable(bytes(image))
