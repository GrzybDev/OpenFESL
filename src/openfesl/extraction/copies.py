from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile, ZipInfo


class ExtractionError(Exception):
    """Tells why a folder's copy of a game gives no catalogue."""


def path(folder: Path, relative: str) -> Path:
    """Return the path of a file of a copy of a game.

    Args:
        folder: The folder of the copy's executable.
        relative: The file's path from there, with forward slashes.
    """
    return folder.joinpath(*PurePosixPath(relative).parts)


def archive(folder: Path, relative: str) -> ZipFile:
    """Open an archive of a copy of a game.

    Args:
        folder: The folder of the copy's executable.
        relative: The archive's path from there, with forward slashes.

    Raises:
        ExtractionError: When the copy lacks the archive or it is no ZIP archive.
    """
    found = path(folder, relative)
    try:
        return ZipFile(found)
    except OSError, BadZipFile:
        raise ExtractionError(f"{found} is missing or no ZIP archive") from None


def member(opened: ZipFile, relative: str, name: str) -> bytes:
    """Return a file of an archive of a copy of a game.

    Args:
        opened: The archive.
        relative: The archive's path in the copy, which an error names.
        name: The file's name in the archive.

    Raises:
        ExtractionError: When the archive lacks the file.
    """
    try:
        return opened.read(name)
    except KeyError:
        raise ExtractionError(f"{relative} holds no {name}") from None


def members(opened: ZipFile) -> dict[str, ZipInfo]:
    """Return the files of an archive by their names lower-cased.

    The games read their archives regardless of case, and their makers wrote names in any.

    Args:
        opened: The archive.
    """
    return {info.filename.lower(): info for info in opened.infolist() if not info.is_dir()}
