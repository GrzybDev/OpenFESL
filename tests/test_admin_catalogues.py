import html
import re
from pathlib import Path

import anyio
import httpx2
import pytest
from identity import IdentityProvider, sign_in
from support import cli
from test_catalogue_import import extracted

pytestmark = pytest.mark.anyio
KEEPER = {"sub": "keeper-9", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}
WARDEN = {"sub": "warden-9", "preferred_username": "invented-warden", "groups": ["fesl-moderators"]}
PAGE = "/admin/catalogues"


def facts(page: str, label: str) -> list[str]:
    """Return the values of a page's facts with a label, as their text reads."""
    found = re.findall(rf"<dt>{label}</dt>\s*<dd[^>]*>(.*?)</dd>", page, re.DOTALL)
    return [" ".join(re.sub(r"<[^>]+>", "", value).split()) for value in found]


@pytest.mark.usefixtures("prompt_actions")
async def test_admins_and_moderators_see_what_each_games_catalogue_holds(
    files: Path,
    admin_provider: IdentityProvider,
    server_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        admin_provider.user = KEEPER
        before = html.unescape((await sign_in(client, base, next=PAGE)).text)
        # The menu offers the page, as a served game takes a catalogue; a game without catalogue
        # rules is listed as taking none.
        assert f'href="{PAGE}"' in before and 'aria-current="page"' in before
        assert "No catalogue imported yet." in before and "Not imported" in before
        assert "Battlefield: Bad Company 2" in before and "Takes no catalogue" in before

        await cli(capsys, "catalogue", "import", str(await extracted(tmp_path, capsys)))
        with anyio.fail_after(5):
            while "Not imported" in (imported := (await client.get(base + PAGE)).text):
                await anyio.sleep(0.05)
        imported = html.unescape(imported)
        log = (await client.get(f"{base}/admin/audit")).text

    # The import came from the command line, which the admin's log names as no admin.
    assert facts(imported, "Imported")[0].endswith(" UTC · The command line")
    assert facts(imported, "Items") == ["12"]
    assert facts(imported, "Languages") == ["2"] and "English and Polish" in imported
    assert facts(imported, "Icons") == ["5 kept"]
    assert facts(imported, "Hero models") == ["4 kept · 2 textures"]
    assert facts(imported, "Daily missions") == ["366 days"]
    assert facts(imported, "XP per level") == ["30 levels"]
    # Items by kind, most first.
    kinds = [(kind, facts(imported, kind)) for kind in ("Missions", "Abilities", "Appearance")]
    assert kinds == [("Missions", ["4"]), ("Abilities", ["3"]), ("Appearance", ["2"])]
    order = [imported.index(f"<dt>{kind}</dt>") for kind in ("Missions", "Abilities", "Weapons")]
    assert order == sorted(order)
    # The audit log's row of the import opens the game's block.
    assert f'href="{PAGE}#game-bfheroes"' in log

    async with httpx2.AsyncClient() as client:
        admin_provider.user = WARDEN
        seen = html.unescape((await sign_in(client, base, next=PAGE)).text)
    assert facts(seen, "Items") == ["12"]
