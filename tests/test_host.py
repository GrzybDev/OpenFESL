import anyio
import pytest
from support import fetch

from openfesl.server.cli import main

pytestmark = pytest.mark.anyio


@pytest.fixture
def contact(monkeypatch: pytest.MonkeyPatch) -> None:
    # Set before the server starts, which reads its settings once.
    monkeypatch.setenv("CONTACT_EMAIL", "admins@example.invalid")


async def test_the_hosting_guide_follows_the_hosting_mode(contact: None, server_url: str) -> None:
    status, _, body = await fetch(server_url, "/bfbc2/host")
    page = body.decode()
    assert status == 200
    for shown in (
        "Approved servers only",
        "Frost.Game.Main_Win32_Final.exe",
        "Add the provider file",
        "Replaced",
        "serverid.dat",
        "After approval",
        'href="mailto:admins@example.invalid"',
    ):
        assert shown in page
    # This server is not the hook's default, so the guide names it in the hook's settings.
    assert f"Url={server_url.rstrip('/')}" in page

    await anyio.to_thread.run_sync(main, ["hosting", "open"])
    _, _, body = await fetch(server_url, "/pl/bfbc2/host")
    page = body.decode()
    for shown in ("Dowolny serwer", "w dwóch krokach", "dołączonego do każdego wydania serwera"):
        assert shown in page
    # Any server's own provider file does here, so the guide asks for none.
    for hidden in ("Dodaj plik operatora", "Po zatwierdzeniu"):
        assert hidden not in page
