import asyncio
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import httpx2
import pytest
from game_files import invented_copy
from identity import IdentityProvider, sign_in
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from support import cli

from openfesl.core.admin import AdminFeed
from openfesl.core.catalogue import CatalogueFile
from openfesl.core.catalogues import Catalogues
from openfesl.core.games import Game, load_games
from openfesl.core.stats import partition_of
from openfesl.storage.admin import SqlAdminLog
from openfesl.storage.catalogues import IconFolder, SqlCatalogueStore
from openfesl.storage.database import migrate

pytestmark = pytest.mark.anyio
KEEPER = {"sub": "keeper-8", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}


async def extracted(folder: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """Extract the catalogue of an invented copy of the game, returning its file."""
    copy, out = folder / "copy", folder / "catalogue.zip"
    invented_copy(copy)
    await cli(capsys, "catalogue", "extract", str(copy), "-o", str(out))
    return out


@pytest.fixture
async def sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    await migrate(engine)
    yield async_sessionmaker(engine)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE catalogue, admin_action CASCADE"))
    await engine.dispose()


async def test_an_imported_catalogue_names_items_in_the_pages_language_with_its_icons_kept(
    admin_provider: IdentityProvider,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    bfheroes: Game,
) -> None:
    file = await extracted(tmp_path, capsys)
    monkeypatch.setenv("FILES_PATH", str(tmp_path / "files"))

    printed = await cli(capsys, "catalogue", "import", str(file))

    assert printed == (
        "Imported the catalogue of Battlefield Heroes: 10 items, texts in 2 languages and 6 icons\n"
    )
    [kept] = (tmp_path / "files" / "catalogues" / "bfheroes").iterdir()
    assert len(list(kept.iterdir())) == 6 and (kept / "item1.webp").read_bytes()[8:12] == b"WEBP"
    engine = create_async_engine(database_url)
    catalogues = Catalogues(SqlCatalogueStore(async_sessionmaker(engine)), None, load_games())
    await catalogues.load()
    polish = await catalogues.of(bfheroes, "pl")
    await engine.dispose()

    assert polish.imported and polish.contents.icons == 6
    hat, hair, rifle, wave = polish.item(1), polish.item(2), polish.item(3000), polish.item(5000)
    assert hat and hair and rifle and wave
    assert (hat.kind, hat.slot, hat.level) == ("appearance", "hat", 1)
    assert hat.factions == {"royal"} and hat.classes == {"commando", "soldier", "gunner"}
    assert hair.slot == "hair" and hair.factions == {"national"} and hair.classes == {"soldier"}
    # A weapon is worn in no slot, and one of both factions fits either.
    assert rifle.slot is None and rifle.factions == {"royal", "national"}
    assert rifle.classes == {"gunner"}
    assert rifle.level is None
    assert wave.classes == {"commando", "soldier", "gunner"} and wave.icon == "emote5000"
    assert [item.id for item in polish.items("ability")] == [2000, 2001, 2010]
    # A mission names its one team by the team's id, or 0 for either.
    mission, any_team = polish.item(6000), polish.item(6001)
    assert mission and mission.classes == {"commando"} and mission.level == 0
    assert mission.factions == {"royal"} and any_team and any_team.factions == {"royal", "national"}
    # Names in the page's language, in English where it has none, and a later level of an
    # ability by its first one's; texts are found whatever their case and stray spaces.
    assert polish.name(1) == "Wymyślony kapelusz"
    assert polish.name(3000) == "Practice Rifle"
    assert polish.name(2001) == "Wymyślony zryw"
    assert polish.name(5000) == "Wymyślone machnięcie"
    assert polish.name(2) is None and polish.name(4) is None
    assert polish.text("auto_rules_mission_6000") == "Wymyślona codzienna"
    english = await catalogues.of(bfheroes, "en")
    assert english.name(1) == "Invented Hat"
    # The day of the year picks the daily mission, the last slot in a leap year's last day.
    assert polish.daily_mission(date(2026, 1, 1)) == 6000
    assert polish.daily_mission(date(2026, 1, 2)) == 6001
    assert polish.daily_mission(date(2028, 12, 31)) == 6001
    assert (polish.xp_to_next(1), polish.xp_to_next(29)) == (100, 2_900)
    assert polish.xp_to_next(30) is None and polish.xp_to_next(0) is None

    admin_provider.user = KEEPER
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        log = (await sign_in(client, base, next="/admin/audit")).text
    assert "Imported catalogue" in log and "Battlefield Heroes" in log
    assert "Items: 10 · languages: 2 · icons: 6" in log


async def test_a_new_import_replaces_the_whole_catalogue_for_every_server_that_follows(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    bfheroes: Game,
) -> None:
    first = (await extracted(tmp_path, capsys)).read_bytes()
    old = CatalogueFile.read(first)
    later = CatalogueFile(
        old.document.model_copy(update={"items": old.document.items[:2]}),
        {"en": {"WEST_Custom_Item_2": "Invented Hair"}},
        {"item1": old.icons["item1"]},
    ).write()
    icons = tmp_path / "files"
    importing = Catalogues(SqlCatalogueStore(sessions), IconFolder(icons), load_games())
    following = Catalogues(SqlCatalogueStore(sessions), None, load_games())
    feed = AdminFeed(SqlAdminLog(sessions))
    following.follow(feed)
    await feed.start()
    await following.load()
    assert not (await following.of(bfheroes, "pl")).imported

    await importing.import_file(first, admin=None)
    await feed.apply()
    assert (await following.of(bfheroes, "pl")).name(1) == "Wymyślony kapelusz"
    imported = await importing.import_file(later, admin=None)
    await feed.apply()

    assert (imported.items, imported.languages, imported.icons) == (2, 1, 1)
    for catalogue in (await following.of(bfheroes, "pl"), await importing.of(bfheroes, "pl")):
        assert catalogue.name(1) is None and catalogue.name(2) == "Invented Hair"
        assert catalogue.item(3000) is None and catalogue.contents.icons == 1
    [kept] = (icons / "catalogues" / "bfheroes").iterdir()
    assert [icon.name for icon in kept.iterdir()] == ["item1.webp"]

    # A file of no catalogue, or of a game without one, leaves the catalogue as it is.
    bad_company = CatalogueFile(old.document.model_copy(update={"game": "bfbc2"}), {}, {})
    for refused in (b"invented", bad_company.write()):
        with pytest.raises(ValueError):
            await importing.import_file(refused, admin=None)
    await following.load()
    assert (await following.of(bfheroes, "en")).name(2) == "Invented Hair"
    assert [folder.name for folder in (icons / "catalogues" / "bfheroes").iterdir()] == [kept.name]


async def test_imports_at_once_on_two_servers_leave_the_icons_of_the_one_kept(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    bfheroes: Game,
) -> None:
    data = (await extracted(tmp_path, capsys)).read_bytes()
    icons = tmp_path / "files"
    first, second = (
        Catalogues(SqlCatalogueStore(sessions), IconFolder(icons), load_games()) for _ in "ab"
    )

    await asyncio.gather(first.import_file(data, None), second.import_file(data, None))

    kept = await SqlCatalogueStore(sessions).current(partition_of(bfheroes))
    assert kept is not None
    folders = [folder.name for folder in (icons / "catalogues" / "bfheroes").iterdir()]
    assert folders == [kept.revision]


async def test_a_file_that_holds_no_catalogue_is_refused_on_the_command_line(
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    unreadable = tmp_path / "catalogue.zip"
    unreadable.write_bytes(b"invented")

    with pytest.raises(SystemExit, match="holds no catalogue that this server imports"):
        await cli(capsys, "catalogue", "import", str(unreadable))
    with pytest.raises(SystemExit, match="No such file"):
        await cli(capsys, "catalogue", "import", str(tmp_path / "missing.zip"))
