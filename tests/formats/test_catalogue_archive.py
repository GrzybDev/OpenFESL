import warnings
from io import BytesIO
from zipfile import ZipFile

import pytest

from openfesl.formats import catalogue_archive
from openfesl.formats.catalogue_archive import CatalogueArchive, read_archive, write_archive

ARCHIVE = CatalogueArchive(
    b'{"format":1,"game":"invented","items":[]}',
    {"pt-BR": b'{"A":"b"}', "en": b'{"A":"a"}'},
    {"item1": b"RIFF\0\0\0\0WEBPinvented", "emote2": b"RIFF\0\0\0\0WEBPother"},
    {"invented_hat": b"glTFhat", "invented-skeleton": b"glTFskeleton"},
    {"invented_hair": b"RIFF\0\0\0\0WEBPhair"},
)


def test_a_catalogue_archive_reads_back_into_the_same_bytes() -> None:
    written = write_archive(ARCHIVE)

    assert read_archive(written) == ARCHIVE
    assert write_archive(read_archive(written)) == written
    with ZipFile(BytesIO(written)) as archive:
        assert archive.namelist() == [
            "catalogue.json",
            "texts/en.json",
            "texts/pt-BR.json",
            "icons/emote2.webp",
            "icons/item1.webp",
            "models/invented-skeleton.glb",
            "models/invented_hat.glb",
            "textures/invented_hair.webp",
        ]


def archive_of(*files: tuple[str, bytes]) -> bytes:
    written = BytesIO()
    # ZipFile warns of a second file of the same name, which one of these archives is for.
    with warnings.catch_warnings(), ZipFile(written, "w") as archive:
        warnings.simplefilter("ignore")
        for name, data in files:
            archive.writestr(name, data)
    return written.getvalue()


@pytest.mark.parametrize(
    ("data", "why"),
    [
        (b"invented", "no ZIP archive"),
        (archive_of(("texts/en.json", b"{}")), "no catalogue.json"),
        (archive_of(("catalogue.json", b"{}"), ("icons/../x.webp", b"")), "which no catalogue"),
        (archive_of(("catalogue.json", b"{}"), ("icons/Item1.webp", b"")), "which no catalogue"),
        (archive_of(("catalogue.json", b"{}"), ("models/hat.webp", b"")), "which no catalogue"),
        (archive_of(("catalogue.json", b"{}"), ("catalogue.json", b"{}")), "twice"),
    ],
)
def test_a_file_that_is_no_catalogue_archive_is_refused(data: bytes, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        read_archive(data)


def test_an_archive_that_unpacks_to_more_than_a_catalogue_holds_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written = write_archive(ARCHIVE)
    monkeypatch.setattr(catalogue_archive, "MAX_SIZE", len(ARCHIVE.document) + 8)
    with pytest.raises(ValueError, match="more than"):
        read_archive(written)
