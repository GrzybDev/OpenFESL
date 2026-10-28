import asyncio
from datetime import date
from pathlib import Path

import anyio
import httpx2
import pytest
from game_files import invented_copy
from identity import IdentityProvider, sign_in
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from support import cli

from openfesl.core.admin import AdminFeed
from openfesl.core.catalogue import CatalogueFile, CatalogueItem
from openfesl.core.catalogues import Catalogues
from openfesl.core.games import Game, load_games
from openfesl.core.stats import partition_of
from openfesl.storage.admin import SqlAdminLog
from openfesl.storage.catalogues import CatalogueFolder, SqlCatalogueStore

pytestmark = pytest.mark.anyio
KEEPER = {"sub": "keeper-8", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}


async def extracted(folder: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """Extract the catalogue of an invented copy of the game, returning its file."""
    copy, out = folder / "copy", folder / "catalogue.zip"
    invented_copy(copy)
    await cli(capsys, "catalogue", "extract", str(copy), "-o", str(out))
    return out


def kept_files(folder: Path) -> list[str]:
    return sorted(str(path.relative_to(folder).as_posix()) for path in folder.rglob("*.*"))


async def test_an_imported_catalogue_names_items_in_the_pages_language_and_serves_its_files(
    files: Path,
    admin_provider: IdentityProvider,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    bfheroes: Game,
) -> None:
    file = await extracted(tmp_path, capsys)

    printed = await cli(capsys, "catalogue", "import", str(file))

    assert printed == (
        "Imported the catalogue of Battlefield Heroes: 12 items, texts in 2 languages, 5 icons "
        "and 4 hero models with 2 textures\n"
    )
    [kept] = (files / "catalogues" / "bfheroes").iterdir()
    assert len(kept_files(kept / "icons")) == 5 and len(kept_files(kept / "models")) == 4
    assert kept_files(kept / "textures") == ["defaultlightwarpskinned.webp", "invented_hair.webp"]
    engine = create_async_engine(database_url)
    catalogues = Catalogues(SqlCatalogueStore(async_sessionmaker(engine)), None, load_games())
    await catalogues.load()
    polish = await catalogues.of(bfheroes, "pl")
    await engine.dispose()

    assert polish.imported and polish.contents.icons == 5
    assert (polish.contents.models, polish.contents.textures) == (4, 2)
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

    # Browsers keep an import's files for good, as a new import comes under a new address.
    base = server_url.replace("ws://", "http://").rstrip("/")
    folder = f"{base}/catalogues/bfheroes/{kept.name}"
    async with httpx2.AsyncClient() as client:
        model = await client.get(f"{folder}/models/invented_hat.glb")
        texture = await client.get(f"{folder}/textures/invented_hair.webp")
        missing = [
            await client.get(f"{base}/catalogues/bfheroes/{'0' * 16}/icons/item1.webp"),
            await client.get(f"{folder}/icons/item1.png"),
            await client.get(f"{folder}/models/invented_lost.glb"),
            await client.get(f"{base}/catalogues/bfbc2/{kept.name}/icons/item1.webp"),
        ]
    assert model.status_code == 200 and model.headers["content-type"] == "model/gltf-binary"
    assert model.content == (kept / "models" / "invented_hat.glb").read_bytes()
    assert model.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert texture.headers["content-type"] == "image/webp"
    assert [response.status_code for response in missing] == [404] * 4

    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        log = (await sign_in(client, base, next="/admin/audit")).text
    assert "Imported catalogue" in log and "Battlefield Heroes" in log
    assert "Items: 12 · languages: 2 · icons: 5 · hero models: 4 · textures: 2" in log


async def test_a_new_import_replaces_the_whole_catalogue_for_every_server_that_follows(
    catalogue_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    bfheroes: Game,
) -> None:
    first = (await extracted(tmp_path, capsys)).read_bytes()
    old = CatalogueFile.read(first)
    later = CatalogueFile(
        old.document.model_copy(update={"items": old.document.items[:2]}),
        {"en": {"WEST_Custom_Item_2": "Invented Hair"}},
        {"item1": old.icons["item1"]},
    ).write()
    files = tmp_path / "files"
    importing = Catalogues(
        SqlCatalogueStore(catalogue_sessions), CatalogueFolder(files), load_games()
    )
    following = Catalogues(SqlCatalogueStore(catalogue_sessions), None, load_games())
    feed = AdminFeed(SqlAdminLog(catalogue_sessions))
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
    assert (imported.models, imported.textures) == (0, 0)
    for catalogue in (await following.of(bfheroes, "pl"), await importing.of(bfheroes, "pl")):
        assert catalogue.name(1) is None and catalogue.name(2) == "Invented Hair"
        assert catalogue.item(3000) is None and catalogue.contents.icons == 1
        assert catalogue.contents.models == 0
    # The earlier import's hero models went with its folder.
    [kept] = (files / "catalogues" / "bfheroes").iterdir()
    assert kept_files(kept) == ["icons/item1.webp"]

    # A file of no catalogue, or of a game without one, leaves the catalogue as it is, and so
    # does an import whose files fill the disk part way, which leaves none of them.
    bad_company = CatalogueFile(old.document.model_copy(update={"game": "bfbc2"}), {}, {})
    for refused in (b"invented", bad_company.write()):
        with pytest.raises(ValueError):
            await importing.import_file(refused, admin=None)
    written: list[Path] = []
    write_bytes = Path.write_bytes

    def full_disk(path: Path, data: bytes) -> int:
        if written:
            raise OSError("No space left on device")
        written.append(path)
        return write_bytes(path, data)

    with monkeypatch.context() as patched:
        patched.setattr(Path, "write_bytes", full_disk)
        with pytest.raises(OSError, match="No space left"):
            await importing.import_file(first, admin=None)
    assert written and not written[0].exists()
    await following.load()
    assert (await following.of(bfheroes, "en")).name(2) == "Invented Hair"
    assert [folder.name for folder in (files / "catalogues" / "bfheroes").iterdir()] == [kept.name]


async def test_imports_at_once_on_two_servers_leave_the_files_of_the_one_kept(
    catalogue_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    bfheroes: Game,
) -> None:
    data = (await extracted(tmp_path, capsys)).read_bytes()
    files = tmp_path / "files"
    first, second = (
        Catalogues(SqlCatalogueStore(catalogue_sessions), CatalogueFolder(files), load_games())
        for _ in "ab"
    )

    await asyncio.gather(first.import_file(data, None), second.import_file(data, None))

    kept = await SqlCatalogueStore(catalogue_sessions).current(partition_of(bfheroes))
    assert kept is not None
    folders = [folder.name for folder in (files / "catalogues" / "bfheroes").iterdir()]
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


@pytest.mark.usefixtures("prompt_actions")
async def test_the_icon_of_a_withheld_item_is_kept_for_the_admin_panel_alone(
    files: Path, server_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = CatalogueFile.read((await extracted(tmp_path, capsys)).read_bytes())
    # One of the shirts that the game data withholds.
    shirt = CatalogueItem(id=257, kind="appearance", icon="item257")
    file = tmp_path / "withheld.zip"
    document = old.document.model_copy(update={"items": (*old.document.items, shirt)})
    icons = {**old.icons, "item257": old.icons["item1"]}
    file.write_bytes(CatalogueFile(document, old.texts, icons).write())

    await cli(capsys, "catalogue", "import", str(file))

    [kept] = (files / "catalogues" / "bfheroes").iterdir()
    base = server_url.replace("ws://", "http://").rstrip("/")
    folder = f"{base}/catalogues/bfheroes/{kept.name}/icons"
    async with httpx2.AsyncClient() as client:
        with anyio.fail_after(5):
            while (await client.get(f"{folder}/item257.webp")).status_code != 404:
                await anyio.sleep(0.05)
        hat = await client.get(f"{folder}/item1.webp")
    assert hat.status_code == 200
    assert (kept / "icons" / "item257.webp").is_file()
