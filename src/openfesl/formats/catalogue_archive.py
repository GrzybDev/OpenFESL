import re
from dataclasses import dataclass, field
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

DOCUMENT = "catalogue.json"
# The most an archive may unpack to, well above a whole game's catalogue with its icons and
# hero models.
MAX_SIZE = 256 * 2**20
_TEXTS = re.compile(r"texts/([a-z]{2}(?:-[A-Z]{2})?)\.json")
_ICON = re.compile(r"icons/([a-z0-9_]{1,64})\.webp")
_MODEL = re.compile(r"models/([a-z0-9_-]{1,64})\.glb")
_TEXTURE = re.compile(r"textures/([a-z0-9_-]{1,64})\.webp")
# Fixed times and system, so that the same catalogue always makes the same archive.
_TIME = (1980, 1, 1, 0, 0, 0)
_SYSTEM = 3


@dataclass(frozen=True, slots=True)
class CatalogueArchive:
    """The file in which a game's catalogue goes from a copy of the game to a server.

    It is a ZIP archive of the catalogue's document, its texts in each language, its icons and
    the models and textures that the game draws its heroes with.

    Attributes:
        document: The catalogue as JSON, in catalogue.json.
        texts: The texts as JSON objects of text id and text, each in texts/<language>.json, by
            language code such as en or pt-BR.
        icons: The icons as WebP pictures, each in icons/<name>.webp, by name.
        models: The models as binary glTF files, each in models/<name>.glb, by name.
        textures: The models' textures as WebP pictures, each in textures/<name>.webp, by name.
    """

    document: bytes
    texts: dict[str, bytes] = field(default_factory=dict)
    icons: dict[str, bytes] = field(default_factory=dict)
    models: dict[str, bytes] = field(default_factory=dict)
    textures: dict[str, bytes] = field(default_factory=dict)


def write_archive(archive: CatalogueArchive) -> bytes:
    """Return the file of a catalogue, the same bytes for the same catalogue.

    Args:
        archive: The catalogue's parts.
    """
    entries = [(DOCUMENT, archive.document, ZIP_DEFLATED)]
    entries += [
        (f"texts/{language}.json", archive.texts[language], ZIP_DEFLATED)
        for language in sorted(archive.texts)
    ]
    # WebP pictures are compressed already.
    entries += [
        (f"icons/{name}.webp", archive.icons[name], ZIP_STORED) for name in sorted(archive.icons)
    ]
    entries += [
        (f"models/{name}.glb", archive.models[name], ZIP_DEFLATED)
        for name in sorted(archive.models)
    ]
    entries += [
        (f"textures/{name}.webp", archive.textures[name], ZIP_STORED)
        for name in sorted(archive.textures)
    ]
    written = BytesIO()
    with ZipFile(written, "w") as zip_file:
        for name, data, compression in entries:
            info = ZipInfo(name, _TIME)
            info.create_system = _SYSTEM
            info.compress_type = compression
            zip_file.writestr(info, data)
    return written.getvalue()


def read_archive(data: bytes) -> CatalogueArchive:
    """Read the file of a catalogue.

    Args:
        data: The file.

    Raises:
        ValueError: When the file is no catalogue archive: no ZIP archive, one without the
            document, with files of other names or twice the same, or one that unpacks to more
            than MAX_SIZE.
    """
    try:
        with ZipFile(BytesIO(data)) as zip_file:
            members = [info for info in zip_file.infolist() if not info.is_dir()]
            names = [info.filename for info in members]
            if len(set(names)) < len(names):
                raise ValueError("the archive holds a file twice")
            if sum(info.file_size for info in members) > MAX_SIZE:
                raise ValueError("the archive unpacks to more than a catalogue can hold")
            document = None
            parts: dict[re.Pattern[str], dict[str, bytes]] = {
                pattern: {} for pattern in (_TEXTS, _ICON, _MODEL, _TEXTURE)
            }
            for info in members:
                content = zip_file.read(info)
                if info.filename == DOCUMENT:
                    document = content
                    continue
                for pattern, found in parts.items():
                    if name := pattern.fullmatch(info.filename):
                        found[name.group(1)] = content
                        break
                else:
                    raise ValueError(f"the archive holds {info.filename}, which no catalogue has")
    except BadZipFile as error:
        raise ValueError(f"the file is no ZIP archive: {error}") from None
    if document is None:
        raise ValueError(f"the archive holds no {DOCUMENT}")
    return CatalogueArchive(document, *parts.values())
