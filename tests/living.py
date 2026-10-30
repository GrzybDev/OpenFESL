"""Helpers of the tests of the living site: hot deals that change by themselves, and rewards."""

from collections.abc import Mapping
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from time import monotonic
from typing import Self
from xml.etree import ElementTree

import anyio
import pytest
from accounts import execute
from heroes import MAGMA
from support import cli, fetch

from openfesl.core.catalogue import CatalogueDocument, CatalogueFile, CatalogueItem

# The items of an invented catalogue by number, each with its name, kind and fields.
type Invented = dict[int, tuple[str, str, dict[str, str]]]

ROYAL, NATIONAL = "1", "2"


def worn(name: str, side: str, slot: str) -> tuple[str, str, dict[str, str]]:
    """Return an invented item that heroes of a faction wear in a slot, by its number."""
    return name, "appearance", {"faction": side, "slot": slot}


def catalogue_file(folder: Path, invented: Invented, texts: Mapping[str, str]) -> Path:
    """Write an invented catalogue as `openfesl catalogue extract` writes it.

    Args:
        folder: Where to write it.
        invented: Its items.
        texts: Its English texts besides the items' names, by id.
    """
    items = [
        CatalogueItem(id=number, kind=kind, fields=fields)
        for number, (_, kind, fields) in invented.items()
    ]
    english = {f"WEST_Custom_Item_{number}": name for number, (name, _, _) in invented.items()}
    document = CatalogueDocument(format=1, game="bfheroes", items=tuple(items))
    file = folder / "catalogue.zip"
    file.write_bytes(CatalogueFile(document, {"en": english | dict(texts)}, {}).write())
    return file


async def import_catalogue(
    server_url: str,
    folder: Path,
    capsys: pytest.CaptureFixture[str],
    invented: Invented,
    texts: Mapping[str, str] = {},
) -> None:
    """Import an invented catalogue as the admin does, and wait for the store to fill from it."""
    await cli(capsys, "catalogue", "import", str(catalogue_file(folder, invented, texts)))
    with anyio.fail_after(30):
        while not len(ElementTree.fromstring((await fetch(server_url, "/ofb/products", MAGMA))[2])):
            await anyio.sleep(0.05)


def freeze(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """Set the clock of the server's rewards, their pages and the items they give to a moment.

    The server runs in the tests' process. From the moment the clock runs on as a real one does,
    so that what a claim gives is later than the page that claims it.
    """
    started = monotonic()

    class Clock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            ticked = moment + timedelta(seconds=monotonic() - started)
            shown = ticked.astimezone(tz) if tz else ticked.replace(tzinfo=None)
            return cls.combine(shown.date(), shown.timetz())

    modules = ("core.rewards", "web.reward_texts", "web.reward_claims", "storage.entitlements")
    for module in modules:
        monkeypatch.setattr(f"openfesl.{module}.datetime", Clock)


async def setting(database_url: str, key: str, value: str) -> None:
    """Change a setting as the admin does."""
    await execute(
        database_url,
        "INSERT INTO setting (key, value) VALUES (:key, :value)"
        " ON CONFLICT (key) DO UPDATE SET value = :value",
        key=key,
        value=value,
    )
