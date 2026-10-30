from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from accounts import select_value
from heroes import create_hero
from identity import IdentityProvider, sign_in
from store import ROYAL_SOLDIER, stock
from support import visible

pytestmark = pytest.mark.anyio
WARDEN = {"sub": "warden-7", "preferred_username": "invented-warden", "groups": ["fesl-moderators"]}
KEEPER = {"sub": "keeper-7", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}


async def account_of(database_url: str, address: str) -> int:
    """Return the number of the account with a login name."""
    found = await select_value(
        database_url, "SELECT id FROM account WHERE nuid = :nuid", nuid=address
    )
    assert isinstance(found, int)
    return found


@pytest.mark.usefixtures("prompt_actions")
async def test_the_panel_lists_an_accounts_heroes_and_shows_each_heroes_page(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    brass = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await create_hero(base, "ann@example.test", "Mudlark")
    ann = await account_of(database_url, "ann@example.test")
    account = f"{base}/admin/accounts/{ann}"
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        page = visible((await sign_in(client, base, next=f"/admin/accounts/{ann}")).content)
        # Heroes leave the in-game names for a list of their own, the first one the default.
        assert "In-game names 0" in page and "Heroes Battlefield Heroes 2" in page
        assert "2 of 4 used 4 free + 0 bought" in page
        assert f"Brassbuckle Royal Army Soldier · hero {brass}" in page
        assert "Default hero" in page and "0 unspent of 0 earned" in page
        assert "0 keys held by the account" in page
        today = datetime.now(UTC)
        day = f"{today.day} {today:%b %Y}"
        assert f"Created {day}" in page

        hero = visible((await client.get(f"{account}/heroes/{brass}")).content)
        assert "Faction Royal Army" in hero and "Class Soldier" in hero
        assert "Default hero Yes" in hero
        # The loadout that the game grants itself, and the bandages the site granted.
        assert (
            "Invented Bandage item 2005 Kind Abilities 10 uses left From Starting kit Active"
            in hero
        )
        assert "No portrait from the game yet" in hero
        ended = visible((await client.get(f"{account}/heroes/{brass}?items=ended")).content)
        assert "No item of the hero has ended." in ended

        polish = visible(
            (await client.get(f"{base}/pl/admin/accounts/{ann}/heroes/{brass}")).content
        )
        assert "Ekwipunek" in polish and "Zestaw startowy" in polish

        bob = await account_of(database_url, "bob@example.test")
        assert (await client.get(f"{base}/admin/accounts/{bob}/heroes/{brass}")).status_code == 404
        none = visible((await client.get(f"{base}/admin/accounts/{bob}")).content)
        assert "No hero yet." in none
